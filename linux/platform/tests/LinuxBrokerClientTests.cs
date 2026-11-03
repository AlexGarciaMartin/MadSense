using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Text.Json;
using DualSenseClient.Hid;
using MadSense.Platform;

namespace MadSense.Linux.Platform.Tests;

public sealed class LinuxBrokerClientTests
{
    sealed class Peer : IAsyncDisposable
    {
        public const string LeaseId = "0123456789abcdef0123456789abcdef";
        readonly TcpListener listener;
        readonly TcpClient server;
        readonly StreamReader input;
        readonly StreamWriter output;
        long inputSequence;
        public List<long> Acknowledgements { get; } = [];
        public LinuxBrokerClient Client { get; }
        Peer(TcpListener listener, TcpClient server, LinuxBrokerClient client)
        {
            this.listener = listener; this.server = server; Client = client;
            input = new(server.GetStream(), Encoding.UTF8, leaveOpen: true);
            output = new(server.GetStream(), new UTF8Encoding(false), leaveOpen: true) { AutoFlush = true };
        }
        public static async Task<Peer> OpenAsync(Func<Stream, Stream>? decorate = null)
        {
            var listener = new TcpListener(IPAddress.Loopback, 0); listener.Start();
            var client = new TcpClient();
            await client.ConnectAsync((IPEndPoint)listener.LocalEndpoint);
            TcpClient server = await listener.AcceptTcpClientAsync();
            Stream stream = client.GetStream();
            return new(listener, server, new LinuxBrokerClient(decorate?.Invoke(stream) ?? stream));
        }
        public async Task<JsonElement> RequestAsync()
        {
            while (true)
            {
                JsonElement message = await ReadMessageAsync();
                if (message.GetProperty("op").GetString() != "input_ack") return message;
                RecordAcknowledgement(message);
            }
        }
        async Task<JsonElement> ReadMessageAsync()
        {
            string? line = await input.ReadLineAsync().WaitAsync(TimeSpan.FromSeconds(3));
            Assert.That(line, Is.Not.Null);
            return JsonSerializer.Deserialize<JsonElement>(line!);
        }
        void RecordAcknowledgement(JsonElement message)
        {
            Assert.That(message.GetProperty("id").GetInt64(), Is.Zero);
            Assert.That(message.GetProperty("leaseId").GetString(), Is.EqualTo(LeaseId));
            long through = message.GetProperty("through").GetInt64();
            Assert.That(through, Is.GreaterThan(Acknowledgements.LastOrDefault()));
            Acknowledgements.Add(through);
        }
        public async Task<long> AcknowledgementAsync()
        {
            JsonElement message = await ReadMessageAsync();
            Assert.That(message.GetProperty("op").GetString(), Is.EqualTo("input_ack"));
            RecordAcknowledgement(message);
            return Acknowledgements[^1];
        }
        public async Task<JsonElement> RequestAcquireAsync()
        {
            JsonElement request = await RequestAsync();
            Assert.That(request.GetProperty("op").GetString(), Is.EqualTo("preflight"));
            await ReplyAsync(request, new { available = true, detail = "ready", inputFlow = new { version = 1, window = 8 } });
            request = await RequestAsync();
            Assert.That(request.GetProperty("op").GetString(), Is.EqualTo("acquire"));
            Assert.That(request.GetProperty("inputFlowVersion").GetInt32(), Is.EqualTo(1));
            return request;
        }
        public Task ReplyAsync(JsonElement request, object result) => SendAsync(new
        { id = request.GetProperty("id").GetInt64(), ok = true, result });
        public Task SendAsync(object message)
        {
            var fields = JsonSerializer.Deserialize<Dictionary<string, JsonElement>>(JsonSerializer.Serialize(message))!;
            if (fields.TryGetValue("event", out var kind) && kind.GetString() == "input")
            {
                fields["leaseId"] = JsonSerializer.SerializeToElement(LeaseId);
                fields["sequence"] = JsonSerializer.SerializeToElement(++inputSequence);
                fields["creditBlockedCount"] = JsonSerializer.SerializeToElement(0L);
                fields["creditBlockedNs"] = JsonSerializer.SerializeToElement(0L);
                fields["creditMaxBlockedNs"] = JsonSerializer.SerializeToElement(0L);
            }
            return output.WriteLineAsync(JsonSerializer.Serialize(fields));
        }
        public Task SendRawAsync(string message) => output.WriteLineAsync(message);
        public async Task<bool> ConnectionEndedAsync()
        {
            while (true)
            {
                string? line = await input.ReadLineAsync().WaitAsync(TimeSpan.FromSeconds(3));
                if (line is null) return true;
                var message = JsonSerializer.Deserialize<JsonElement>(line);
                if (message.GetProperty("op").GetString() != "input_ack") return false;
                RecordAcknowledgement(message);
            }
        }
        public void Disconnect() => server.Dispose();
        public async Task<LinuxPhysicalSession> AcquireAsync(CancellationToken cancellation = default)
        {
            Task<LinuxPhysicalSession> acquiring = Client.AcquirePhysicalAsync("/dev/hidraw4", cancellation);
            JsonElement request = await RequestAcquireAsync();
            await ReplyAsync(request, Identity);
            return await acquiring;
        }
        public static object Identity => new
        { isolationVerified = true, path = "/dev/hidraw4", vendorId = 0x054c, productId = 0x0ce6, productName = "DualSense",
          inputFlow = new { version = 1, window = 8, leaseId = LeaseId } };
        public async ValueTask DisposeAsync()
        {
            await Client.DisposeAsync(); output.Dispose(); input.Dispose(); server.Dispose(); listener.Stop();
        }
    }

