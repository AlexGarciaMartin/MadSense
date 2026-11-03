using System.Collections.Concurrent;
using System.Diagnostics;
using System.Net.Sockets;
using System.Runtime.InteropServices;
using System.Text.Json;

namespace MadSense.Platform;

public sealed record LinuxBrokerPreflight(bool Available, string Detail, bool? LeaseActive = null, bool? RecoveryPending = null)
{
    // Older helpers do not provide these fields. Availability alone is not an
    // acknowledgement that an earlier lease has finished restoring the device.
    public bool CleanupConfirmed => LeaseActive == false && RecoveryPending == false;
}

/// <summary>Unprivileged client for the installed, root-owned local broker.</summary>
public sealed class LinuxBrokerClient : IAsyncDisposable
{
    public const string SocketPath = "/run/madsense/broker.sock";
    internal const int MaximumLineBytes = 16384;
    internal const int MaximumReportBytes = 4096;
    readonly Stream? stream;
    readonly string? unavailableReason;
    readonly CancellationTokenSource lifetime = new();
    readonly SemaphoreSlim writes = new(1, 1);
    readonly SemaphoreSlim requestSlots = new(16, 16);
    readonly ConcurrentDictionary<long, TaskCompletionSource<JsonElement>> pending = new();
    readonly Task reader;
    internal ProxyTiming HidRpcTiming { get; } = new();
    readonly object inputGate = new();
    readonly Queue<(byte[] Bytes, long Timestamp, long BrokerReadNs)> startupInput = new();
    LinuxPhysicalSession? session;
    string? isolationFailureReason, terminationReason;
    long nextId;
    int disposed, acquired, isolationLost;

    LinuxBrokerClient(string reason) { unavailableReason = reason; reader = Task.CompletedTask; }
    internal LinuxBrokerClient(Stream stream)
    {
        this.stream = stream;
        reader = Task.Run(ReadMessagesAsync);
    }

    public static async Task<LinuxBrokerClient> ConnectAsync(CancellationToken ct = default)
    {
        if (!OperatingSystem.IsLinux()) return new("The device broker is available only on Linux.");
        Socket? socket = new(AddressFamily.Unix, SocketType.Stream, ProtocolType.Unspecified);
        try
        {
            using var timeout = CancellationTokenSource.CreateLinkedTokenSource(ct);
            timeout.CancelAfter(TimeSpan.FromSeconds(3));
            await socket.ConnectAsync(new UnixDomainSocketEndPoint(SocketPath), timeout.Token).ConfigureAwait(false);
            uint length = 12;
            if (getsockopt(socket.Handle.ToInt32(), 1, 17, out PeerCredentials peer, ref length) != 0 ||
                length != 12 || peer.Uid != 0 || peer.Pid <= 0)
                throw new IOException("The device broker peer is not a verified root-owned service.");
            var client = new LinuxBrokerClient(new NetworkStream(socket, ownsSocket: true));
            socket = null;
            return client;
        }
        catch (OperationCanceledException) when (ct.IsCancellationRequested) { throw; }
        catch (Exception error) when (error is SocketException or IOException or OperationCanceledException)
        {
            return new($"Device broker unavailable. Install and start the MadSense system helper. {error.Message}");
        }
        finally { socket?.Dispose(); }
    }

    public async Task<LinuxBrokerPreflight> PreflightAsync(CancellationToken ct = default)
    {
        if (unavailableReason is not null) return new(false, unavailableReason);
        try
        {
            JsonElement result = await RequestAsync("preflight", null, ct).ConfigureAwait(false);
            return new(result.GetProperty("available").GetBoolean(), result.GetProperty("detail").GetString() ?? "",
                result.TryGetProperty("leaseActive", out var active) ? active.GetBoolean() : null,
                result.TryGetProperty("recoveryPending", out var recovery) ? recovery.GetBoolean() : null);
        }
        catch (OperationCanceledException) when (ct.IsCancellationRequested) { throw; }
        catch (Exception error) when (error is IOException or InvalidOperationException or JsonException)
        { return new(false, error.Message); }
    }

