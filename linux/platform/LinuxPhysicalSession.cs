using System.Text.Json;
using System.Threading.Channels;
using System.Diagnostics;
using DualSenseClient.Hid;
using DualSenseClient.Emulation.Windows;

namespace MadSense.Platform;

public sealed class LinuxPhysicalSession : IAsyncDisposable
{
    readonly LinuxBrokerClient broker;
    readonly BrokerHidDevice device;
    readonly LinuxAttachStrategy attachStrategy;
    readonly object isolationGate = new();
    string? isolationLostReason;
    int verified = 1, disposed, cleanupConfirmed;
    public IHidDevice Device => device;
    public IUsbIpAttachStrategy AttachStrategy => attachStrategy;
    public bool IsolationVerified => Volatile.Read(ref verified) != 0 && broker.IsConnected;
    public bool CleanupConfirmed => Volatile.Read(ref cleanupConfirmed) != 0;
    /// <summary>The first isolation failure; normal ordered closure leaves this null.</summary>
    public string? IsolationLostReason => Volatile.Read(ref isolationLostReason);
    public LinuxProxyMetrics Metrics => device.Metrics;
    public event Action? IsolationLost;

    internal LinuxPhysicalSession(LinuxBrokerClient broker, string path, ushort vendor, ushort product, string name,
        CancellationToken acquisitionCancellation = default)
    {
        this.broker = broker;
        device = new BrokerHidDevice(this, broker, path, vendor, product, name);
        attachStrategy = new LinuxAttachStrategy(this, broker, acquisitionCancellation);
    }

    /// <summary>Cancels only an in-flight import; the broker then owns ordered lease cleanup.</summary>
    public void CancelPendingAttach() => attachStrategy.CancelPendingAttach();

    internal void AcceptInput(byte[] report, long timestamp, long brokerReadNs)
    { if (IsolationVerified) device.AcceptInput(report, timestamp, brokerReadNs); }
    internal void MarkLost(string reason)
    {
        lock (isolationGate)
        {
            if (verified == 0) return;
            // Publish the cause before readiness changes or pending operations
            // wake. Later EOF/cancellation must not replace this first failure.
            Volatile.Write(ref isolationLostReason, reason);
            Volatile.Write(ref verified, 0);
        }
        device.EndInput(new IOException(reason));
        // A bridge shutdown may synchronously call back into the broker.
        // Never invoke its callback on the sole response-reading task.
        Action? callback = IsolationLost;
        if (callback is not null) ThreadPool.QueueUserWorkItem(_ => { try { callback(); } catch { } });
    }
    internal void MarkClosed()
    {
        lock (isolationGate) Volatile.Write(ref verified, 0);
        device.EndInput(null);
    }

    public async ValueTask DisposeAsync()
    {
        if (Interlocked.Exchange(ref disposed, 1) != 0) return;
        CancelPendingAttach();
        MarkClosed();
        device.Dispose();
        try
        {
            if (broker.IsConnected)
            {
                await broker.RequestAsync("release").ConfigureAwait(false);
                Volatile.Write(ref cleanupConfirmed, 1);
            }
        }
        finally { await broker.DisposeAsync().ConfigureAwait(false); }
    }