    sealed class InterruptedWriteStream(Stream inner, Action beforeWrite, Action onClose) : Stream
    {
        public bool InterceptWrite { get; set; }
        public override bool CanRead => inner.CanRead;
        public override bool CanWrite => inner.CanWrite;
        public override bool CanSeek => false;
        public override long Length => throw new NotSupportedException();
        public override long Position { get => throw new NotSupportedException(); set => throw new NotSupportedException(); }
        public override int Read(byte[] buffer, int offset, int count) => inner.Read(buffer, offset, count);
        public override void Write(byte[] buffer, int offset, int count)
        {
            if (InterceptWrite) beforeWrite();
            inner.Write(buffer, offset, count);
        }
        public override ValueTask WriteAsync(ReadOnlyMemory<byte> buffer, CancellationToken cancellation = default) =>
            inner.WriteAsync(buffer, cancellation);
        public override void Flush() => inner.Flush();
        public override long Seek(long offset, SeekOrigin origin) => throw new NotSupportedException();
        public override void SetLength(long value) => throw new NotSupportedException();
        protected override void Dispose(bool disposing)
        {
            if (disposing) { inner.Dispose(); onClose(); }
            base.Dispose(disposing);
        }
    }

    [Test]
    public async Task PreflightReturnsUnavailableDetailWithoutOpeningPhysicalDevice()
    {
        await using var peer = await Peer.OpenAsync();
        Task<LinuxBrokerPreflight> task = peer.Client.PreflightAsync();
        JsonElement request = await peer.RequestAsync();
        Assert.That(request.GetProperty("op").GetString(), Is.EqualTo("preflight"));
        await peer.ReplyAsync(request, new { available = false, detail = "vhci_hcd is not installed" });
        Assert.That(await task, Is.EqualTo(new LinuxBrokerPreflight(false, "vhci_hcd is not installed")));
        Assert.That((await task).CleanupConfirmed, Is.False, "An older helper does not acknowledge lease recovery.");
    }

    [Test]
    public async Task LegacyHelperIsRejectedBeforeAnyPhysicalAcquisition()
    {
        await using var peer = await Peer.OpenAsync();
        var acquiring = peer.Client.AcquirePhysicalAsync("/dev/hidraw4");
        var request = await peer.RequestAsync();
        Assert.That(request.GetProperty("op").GetString(), Is.EqualTo("preflight"));
        await peer.ReplyAsync(request, new { available = true, detail = "legacy helper" });
        var error = await Assert.ThrowsAsync<IOException>(async () => await acquiring);
        Assert.That(error!.Message, Does.Contain("Update the MadSense system helper"));
        Assert.That(await peer.ConnectionEndedAsync(), Is.True, "No acquire request may follow unsupported capability.");
    }

    [TestCase(false, false, true)]
    [TestCase(true, false, false)]
    [TestCase(false, true, false)]
    public async Task PreflightOnlyConfirmsCleanupWhenExplicitlyIdle(bool leaseActive, bool recoveryPending, bool confirmed)
    {
        await using var peer = await Peer.OpenAsync();
        var pending = peer.Client.PreflightAsync();
        var request = await peer.RequestAsync();
        await peer.ReplyAsync(request, new { available = true, detail = "broker available", leaseActive, recoveryPending });
        Assert.That((await pending).CleanupConfirmed, Is.EqualTo(confirmed));
    }

    [Test]
    public async Task ProxyPreservesInputWriteAndFeatureReportBytes()
    {
        await using var peer = await Peer.OpenAsync();
        LinuxPhysicalSession session = await peer.AcquireAsync();
        Assert.That(session.IsolationVerified, Is.True);
        Assert.That(session.Device.VendorId, Is.EqualTo(0x054c));
        byte[] input = { 0x31, 2, 3, 4 };
        await peer.SendAsync(new { @event = "input", data = Convert.ToBase64String(input) });
        byte[] destination = new byte[8];
        Assert.That(await session.Device.ReadAsync(destination, 2, 4, CancellationToken.None).WaitAsync(TimeSpan.FromSeconds(3)), Is.EqualTo(4));
        Assert.That(destination.AsSpan(2, 4).ToArray(), Is.EqualTo(input));
        Task<int> write = Task.Run(() => session.Device.Write(destination, 2, 4));
        JsonElement request = await peer.RequestAsync();
        Assert.That(request.GetProperty("op").GetString(), Is.EqualTo("write"));
        Assert.That(Convert.FromBase64String(request.GetProperty("data").GetString()!), Is.EqualTo(input));
        await peer.ReplyAsync(request, new { written = 4 });
        Assert.That(await write, Is.EqualTo(4));
        Task<byte[]> feature = Task.Run(() => session.Device.GetFeatureReport(0x20, 64));
        request = await peer.RequestAsync();
        Assert.That(request.GetProperty("reportId").GetInt32(), Is.EqualTo(0x20));
        byte[] expectedFeature = { 0x20, 9, 8, 7 };
        await peer.ReplyAsync(request, new { data = Convert.ToBase64String(expectedFeature) });
        Assert.That(await feature, Is.EqualTo(expectedFeature));
        LinuxProxyMetrics metrics = session.Metrics;
        Assert.That(metrics.ReceivedReports, Is.EqualTo(1));
        Assert.That(metrics.ReadReports, Is.EqualTo(1));
        Assert.That(metrics.QueuedReports, Is.EqualTo(0));
        Assert.That(metrics.ClientQueueAge.Count, Is.EqualTo(1));
        Assert.That(metrics.HidRpcRoundTrip.Count, Is.EqualTo(2));
        Assert.That(metrics.LastDeliveredTimestamp, Is.GreaterThanOrEqualTo(metrics.LastReceivedTimestamp));
    }