    public async Task<LinuxPhysicalSession> AcquirePhysicalAsync(string path, CancellationToken ct = default)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(path);
        if (Interlocked.Exchange(ref acquired, 1) != 0)
            throw new InvalidOperationException("Use a fresh broker connection for each physical session.");
        try
        {
            JsonElement result = await RequestAsync("acquire", new() { ["path"] = path }, ct,
                TimeSpan.FromMinutes(2)).ConfigureAwait(false);
            if (!result.GetProperty("isolationVerified").GetBoolean())
                throw new IOException("The device broker did not verify physical isolation.");
            var created = new LinuxPhysicalSession(this,
                result.GetProperty("path").GetString() ?? throw new IOException("Missing device identity."),
                result.GetProperty("vendorId").GetUInt16(), result.GetProperty("productId").GetUInt16(),
                result.GetProperty("productName").GetString() ?? "DualSense", ct);
            lock (inputGate)
            {
                Volatile.Write(ref session, created);
                if (!IsConnected || Volatile.Read(ref isolationLost) != 0)
                    created.MarkLost(FailureReason ?? "The broker lost isolation while acquiring the controller.");
                while (startupInput.TryDequeue(out var input)) created.AcceptInput(input.Bytes, input.Timestamp, input.BrokerReadNs);
            }
            if (!created.IsolationVerified) throw new IOException(created.IsolationLostReason ?? "Physical isolation ended during acquisition.");
            return created;
        }
        catch
        {
            // A cancelled/partial acquisition must release broker-owned resources.
            await DisposeAsync().ConfigureAwait(false);
            throw;
        }
    }

    internal bool IsConnected => stream is not null && Volatile.Read(ref disposed) == 0;
    string? FailureReason => Volatile.Read(ref session)?.IsolationLostReason ??
        Volatile.Read(ref isolationFailureReason) ?? Volatile.Read(ref terminationReason);

    internal async Task<JsonElement> RequestAsync(string operation, Dictionary<string, object?>? arguments = null,
        CancellationToken ct = default, TimeSpan? deadline = null)
    {
        if (!IsConnected) throw new IOException(FailureReason ?? unavailableReason ?? "The device broker connection is closed.");
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(ct, lifetime.Token);
        timeout.CancelAfter(deadline ?? TimeSpan.FromSeconds(5));
        bool slot = false;
        long started = Stopwatch.GetTimestamp();
        long id = Interlocked.Increment(ref nextId);
        var completion = new TaskCompletionSource<JsonElement>(TaskCreationOptions.RunContinuationsAsynchronously);
        try
        {
            await requestSlots.WaitAsync(timeout.Token).ConfigureAwait(false);
            slot = true;
            if (!pending.TryAdd(id, completion)) throw new IOException("Duplicate broker request identifier.");
            var request = arguments is null ? new Dictionary<string, object?>() : new(arguments);
            request.Add("id", id); request.Add("op", operation);
            byte[] bytes = JsonSerializer.SerializeToUtf8Bytes(request);
            if (bytes.Length > MaximumLineBytes) throw new IOException("Broker request exceeds the protocol bound.");
            await writes.WaitAsync(timeout.Token).ConfigureAwait(false);
            try
            {
                await stream!.WriteAsync(bytes, timeout.Token).ConfigureAwait(false);
                await stream.WriteAsync(new byte[] { (byte)'\n' }, timeout.Token).ConfigureAwait(false);
                await stream.FlushAsync(timeout.Token).ConfigureAwait(false);
            }
            finally { writes.Release(); }
            return await completion.Task.WaitAsync(timeout.Token).ConfigureAwait(false);
        }
        catch (OperationCanceledException)
        {
            // A timed-out attach/acquire might still complete in the helper.
            // Closing the lease makes its cleanup authoritative instead of guessing.
            if (!lifetime.IsCancellationRequested)
                Terminate(ct.IsCancellationRequested ? "The device broker request was cancelled." : "The device broker request timed out.");
            if (ct.IsCancellationRequested) throw;
            throw new IOException(FailureReason ?? "The device broker session ended.");
        }
        catch (Exception error) when (error is SocketException or ObjectDisposedException)
        {
            Terminate(error.Message);
            throw new IOException("The device broker connection failed.", error);
        }
        catch (IOException error) when (error is not BrokerOperationException)
        {
            Terminate(error.Message);
            throw;
        }
        finally
        {
            if (operation is "write" or "get_feature" or "send_feature")
                HidRpcTiming.Add(Stopwatch.GetTimestamp() - started);
            pending.TryRemove(id, out _);
            if (slot) requestSlots.Release();
        }
    }

    async Task ReadMessagesAsync()
    {
        byte[] incoming = new byte[4096], line = new byte[MaximumLineBytes];
        int used = 0;
        try
        {
            while (!lifetime.IsCancellationRequested)
            {
                int count = await stream!.ReadAsync(incoming, lifetime.Token).ConfigureAwait(false);
                if (count == 0) throw new IOException("The device broker closed its session.");
                for (int i = 0; i < count; ++i)
                {
                    if (incoming[i] == '\n')
                    {
                        if (used == 0) throw new IOException("Empty broker protocol message.");
                        using JsonDocument document = JsonDocument.Parse(line.AsMemory(0, used));
                        Dispatch(document.RootElement);
                        used = 0;
                    }
                    else
                    {
                        if (used == line.Length) throw new IOException("Broker message exceeds the protocol bound.");
                        line[used++] = incoming[i];
                    }
                }
            }
        }
        catch (OperationCanceledException) when (lifetime.IsCancellationRequested) { }
        catch (ObjectDisposedException) when (lifetime.IsCancellationRequested) { }
        catch (Exception error) when (error is IOException or SocketException or JsonException or
                                     InvalidOperationException or FormatException or KeyNotFoundException or OverflowException)
        { Terminate(error.Message); }
    }

    void Dispatch(JsonElement message)
    {
        if (message.TryGetProperty("event", out JsonElement eventName))
        {
            switch (eventName.GetString())
            {
                case "input":
                    long received = Stopwatch.GetTimestamp();
                    byte[] report = DecodeReport(message.GetProperty("data"));
                    long brokerReadNs = message.TryGetProperty("brokerReadMonotonicNs", out var stamp) ? stamp.GetInt64() : 0;
                    if (brokerReadNs < 0) throw new IOException("Invalid broker input timestamp.");
                    lock (inputGate)
                    {
                        // A response and the first input can arrive in the same read.
                        // The acquisition continuation has not necessarily run yet.
                        if (session is not null) session.AcceptInput(report, received, brokerReadNs);
                        else if (Volatile.Read(ref acquired) != 0 && startupInput.Count < 8)
                            startupInput.Enqueue((report, received, brokerReadNs));
                        else throw new IOException("Unexpected or overflowing input before physical acquisition completed.");
                    }
                    return;
                case "isolation_lost":
                    string reason = "Broker isolation lost: " + (message.GetProperty("reason").GetString() ?? "Physical isolation ended.");
                    Interlocked.CompareExchange(ref isolationFailureReason, reason, null);
                    Interlocked.Exchange(ref isolationLost, 1);
                    Volatile.Read(ref session)?.MarkLost(Volatile.Read(ref isolationFailureReason)!);
                    return;
                default: throw new IOException("Unsupported device broker event.");
            }
        }
        long id = message.GetProperty("id").GetInt64();
        bool ok = message.GetProperty("ok").GetBoolean();
        JsonElement result = default;
        Exception? failure = null;
        if (ok) result = message.GetProperty("result").Clone();
        else
        {
            JsonElement error = message.GetProperty("error");
            string? code = error.GetProperty("code").GetString();
            string detail = $"{code}: {error.GetProperty("message").GetString()}";
            failure = code is "not_authorized" or "authorization_denied" ?
                new UnauthorizedAccessException(detail) : new BrokerOperationException(detail);
        }
        // Keep the waiter registered until the complete reply has been validated,
        // so a malformed reply also wakes it when the connection terminates.
        if (!pending.TryRemove(id, out var completion)) throw new IOException("Unknown broker response identifier.");
        if (ok) completion.TrySetResult(result);
        else completion.TrySetException(failure!);
    }

    internal static byte[] DecodeReport(JsonElement data)
    {
        string encoded = data.GetString() ?? throw new IOException("Missing report payload.");
        if (encoded.Length > ((MaximumReportBytes + 2) / 3) * 4) throw new IOException("Report exceeds the protocol bound.");
        byte[] report = Convert.FromBase64String(encoded);
        if (report.Length is < 1 or > MaximumReportBytes) throw new IOException("Invalid report length.");
        return report;
    }

    void Terminate(string reason)
    {
        if (Interlocked.Exchange(ref disposed, 1) != 0) return;
        Volatile.Read(ref session)?.MarkLost(reason);
        reason = FailureReason ?? reason;
        Interlocked.CompareExchange(ref terminationReason, reason, null);
        lifetime.Cancel();
        stream?.Dispose();
        foreach (var item in pending) item.Value.TrySetException(new IOException(reason));
        pending.Clear();
    }

    public async ValueTask DisposeAsync()
    {
        if (Interlocked.Exchange(ref disposed, 1) == 0)
        {
            string reason = FailureReason ?? "The broker session was disposed.";
            Interlocked.CompareExchange(ref terminationReason, reason, null);
            Volatile.Read(ref session)?.MarkClosed();
            lifetime.Cancel();
            stream?.Dispose();
            foreach (var item in pending) item.Value.TrySetException(new IOException(reason));
            pending.Clear();
        }
        await reader.ConfigureAwait(false);
    }

    [StructLayout(LayoutKind.Sequential)]
    struct PeerCredentials { public int Pid; public uint Uid, Gid; }
    sealed class BrokerOperationException(string message) : IOException(message);
    [DllImport("libc", SetLastError = true)]
    static extern int getsockopt(int socket, int level, int option, out PeerCredentials value, ref uint length);
}
