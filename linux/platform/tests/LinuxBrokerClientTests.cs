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
        readonly TcpListener listener;
        readonly TcpClient server;
        readonly StreamReader input;
        readonly StreamWriter output;
        public LinuxBrokerClient Client { get; }
        Peer(TcpListener listener, TcpClient server, LinuxBrokerClient client)
        {
            this.listener = listener; this.server = server; Client = client;
            input = new(server.GetStream(), Encoding.UTF8, leaveOpen: true);
            output = new(server.GetStream(), new UTF8Encoding(false), leaveOpen: true) { AutoFlush = true };
        }
        public static async Task<Peer> OpenAsync()
        {
            var listener = new TcpListener(IPAddress.Loopback, 0); listener.Start();
            var client = new TcpClient();
            await client.ConnectAsync((IPEndPoint)listener.LocalEndpoint);
            TcpClient server = await listener.AcceptTcpClientAsync();
            return new(listener, server, new LinuxBrokerClient(client.GetStream()));
        }
        public async Task<JsonElement> RequestAsync()
        {
            string? line = await input.ReadLineAsync().WaitAsync(TimeSpan.FromSeconds(3));
            Assert.That(line, Is.Not.Null);
            using var document = JsonDocument.Parse(line!);
            return document.RootElement.Clone();
        }
        public Task ReplyAsync(JsonElement request, object result) => SendAsync(new
        { id = request.GetProperty("id").GetInt64(), ok = true, result });
        public Task SendAsync(object message) => output.WriteLineAsync(JsonSerializer.Serialize(message));
        public Task SendRawAsync(string message) => output.WriteLineAsync(message);
        public void Disconnect() => server.Dispose();
        public async Task<LinuxPhysicalSession> AcquireAsync()
        {
            Task<LinuxPhysicalSession> acquiring = Client.AcquirePhysicalAsync("/dev/hidraw4");
            JsonElement request = await RequestAsync();
            Assert.That(request.GetProperty("op").GetString(), Is.EqualTo("acquire"));
            await ReplyAsync(request, Identity);
            return await acquiring;
        }
        public static object Identity => new
        { isolationVerified = true, path = "/dev/hidraw4", vendorId = 0x054c, productId = 0x0ce6, productName = "DualSense" };
        public async ValueTask DisposeAsync()
        {
            await Client.DisposeAsync(); output.Dispose(); input.Dispose(); server.Dispose(); listener.Stop();
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
        JsonElement request = await peer.RequestAsync();
        // Force delivery before the response continuation can publish the session.
        await peer.SendAsync(new { @event = "input", data = "MQE=" });
        await peer.SendAsync(new { @event = "input", data = "MQI=", brokerReadMonotonicNs = 123456789L });
        await peer.ReplyAsync(request, Peer.Identity);
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
    public async Task InputOverflowDuringAcquisitionNeverReturnsReady()
    {
        await using var peer = await Peer.OpenAsync();
        Task<LinuxPhysicalSession> task = peer.Client.AcquirePhysicalAsync("/dev/hidraw4");
        await peer.RequestAsync();
        await peer.SendRawAsync(string.Concat(Enumerable.Repeat("{\"event\":\"input\",\"data\":\"MQE=\"}\n", 9)));
        await Assert.ThrowsAsync<IOException>(async () => await task.WaitAsync(TimeSpan.FromSeconds(3)));
        Assert.That(peer.Client.IsConnected, Is.False);
    }

    [Test]
    public async Task LostIsolationBeforeAcquisitionCompletesNeverReturnsReady()
    {
        await using var peer = await Peer.OpenAsync();
        Task<LinuxPhysicalSession> task = peer.Client.AcquirePhysicalAsync("/dev/hidraw4");
        JsonElement request = await peer.RequestAsync();
        await peer.SendAsync(new { @event = "isolation_lost", reason = "node changed during acquire" });
        await peer.ReplyAsync(request, Peer.Identity);
        await Assert.ThrowsAsync<IOException>(async () => await task);
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
        Assert.That(session.AttachStrategy.TryAttach("192.0.2.1", 3241, "1-1", out _), Is.False);
        Assert.That(session.AttachStrategy.TryAttach("127.0.0.1", 22, "1-1", out _), Is.False);
        Assert.That(session.AttachStrategy.TryDetach("1-1", out _), Is.False);
        Task<bool> attach = Task.Run(() => session.AttachStrategy.TryAttach("127.0.0.1", 3241, "1-1", out _));
        JsonElement request = await peer.RequestAsync();
        Assert.That(request.GetProperty("op").GetString(), Is.EqualTo("attach"));
        await peer.ReplyAsync(request, new { });
        Assert.That(await attach, Is.True);
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
        await Assert.ThrowsAsync<HidException>(async () => await read);
        Assert.That(peer.Client.IsConnected, Is.False);
    }

    [Test]
    public async Task InputOverflowIsCountedAndEndsReadinessWithoutDroppingSilently()
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
        Assert.That(session.Metrics.ReceivedReports, Is.EqualTo(9));
        Assert.That(session.Metrics.InputOverflows, Is.EqualTo(1));
        Assert.That(session.Metrics.QueuedReports, Is.EqualTo(8));
        Assert.That(session.Metrics.ReadReports, Is.EqualTo(0));
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
            await peer.SendAsync(new { @event = "input", data = Convert.ToBase64String(new byte[] { 0x31, (byte)i }) });
        long deadline = Environment.TickCount64 + 3000;
        while (session.Metrics.ReceivedReports < 20 && Environment.TickCount64 < deadline) await Task.Delay(1);
        Assert.That(session.IsolationVerified, Is.True);
        Assert.That(session.Metrics.InitializationDiscardedReports, Is.EqualTo(12));
        Assert.That(session.Metrics.InputOverflows, Is.Zero);
        byte[] buffer = new byte[64];
        for (int i = 12; i < 20; ++i)
        {
            Assert.That(session.Device.Read(buffer, 0, 64, 0), Is.EqualTo(2));
            Assert.That(buffer[1], Is.EqualTo(i));
        }
        Assert.That(session.Metrics.ReceivedReports,
            Is.EqualTo(session.Metrics.ReadReports + session.Metrics.InitializationDiscardedReports));
    }

    [Test]
    public async Task AuthorizationDenialIsTypedAndNeverCreatesAPhysicalSession()
    {
        await using var peer = await Peer.OpenAsync();
        Task<LinuxPhysicalSession> task = peer.Client.AcquirePhysicalAsync("/dev/hidraw4");
        JsonElement request = await peer.RequestAsync();
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