    [TestCase(null, null)]
    [TestCase("0", 0L)]
    [TestCase("17", 17L)]
    [TestCase("9223372036854775807", long.MaxValue)]
    public async Task SetupDrainCountIsAnImmutableAcquisitionSnapshotSeparateFromInputCounters(string? wireCount, long? expected)
    {
        await using var peer = await Peer.OpenAsync();
        Task<LinuxPhysicalSession> acquiring = peer.Client.AcquirePhysicalAsync("/dev/hidraw4");
        JsonElement request = await peer.RequestAcquireAsync();
        var identity = JsonSerializer.Deserialize<Dictionary<string, JsonElement>>(JsonSerializer.Serialize(Peer.Identity))!;
        if (wireCount is not null) identity["setupReportsDrained"] = JsonSerializer.Deserialize<JsonElement>(wireCount);
        await peer.ReplyAsync(request, identity);
        LinuxPhysicalSession session = await acquiring;
        Assert.That(session.Metrics.SetupReportsDrained, Is.EqualTo(expected));
        Assert.That(session.Metrics.ReceivedReports, Is.Zero);
        Assert.That(session.Metrics.ReadReports, Is.Zero);
        Assert.That(session.Metrics.InitializationDiscardedReports, Is.Zero);
        await peer.SendAsync(new { @event = "input", data = "MQE=" });
        Assert.That(await session.Device.ReadAsync(new byte[64], 0, 64, CancellationToken.None).WaitAsync(TimeSpan.FromSeconds(3)), Is.EqualTo(2));
        Assert.That(session.Metrics.SetupReportsDrained, Is.EqualTo(expected));
        Assert.That(session.Metrics.ReceivedReports, Is.EqualTo(1));
        Assert.That(session.Metrics.ReadReports, Is.EqualTo(1));
        Assert.That(session.Metrics.InitializationDiscardedReports, Is.Zero);
    }

    [TestCase("-1")]
    [TestCase("1.5")]
    [TestCase("1.0")]
    [TestCase("\"1\"")]
    [TestCase("true")]
    [TestCase("null")]
    [TestCase("9223372036854775808")]
    public async Task InvalidSetupDrainCountRejectsAcquisitionAndClosesItsLease(string wireCount)
    {
        await using var peer = await Peer.OpenAsync();
        Task<LinuxPhysicalSession> acquiring = peer.Client.AcquirePhysicalAsync("/dev/hidraw4");
        JsonElement request = await peer.RequestAcquireAsync();
        var identity = JsonSerializer.Deserialize<Dictionary<string, JsonElement>>(JsonSerializer.Serialize(Peer.Identity))!;
        identity["setupReportsDrained"] = JsonSerializer.Deserialize<JsonElement>(wireCount);
        await peer.ReplyAsync(request, identity);
        var error = await Assert.ThrowsAsync<IOException>(async () => await acquiring.WaitAsync(TimeSpan.FromSeconds(3)));
        Assert.That(error!.Message, Does.Contain("invalid setup input drain count"));
        Assert.That(await peer.ConnectionEndedAsync(), Is.True);
        Assert.That(peer.Client.IsConnected, Is.False);
    }

    [Test]
    public async Task LostIsolationFailsPendingReadsAndRaisesOneEventOffResponsePump()
    {
        await using var peer = await Peer.OpenAsync();
        LinuxPhysicalSession session = await peer.AcquireAsync();
        var lost = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        int calls = 0;
        session.IsolationLost += () => { Interlocked.Increment(ref calls); lost.TrySetResult(); };
        Task<int> read = session.Device.ReadAsync(new byte[64], 0, 64, CancellationToken.None);
        await peer.SendAsync(new { @event = "isolation_lost", reason = "udev replaced the device permissions" });
        await lost.Task.WaitAsync(TimeSpan.FromSeconds(3));
        await Assert.ThrowsAsync<HidException>(async () => await read);
        Assert.That(session.IsolationVerified, Is.False);
        Assert.That(session.CleanupConfirmed, Is.False);
        Assert.That(session.Device.IsConnected, Is.False);
        Assert.That(session.AttachStrategy.TryAttach("127.0.0.1", 3241, "1-1", out _), Is.False);
        await peer.SendAsync(new { @event = "isolation_lost", reason = "device removed" });
        Assert.That(calls, Is.EqualTo(1));
    }