    sealed class BrokerHidDevice : IHidDevice
    {
        readonly LinuxPhysicalSession session;
        readonly LinuxBrokerClient broker;
        readonly string name;
        readonly Channel<InputReport> input = Channel.CreateBounded<InputReport>(new BoundedChannelOptions(8)
        { SingleWriter = true, SingleReader = false, FullMode = BoundedChannelFullMode.Wait });
        readonly ProxyTiming queueAge = new();
        readonly object readerStartGate = new();
        readonly object inputAvailable = new();
        long received, read, initializationDiscarded, overflows, lastReceived, lastDelivered, lastBrokerReadNs;
        int reading;
        int queued;
        int disposed;
        sealed record InputReport(byte[] Bytes, long ReceivedTimestamp);
        public ushort VendorId { get; }
        public ushort ProductId { get; }
        public string DevicePath { get; }
        public bool IsConnected => Volatile.Read(ref disposed) == 0 && session.IsolationVerified;
        internal BrokerHidDevice(LinuxPhysicalSession session, LinuxBrokerClient broker, string path,
            ushort vendor, ushort product, string name)
        {
            this.session = session; this.broker = broker; this.name = name;
            DevicePath = path; VendorId = vendor; ProductId = product;
        }
        internal void AcceptInput(byte[] report, long timestamp, long brokerReadNs)
        {
            Interlocked.Increment(ref received); Interlocked.Exchange(ref lastReceived, timestamp);
            Interlocked.Exchange(ref lastBrokerReadNs, brokerReadNs);
            var inputReport = new InputReport(report, timestamp);
            if (TryEnqueue(inputReport)) return;
            lock (readerStartGate)
            {
                // DualSenseDevice reads firmware/pairing features before starting
                // its input thread. Retain recent setup input and account for
                // every superseded report; an active reader never takes this path.
                if (reading == 0 && input.Reader.TryRead(out _))
                {
                    Interlocked.Decrement(ref queued);
                    Interlocked.Increment(ref initializationDiscarded);
                    if (TryEnqueue(inputReport)) return;
                }
            }
            Interlocked.Increment(ref overflows);
            session.MarkLost("The physical input proxy exceeded its eight-report queue; no input loss is hidden.");
        }
        bool TryEnqueue(InputReport report)
        {
            Interlocked.Increment(ref queued);
            if (input.Writer.TryWrite(report))
            {
                lock (inputAvailable) Monitor.PulseAll(inputAvailable);
                return true;
            }
            Interlocked.Decrement(ref queued);
            return false;
        }
        void BeginReading()
        {
            if (Volatile.Read(ref reading) == 0)
                lock (readerStartGate) Volatile.Write(ref reading, 1);
        }
        internal LinuxProxyMetrics Metrics
        {
            get
            {
                double oldest = input.Reader.TryPeek(out InputReport? value) ?
                    (Stopwatch.GetTimestamp() - value.ReceivedTimestamp) * 1000.0 / Stopwatch.Frequency : 0;
                return new(Interlocked.Read(ref received), Interlocked.Read(ref read), Interlocked.Read(ref initializationDiscarded), Interlocked.Read(ref overflows),
                    Volatile.Read(ref queued), oldest, Interlocked.Read(ref lastReceived), Interlocked.Read(ref lastDelivered),
                    Stopwatch.Frequency, Interlocked.Read(ref lastBrokerReadNs), queueAge.Snapshot(), broker.HidRpcTiming.Snapshot());
            }
        }
        internal void EndInput(Exception? error)
        {
            input.Writer.TryComplete(error);
            lock (inputAvailable) Monitor.PulseAll(inputAvailable);
        }
        void EnsureConnected()
        {
            ObjectDisposedException.ThrowIf(Volatile.Read(ref disposed) != 0, this);
            if (!session.IsolationVerified) throw new HidException("Physical controller isolation has ended.");
        }
        static void ValidateBuffer(byte[] buffer, int offset, int count)
        {
            ArgumentNullException.ThrowIfNull(buffer);
            ArgumentOutOfRangeException.ThrowIfNegative(offset);
            ArgumentOutOfRangeException.ThrowIfNegative(count);
            if (offset > buffer.Length - count) throw new ArgumentException("Invalid report buffer range.");
        }
        public int Read(byte[] buffer, int offset, int count, int timeoutMs)
        {
            ValidateBuffer(buffer, offset, count); EnsureConnected();
            if (timeoutMs < -1) throw new ArgumentOutOfRangeException(nameof(timeoutMs));
            if (count == 0) return 0;
            BeginReading();
            long deadline = timeoutMs < 0 ? 0 : Environment.TickCount64 + timeoutMs;
            InputReport report;
            lock (inputAvailable)
            {
                while (!input.Reader.TryRead(out report!))
                {
                    if (!IsConnected) throw new HidException("The physical report stream ended.");
                    int remaining = timeoutMs < 0 ? Timeout.Infinite : (int)Math.Max(0, deadline - Environment.TickCount64);
                    if (remaining == 0 || !Monitor.Wait(inputAvailable, remaining)) return 0;
                }
            }
            return Copy(report, buffer, offset, count);
        }
        public async Task<int> ReadAsync(byte[] buffer, int offset, int count, CancellationToken ct)
        {
            ValidateBuffer(buffer, offset, count); EnsureConnected();
            if (count == 0) return 0;
            BeginReading();
            InputReport report;
            try { report = await input.Reader.ReadAsync(ct).ConfigureAwait(false); }
            catch (ChannelClosedException) { throw new HidException("The physical report stream ended."); }
            return Copy(report, buffer, offset, count);
        }
        int Copy(InputReport report, byte[] buffer, int offset, int count)
        {
            Interlocked.Decrement(ref queued);
            EnsureConnected();
            long delivered = Stopwatch.GetTimestamp();
            queueAge.Add(delivered - report.ReceivedTimestamp);
            Interlocked.Increment(ref read); Interlocked.Exchange(ref lastDelivered, delivered);
            int length = Math.Min(report.Bytes.Length, count);
            report.Bytes.AsSpan(0, length).CopyTo(buffer.AsSpan(offset, length));
            return length;
        }
        static string Encode(byte[] buffer, int offset, int count)
        {
            ValidateBuffer(buffer, offset, count);
            if (count is < 1 or > LinuxBrokerClient.MaximumReportBytes) throw new ArgumentOutOfRangeException(nameof(count));
            return Convert.ToBase64String(buffer, offset, count);
        }
        public int Write(byte[] buffer, int offset, int count)
        {
            EnsureConnected();
            string encoded = Encode(buffer, offset, count);
            return HidOperation(() =>
            {
                JsonElement result = broker.Request("write", new() { ["data"] = encoded });
                int written = result.GetProperty("written").GetInt32();
                if (written != count) throw new IOException("The broker did not write the complete HID report.");
                return written;
            }, fatal: true);
        }
        public byte[] GetFeatureReport(byte reportId, int bufferSize = 64)
        {
            EnsureConnected();
            if (bufferSize is < 1 or > LinuxBrokerClient.MaximumReportBytes) throw new ArgumentOutOfRangeException(nameof(bufferSize));
            return HidOperation(() =>
            {
                JsonElement result = broker.Request("get_feature", new() { ["reportId"] = reportId, ["size"] = bufferSize });
                byte[] report = LinuxBrokerClient.DecodeReport(result.GetProperty("data"));
                if (report.Length > bufferSize || report[0] != reportId) throw new IOException("Invalid feature report identity or size.");
                return report;
            });
        }
        public void SendFeatureReport(byte[] buffer, int offset, int count)
        {
            EnsureConnected();
            string encoded = Encode(buffer, offset, count);
            HidOperation(() => broker.Request("send_feature", new() { ["data"] = encoded }), fatal: true);
        }
        T HidOperation<T>(Func<T> operation, bool fatal = false)
        {
            try { return operation(); }
            catch (Exception error) when (error is IOException or JsonException or InvalidOperationException or
                                          KeyNotFoundException or FormatException or OverflowException)
            {
                if (fatal) session.MarkLost(error.Message);
                // FeatureReader and the shared output writer deliberately handle
                // HidException. Preserve that contract at the proxy boundary.
                throw new HidException(error.Message);
            }
        }
        public string GetProductName() => name;
        public void Dispose()
        {
            if (Interlocked.Exchange(ref disposed, 1) == 0) EndInput(null);
        }
    }
}