    [Test]
    public async Task InputDuringAcquisitionIsDeliveredInOrderWithoutAnUnobservedGap()
    {
        await using var peer = await Peer.OpenAsync();
        Task<LinuxPhysicalSession> task = peer.Client.AcquirePhysicalAsync("/dev/hidraw4");
        JsonElement request = await peer.RequestAcquireAsync();
        // A single write puts input immediately after negotiation, without waiting
        // for the acquisition continuation to publish its session.
        await peer.SendRawAsync(JsonSerializer.Serialize(new { id = request.GetProperty("id").GetInt64(), ok = true, result = Peer.Identity }) + "\n" +
            string.Concat(Enumerable.Range(1, 2).Select(sequence => JsonSerializer.Serialize(new
            { @event = "input", data = Convert.ToBase64String(new byte[] { 0x31, (byte)sequence }),
              leaseId = Peer.LeaseId, sequence, creditBlockedCount = 0, creditBlockedNs = 0,
              creditMaxBlockedNs = 0, brokerReadMonotonicNs = sequence == 2 ? 123456789L : 0L }) + "\n")));
        LinuxPhysicalSession session = await task;
        byte[] buffer = new byte[64];
        Assert.That(session.Device.Read(buffer, 0, 64, 1000), Is.EqualTo(2));
        Assert.That(buffer[1], Is.EqualTo(1));
        Assert.That(session.Device.Read(buffer, 0, 64, 1000), Is.EqualTo(2));
        Assert.That(buffer[1], Is.EqualTo(2));
        Assert.That(session.Metrics.ReceivedReports, Is.EqualTo(2));
        Assert.That(session.Metrics.ReadReports, Is.EqualTo(2));
        Assert.That(session.Metrics.LastBrokerReadMonotonicNs, Is.EqualTo(123456789L));
    }

    [Test]
    public async Task InputBeforeNegotiationNeverReturnsReady()
    {
        await using var peer = await Peer.OpenAsync();
        Task<LinuxPhysicalSession> task = peer.Client.AcquirePhysicalAsync("/dev/hidraw4");
        await peer.RequestAcquireAsync();
        await peer.SendAsync(new { @event = "input", data = "MQE=" });
        var error = await Assert.ThrowsAsync<IOException>(async () => await task.WaitAsync(TimeSpan.FromSeconds(3)));
        Assert.That(error!.Message, Does.Contain("before flow-control negotiation"));
        Assert.That(peer.Client.IsConnected, Is.False);
    }

    [Test]
    public async Task LostIsolationBeforeAcquisitionCompletesNeverReturnsReady()
    {
        await using var peer = await Peer.OpenAsync();
        Task<LinuxPhysicalSession> task = peer.Client.AcquirePhysicalAsync("/dev/hidraw4");
        JsonElement request = await peer.RequestAcquireAsync();
        await peer.SendAsync(new { @event = "isolation_lost", reason = "node changed during acquire" });
        await peer.ReplyAsync(request, Peer.Identity);
        var error = await Assert.ThrowsAsync<IOException>(async () => await task);
        Assert.That(error!.Message, Is.EqualTo("Broker isolation lost: node changed during acquire"));
    }

    [Test]
    public async Task DisconnectEndsReadAndSession()
    {
        await using var peer = await Peer.OpenAsync();
        LinuxPhysicalSession session = await peer.AcquireAsync();
        Task<int> read = session.Device.ReadAsync(new byte[64], 0, 64, CancellationToken.None);
        peer.Disconnect();
        await Assert.ThrowsAsync<HidException>(async () => await read.WaitAsync(TimeSpan.FromSeconds(3)));
        Assert.That(session.IsolationVerified, Is.False);
    }

    [Test]
    public async Task FirstBrokerIsolationCauseSurvivesLaterEventsAndPendingFeatureCancellation()
    {
        await using var peer = await Peer.OpenAsync();
        LinuxPhysicalSession session = await peer.AcquireAsync();
        Task<byte[]> feature = Task.Run(() => session.Device.GetFeatureReport(0x05, 41));
        Assert.That((await peer.RequestAsync()).GetProperty("op").GetString(), Is.EqualTo("get_feature"));
        await peer.SendRawAsync("{\"event\":\"isolation_lost\",\"reason\":\"Eight physical input reports are already pending\"}\n" +
                               "{\"event\":\"isolation_lost\",\"reason\":\"later device removal\"}");
        peer.Disconnect();
        var error = await Assert.ThrowsAsync<HidException>(async () => await feature.WaitAsync(TimeSpan.FromSeconds(3)));
        const string expected = "Broker isolation lost: Eight physical input reports are already pending";
        Assert.That(error!.Message, Is.EqualTo(expected), "Lifetime cancellation must retain the broker's actual cause.");
        Assert.That(session.IsolationLostReason, Is.EqualTo(expected));
        await session.DisposeAsync();
        Assert.That(session.IsolationLostReason, Is.EqualTo(expected));
        Assert.That(session.CleanupConfirmed, Is.False);
    }

    [Test]
    public async Task PlainDisconnectFailsPendingFeatureWithTransportReasonInsteadOfTimeout()
    {
        await using var peer = await Peer.OpenAsync();
        LinuxPhysicalSession session = await peer.AcquireAsync();
        Task<byte[]> feature = Task.Run(() => session.Device.GetFeatureReport(0x05, 41));
        await peer.RequestAsync();
        peer.Disconnect();
        var error = await Assert.ThrowsAsync<HidException>(async () => await feature.WaitAsync(TimeSpan.FromSeconds(3)));
        Assert.That(error!.Message, Is.EqualTo("The device broker closed its session."));
        Assert.That(session.IsolationLostReason, Is.EqualTo(error.Message));
    }

    [Test]
    public async Task MalformedReplyWakesItsPendingRequestWhenTheConnectionEnds()
    {
        await using var peer = await Peer.OpenAsync();
        Task<JsonElement> task = peer.Client.RequestAsync("preflight");
        JsonElement request = await peer.RequestAsync();
        await peer.SendAsync(new { id = request.GetProperty("id").GetInt64(), ok = true });
        await Assert.ThrowsAsync<IOException>(async () => await task.WaitAsync(TimeSpan.FromSeconds(3)));
        Assert.That(peer.Client.IsConnected, Is.False);
    }

    [Test]
    public async Task OversizedProtocolMessageFailsClosed()
    {
        await using var peer = await Peer.OpenAsync();
        LinuxPhysicalSession session = await peer.AcquireAsync();
        Task<int> read = session.Device.ReadAsync(new byte[64], 0, 64, CancellationToken.None);
        await peer.SendRawAsync(new string('x', LinuxBrokerClient.MaximumLineBytes + 1));
        await Assert.ThrowsAsync<HidException>(async () => await read.WaitAsync(TimeSpan.FromSeconds(3)));
        Assert.That(session.IsolationVerified, Is.False);
    }

    [Test]
    public async Task AttachIsLocalAndDetachRequiresThisSessionOwnership()
    {
        await using var peer = await Peer.OpenAsync();
        LinuxPhysicalSession session = await peer.AcquireAsync();
        session.CancelPendingAttach();
        Assert.That(session.IsolationVerified, Is.True);
        Assert.That(session.AttachStrategy.TryAttach("192.0.2.1", 3241, "1-1", out _), Is.False);
        Assert.That(session.AttachStrategy.TryAttach("127.0.0.1", 22, "1-1", out _), Is.False);
        Assert.That(session.AttachStrategy.TryDetach("1-1", out _), Is.False);
        Task<bool> attach = Task.Run(() => session.AttachStrategy.TryAttach("127.0.0.1", 3241, "1-1", out _));
        JsonElement request = await peer.RequestAsync();
        Assert.That(request.GetProperty("op").GetString(), Is.EqualTo("attach"));
        await peer.ReplyAsync(request, new { });
        Assert.That(await attach, Is.True);
        session.CancelPendingAttach();
        Assert.That(peer.Client.IsConnected, Is.True, "A completed import must use normal detach/release ordering.");
        Assert.That(session.AttachStrategy.TryAttach("127.0.0.1", 3241, "1-1", out _), Is.False);
        Task<bool> detach = Task.Run(() => session.AttachStrategy.TryDetach("1-1", out _));
        request = await peer.RequestAsync();
        Assert.That(request.GetProperty("op").GetString(), Is.EqualTo("detach"));
        await peer.ReplyAsync(request, new { });
        Assert.That(await detach, Is.True);
        Assert.That(session.AttachStrategy.TryDetach("1-1", out _), Is.False);
    }

    [Test]
    public async Task CancelledRequestClosesLeaseInsteadOfLeavingAnUnknownAttach()
    {
        await using var peer = await Peer.OpenAsync();
        LinuxPhysicalSession session = await peer.AcquireAsync();
        using var cancel = new CancellationTokenSource();
        Task<JsonElement> request = peer.Client.RequestAsync("attach", new() { ["host"] = "127.0.0.1", ["port"] = 3241, ["busId"] = "1-1" }, cancel.Token);
        await peer.RequestAsync(); cancel.Cancel();
        await Assert.ThrowsAsync(Is.InstanceOf<OperationCanceledException>(), async () => await request);
        Assert.That(session.IsolationVerified, Is.False);
        Assert.That(peer.Client.IsConnected, Is.False);
    }

    [Test]
    public async Task PendingAttachCancellationDoesNotWaitForItsOwnershipLock()
    {
        await using var peer = await Peer.OpenAsync();
        LinuxPhysicalSession session = await peer.AcquireAsync();
        Task<bool> attach = Task.Run(() => session.AttachStrategy.TryAttach("127.0.0.1", 3241, "1-1", out _));
        Assert.That((await peer.RequestAsync()).GetProperty("op").GetString(), Is.EqualTo("attach"));
        // No reply arrives. The cancel path must not wait on TryAttach's gate.
        await Task.Run(session.CancelPendingAttach).WaitAsync(TimeSpan.FromSeconds(1));
        Assert.That(await attach.WaitAsync(TimeSpan.FromSeconds(2)), Is.False);
        Assert.That(await peer.ConnectionEndedAsync(), Is.True, "EOF hands owned import cleanup to the broker.");
        Assert.That(session.IsolationVerified, Is.False);
    }

    [TestCase(false)]
    [TestCase(true)]
    public async Task AcquisitionCancellationAlsoAbortsTheLaterVirtualImport(bool cancelBeforeAttach)
    {
        await using var peer = await Peer.OpenAsync();
        using var cancellation = new CancellationTokenSource();
        LinuxPhysicalSession session = await peer.AcquireAsync(cancellation.Token);
        if (cancelBeforeAttach) cancellation.Cancel();
        Task<bool> attach = Task.Run(() => session.AttachStrategy.TryAttach("127.0.0.1", 3241, "1-1", out _));
        if (!cancelBeforeAttach)
        {
            Assert.That((await peer.RequestAsync()).GetProperty("op").GetString(), Is.EqualTo("attach"));
            cancellation.Cancel();
        }
        Assert.That(await attach.WaitAsync(TimeSpan.FromSeconds(2)), Is.False);
        Assert.That(await peer.ConnectionEndedAsync(), Is.True);
        Assert.That(session.IsolationVerified, Is.False);
    }

    [Test]
    public async Task DisposeReleasesBeforeClosingAndWakesPendingRead()
    {
        await using var peer = await Peer.OpenAsync();
        LinuxPhysicalSession session = await peer.AcquireAsync();
        Task<int> read = session.Device.ReadAsync(new byte[64], 0, 64, CancellationToken.None);
        Task disposing = session.DisposeAsync().AsTask();
        JsonElement request = await peer.RequestAsync();
        Assert.That(request.GetProperty("op").GetString(), Is.EqualTo("release"));
        await peer.ReplyAsync(request, new { });
        await disposing;
        Assert.That(session.CleanupConfirmed, Is.True);
        Assert.That(session.IsolationLostReason, Is.Null, "Ordered release is not an isolation failure.");
        await Assert.ThrowsAsync<HidException>(async () => await read);
        Assert.That(peer.Client.IsConnected, Is.False);
    }

    [Test]
    public async Task DisposingAfterTransportLossCannotAcknowledgeHelperCleanup()
    {
        await using var peer = await Peer.OpenAsync();
        LinuxPhysicalSession session = await peer.AcquireAsync();
        await peer.Client.DisposeAsync();
        await session.DisposeAsync();
        Assert.That(session.CleanupConfirmed, Is.False);
    }

    [Test]
    public async Task CancellingSetupConnectionWakesPendingFeatureReadAndLeavesCleanupUnconfirmed()
    {
        await using var peer = await Peer.OpenAsync();
        LinuxPhysicalSession session = await peer.AcquireAsync();
        using var cancellation = new CancellationTokenSource();
        using var registration = cancellation.Token.Register(() => { _ = peer.Client.DisposeAsync(); });
        Task<byte[]> feature = Task.Run(() => session.Device.GetFeatureReport(0x20, 64));
        Assert.That((await peer.RequestAsync()).GetProperty("op").GetString(), Is.EqualTo("get_feature"));
        cancellation.Cancel();
        await Assert.ThrowsAsync<HidException>(async () => await feature.WaitAsync(TimeSpan.FromSeconds(1)));
        Assert.That(await peer.ConnectionEndedAsync(), Is.True);
        await session.DisposeAsync();
        Assert.That(session.CleanupConfirmed, Is.False);
    }

    [Test]
    public async Task BrokerExceedingNegotiatedInputCreditEndsReadinessWithoutAcceptingTheExtraReport()
    {
        await using var peer = await Peer.OpenAsync();
        LinuxPhysicalSession session = await peer.AcquireAsync();
        Assert.That(session.Device.Read(new byte[64], 0, 64, 0), Is.Zero);
        var lost = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        session.IsolationLost += () => lost.TrySetResult();
        for (int i = 0; i < 9; ++i)
            await peer.SendAsync(new { @event = "input", data = Convert.ToBase64String(new byte[] { 0x31, (byte)i }) });
        await lost.Task.WaitAsync(TimeSpan.FromSeconds(3));
        Assert.That(session.IsolationVerified, Is.False);
        Assert.That(session.Metrics.ReceivedReports, Is.EqualTo(8));
        Assert.That(session.Metrics.InputOverflows, Is.Zero);
        Assert.That(session.Metrics.QueuedReports, Is.EqualTo(8));
        Assert.That(session.Metrics.ReadReports, Is.EqualTo(0));
        Assert.That(session.IsolationLostReason, Does.Contain("credit window"));
        Assert.Throws<HidException>(() => session.Device.Read(new byte[64], 0, 64, 0));
    }

    [Test]
    public async Task FeatureInitializationRetainsRecentInputAndCountsEverySupersededReport()
    {
        await using var peer = await Peer.OpenAsync();
        LinuxPhysicalSession session = await peer.AcquireAsync();
        // The shared controller constructor performs feature reads before its
        // input thread starts; model input arriving during that initialization.
        for (int i = 0; i < 20; ++i)
        {
            await peer.SendAsync(new { @event = "input", data = Convert.ToBase64String(new byte[] { 0x31, (byte)i }) });
            if (i >= 7) Assert.That(await peer.AcknowledgementAsync(), Is.EqualTo(i - 6), "Setup discard must return actual credit before more input.");
        }
        long deadline = Environment.TickCount64 + 3000;
        while (session.Metrics.ReceivedReports < 20 && Environment.TickCount64 < deadline) await Task.Delay(1);
        Assert.That(session.IsolationVerified, Is.True);
        Assert.That(session.Metrics.InitializationDiscardedReports, Is.EqualTo(13));
        Assert.That(session.Metrics.InputOverflows, Is.Zero);
        byte[] buffer = new byte[64];
        for (int i = 13; i < 20; ++i)
        {
            Assert.That(session.Device.Read(buffer, 0, 64, 0), Is.EqualTo(2));
            Assert.That(buffer[1], Is.EqualTo(i));
        }
        Assert.That(session.Metrics.ReceivedReports,
            Is.EqualTo(session.Metrics.ReadReports + session.Metrics.InitializationDiscardedReports));
    }

    [Test]
    public async Task FullInputWindowStillDeliversFeatureRepliesAndConsumptionReturnsExactlyOneCredit()
    {
        await using var peer = await Peer.OpenAsync();
        LinuxPhysicalSession session = await peer.AcquireAsync();
        byte[] buffer = new byte[64];
        Assert.That(session.Device.Read(buffer, 0, buffer.Length, 0), Is.Zero);
        for (int i = 1; i <= 8; ++i)
            await peer.SendAsync(new { @event = "input", data = Convert.ToBase64String(new byte[] { 0x31, (byte)i }) });
        Task<byte[]> feature = Task.Run(() => session.Device.GetFeatureReport(0x20, 64));
        var request = await peer.RequestAsync();
        Assert.That(request.GetProperty("op").GetString(), Is.EqualTo("get_feature"));
        await peer.ReplyAsync(request, new { data = "IAE=" });
        Assert.That(await feature.WaitAsync(TimeSpan.FromSeconds(3)), Is.EqualTo(new byte[] { 0x20, 1 }));
        Assert.That(session.Metrics.QueuedReports, Is.EqualTo(8));
        Assert.That(peer.Acknowledgements, Is.Empty);
        Assert.That(session.Device.Read(buffer, 0, buffer.Length, 0), Is.EqualTo(2));
        Assert.That(buffer[1], Is.EqualTo(1));
        Assert.That(await peer.AcknowledgementAsync(), Is.EqualTo(1));
        await peer.SendAsync(new { @event = "input", data = "MQk=" });
        for (int i = 2; i <= 9; ++i)
        {
            Assert.That(session.Device.Read(buffer, 0, buffer.Length, 1000), Is.EqualTo(2));
            Assert.That(buffer[1], Is.EqualTo(i));
        }
        Assert.That(session.Metrics.InitializationDiscardedReports, Is.Zero);
        Assert.That(session.Metrics.InputOverflows, Is.Zero);
        Assert.That(session.Metrics.ReadReports, Is.EqualTo(9));
        Assert.That(session.IsolationVerified, Is.True);
    }

    [Test]
    public async Task BlockedAcknowledgementWriterNeverBlocksInputConsumptionOrConnectionDisposal()
    {
        using var writeEntered = new ManualResetEventSlim();
        using var streamClosed = new ManualResetEventSlim();
        InterruptedWriteStream? wrapped = null;
        await using var peer = await Peer.OpenAsync(stream => wrapped = new(stream, () =>
        {
            writeEntered.Set();
            if (!streamClosed.Wait(TimeSpan.FromSeconds(3))) throw new TimeoutException("Disposal did not close the ACK stream.");
            throw new IOException("The blocked acknowledgement was interrupted by socket closure.");
        }, streamClosed.Set));
        LinuxPhysicalSession session = await peer.AcquireAsync();
        wrapped!.InterceptWrite = true;
        await peer.SendAsync(new { @event = "input", data = "MQE=" });
        Assert.That(session.Device.Read(new byte[64], 0, 64, 1000), Is.EqualTo(2));
        Assert.That(writeEntered.Wait(TimeSpan.FromSeconds(3)), Is.True);
        await peer.Client.DisposeAsync().AsTask().WaitAsync(TimeSpan.FromSeconds(2));
        Assert.That(await peer.ConnectionEndedAsync(), Is.True);
        Assert.That(session.IsolationVerified, Is.False);
        Assert.That(session.Metrics.InputFlow!.AcknowledgementFrames, Is.Zero);
    }

    [Test]
    public async Task SynchronousRequestDeadlineClosesAnUnresponsivePeer()
    {
        await using var peer = await Peer.OpenAsync();
        LinuxPhysicalSession session = await peer.AcquireAsync();
        var task = Task.Run(() => peer.Client.Request("get_feature", new() { ["reportId"] = 5, ["size"] = 41 },
            deadline: TimeSpan.FromMilliseconds(250)));
        JsonElement request = await peer.RequestAsync();
        Assert.That(request.GetProperty("op").GetString(), Is.EqualTo("get_feature"));
        // The peer received a complete request but deliberately never replies.
        var failure = await Assert.ThrowsAsync<IOException>(async () => await task.WaitAsync(TimeSpan.FromSeconds(3)));
        Assert.That(failure!.Message, Does.Contain("timed out"));
        Assert.That(await peer.ConnectionEndedAsync(), Is.True);
        Assert.That(session.IsolationLostReason, Does.Contain("timed out"));
        Assert.That(session.CleanupConfirmed, Is.False);
    }

    [Test]
    public async Task SynchronousRequestCancellationClosesAnUnresponsivePeer()
    {
        await using var peer = await Peer.OpenAsync();
        LinuxPhysicalSession session = await peer.AcquireAsync();
        using var cancel = new CancellationTokenSource();
        var task = Task.Run(() => peer.Client.Request("get_feature", new() { ["reportId"] = 5, ["size"] = 41 }, cancel.Token));
        await peer.RequestAsync();
        cancel.Cancel();
        await Assert.ThrowsAsync<OperationCanceledException>(async () => await task.WaitAsync(TimeSpan.FromSeconds(3)));
        Assert.That(await peer.ConnectionEndedAsync(), Is.True);
        Assert.That(session.IsolationLostReason, Does.Contain("cancelled"));
        Assert.That(session.CleanupConfirmed, Is.False);
    }

    [Test]
    public async Task ExplicitCancellationKeepsItsTokenWhenClosingTheSocketInterruptsWrite()
    {
        using var writeEntered = new ManualResetEventSlim();
        using var streamClosed = new ManualResetEventSlim();
        InterruptedWriteStream? wrapped = null;
        await using var peer = await Peer.OpenAsync(stream => wrapped = new(stream, () =>
        {
            writeEntered.Set();
            if (!streamClosed.Wait(TimeSpan.FromSeconds(3))) throw new TimeoutException("Cancellation did not close the stream.");
            throw new IOException("The synchronous write was interrupted by socket closure.");
        }, streamClosed.Set));
        LinuxPhysicalSession session = await peer.AcquireAsync();
        using var cancel = new CancellationTokenSource();
        wrapped!.InterceptWrite = true;
        var request = Task.Run(() => peer.Client.Request("get_feature", new() { ["reportId"] = 5, ["size"] = 41 }, cancel.Token));
        Assert.That(writeEntered.Wait(TimeSpan.FromSeconds(3)), Is.True);
        cancel.Cancel();
        var error = await Assert.ThrowsAsync<OperationCanceledException>(async () => await request.WaitAsync(TimeSpan.FromSeconds(3)));
        Assert.That(error!.CancellationToken, Is.EqualTo(cancel.Token));
        Assert.That(await peer.ConnectionEndedAsync(), Is.True);
        Assert.That(session.CleanupConfirmed, Is.False);
    }

    [Test]
    public async Task EofBeforeReleaseAcknowledgementNeverConfirmsCleanup()
    {
        await using var peer = await Peer.OpenAsync();
        LinuxPhysicalSession session = await peer.AcquireAsync();
        Task disposing = session.DisposeAsync().AsTask();
        Assert.That((await peer.RequestAsync()).GetProperty("op").GetString(), Is.EqualTo("release"));
        peer.Disconnect();
        await Assert.ThrowsAsync<IOException>(async () => await disposing.WaitAsync(TimeSpan.FromSeconds(3)));
        Assert.That(session.CleanupConfirmed, Is.False);
    }

    [Test]
    public async Task RejectedReleaseFollowedByEofNeverConfirmsCleanup()
    {
        await using var peer = await Peer.OpenAsync();
        LinuxPhysicalSession session = await peer.AcquireAsync();
        Task disposing = session.DisposeAsync().AsTask();
        JsonElement request = await peer.RequestAsync();
        await peer.SendAsync(new { id = request.GetProperty("id").GetInt64(), ok = false,
            error = new { code = "recovery_pending", message = "Physical reset is not confirmed" } });
        peer.Disconnect();
        var error = await Assert.CatchAsync<IOException>(async () => await disposing.WaitAsync(TimeSpan.FromSeconds(3)));
        Assert.That(error!.Message, Is.EqualTo("recovery_pending: Physical reset is not confirmed"));
        Assert.That(session.CleanupConfirmed, Is.False);
    }

    [Test]
    public async Task AuthorizationDenialIsTypedAndNeverCreatesAPhysicalSession()
    {
        await using var peer = await Peer.OpenAsync();
        Task<LinuxPhysicalSession> task = peer.Client.AcquirePhysicalAsync("/dev/hidraw4");
        JsonElement request = await peer.RequestAcquireAsync();
        await peer.SendAsync(new { id = request.GetProperty("id").GetInt64(), ok = false,
            error = new { code = "not_authorized", message = "Authorization was declined" } });
        await Assert.ThrowsAsync<UnauthorizedAccessException>(async () => await task);
        Assert.That(peer.Client.IsConnected, Is.False);
    }

    [TestCase("")]
    [TestCase("IQ==")]
    public async Task InvalidFeaturePayloadFailsBeforeAccessingItsReportId(string payload)
    {
        await using var peer = await Peer.OpenAsync();
        LinuxPhysicalSession session = await peer.AcquireAsync();
        Task<byte[]> task = Task.Run(() => session.Device.GetFeatureReport(0x20, 64));
        JsonElement request = await peer.RequestAsync();
        await peer.ReplyAsync(request, new { data = payload });
        await Assert.ThrowsAsync<HidException>(async () => await task);
        Assert.That(session.IsolationVerified, Is.True);
    }

    [Test]
    public async Task FailedOutputUsesSharedHidErrorAndEndsReadiness()
    {
        await using var peer = await Peer.OpenAsync();
        LinuxPhysicalSession session = await peer.AcquireAsync();
        Task<int> task = Task.Run(() => session.Device.Write(new byte[] { 0x31, 0 }, 0, 2));
        JsonElement request = await peer.RequestAsync();
        await peer.SendAsync(new { id = request.GetProperty("id").GetInt64(), ok = false,
            error = new { code = "io_failed", message = "The transport disconnected" } });
        await Assert.ThrowsAsync<HidException>(async () => await task);
        Assert.That(session.IsolationVerified, Is.False);
        Assert.That(session.Device.IsConnected, Is.False);
    }

    [TestCase("1-1", true)] [TestCase("65535-1", true)] [TestCase("65536-1", false)]
    [TestCase("01-1", false)] [TestCase("1-2", false)] [TestCase("1-1\n", false)]
    [TestCase("../../1-1", false)] [TestCase("-1", false)]
    public void BusIdValidationIsBoundedAndCanonical(string value, bool valid) =>
        Assert.That(LinuxAttachStrategy.ValidBusId(value), Is.EqualTo(valid));
}
