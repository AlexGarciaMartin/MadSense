using System.Collections.Concurrent;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Text.Json;
using MadSense.Platform;

namespace MadSense.Linux.Platform.Tests;

public sealed class LinuxInputFlowTests
{
    const string Lease = "0123456789abcdef0123456789abcdef";
    static JsonElement Identity => JsonSerializer.SerializeToElement(new { inputFlow = new { version = 1, window = 8, leaseId = Lease } });
    static JsonElement Input(long sequence, long count = 0, long duration = 0, long maximum = 0) =>
        JsonSerializer.SerializeToElement(new { leaseId = Lease, sequence, creditBlockedCount = count,
            creditBlockedNs = duration, creditMaxBlockedNs = maximum });

    sealed class Link : IAsyncDisposable
    {
        readonly TcpClient client, server;
        readonly StreamReader reader;
        readonly CancellationTokenSource cancellation = new();
        public SemaphoreSlim Writes { get; }
        public LinuxInputFlow Flow { get; }
        public ConcurrentQueue<string> Failures { get; } = new();
        Link(TcpClient client, TcpClient server, bool blockWriter)
        {
            this.client = client; this.server = server;
            reader = new(server.GetStream(), Encoding.UTF8, leaveOpen: true);
            Writes = new(blockWriter ? 0 : 1, 1);
            Flow = new(Identity, client.GetStream(), Writes, cancellation.Token, Failures.Enqueue);
        }
        public static async Task<Link> Open(bool blockWriter = false)
        {
            var listener = new TcpListener(IPAddress.Loopback, 0); listener.Start();
            try
            {
                var client = new TcpClient();
                await client.ConnectAsync((IPEndPoint)listener.LocalEndpoint);
                return new(client, await listener.AcceptTcpClientAsync(), blockWriter);
            }
            finally { listener.Stop(); }
        }
        public async Task<long> Ack()
        {
            string? line = await reader.ReadLineAsync().WaitAsync(TimeSpan.FromSeconds(3));
            Assert.That(line, Is.Not.Null);
            var row = JsonSerializer.Deserialize<JsonElement>(line!);
            Assert.That(row.EnumerateObject().Select(p => p.Name), Is.EquivalentTo(new[] { "id", "op", "leaseId", "through" }));
            Assert.That(row.GetProperty("id").GetInt64(), Is.Zero);
            Assert.That(row.GetProperty("op").GetString(), Is.EqualTo("input_ack"));
            Assert.That(row.GetProperty("leaseId").GetString(), Is.EqualTo(Lease));
            return row.GetProperty("through").GetInt64();
        }
        public async ValueTask DisposeAsync()
        {
            Flow.Stop(); cancellation.Cancel(); client.Dispose();
            await Flow.Completion.WaitAsync(TimeSpan.FromSeconds(3));
            reader.Dispose(); server.Dispose(); Writes.Dispose(); cancellation.Dispose();
        }
    }

    [TestCase("{}")]
    [TestCase("{\"inputFlow\":{\"version\":2,\"window\":8}}")]
    [TestCase("{\"inputFlow\":{\"version\":1,\"window\":16}}")]
    [TestCase("{\"inputFlow\":{\"version\":true,\"window\":8}}")]
    public void UnsupportedHelperCapabilityCannotCreateAFlow(string raw)
    {
        Assert.Throws<IOException>(() => LinuxInputFlow.RequireCapability(JsonSerializer.Deserialize<JsonElement>(raw)));
    }

    [Test]
    public async Task OutOfOrderConsumptionOnlyAcknowledgesAContiguousPrefix()
    {
        await using var link = await Link.Open(blockWriter: true);
        for (long sequence = 1; sequence <= 4; ++sequence) link.Flow.Receive(Input(sequence));
        link.Flow.Consumed(2); link.Flow.Consumed(4);
        Assert.That(link.Flow.Metrics.ConsumedThrough, Is.Zero);
        link.Flow.Consumed(1);
        Assert.That(link.Flow.Metrics.ConsumedThrough, Is.EqualTo(2));
        link.Writes.Release();
        Assert.That(await link.Ack(), Is.EqualTo(2));
        link.Flow.Consumed(3);
        Assert.That(await link.Ack(), Is.EqualTo(4));
        Assert.That(link.Failures, Is.Empty);
    }

    [Test]
    public async Task EightOutstandingReportsAreBoundedAndConsumedSlotsCanBeReused()
    {
        await using var link = await Link.Open();
        for (long first = 1; first <= 33; first += 8)
        {
            for (long sequence = first; sequence < first + 8; ++sequence)
                Assert.That(link.Flow.Receive(Input(sequence)), Is.EqualTo(sequence));
            Assert.Throws<IOException>(() => link.Flow.Receive(Input(first + 8)));
            Assert.That(link.Flow.Metrics.ReceivedSequence, Is.EqualTo(first + 7));
            for (long sequence = first + 7; sequence >= first; --sequence) link.Flow.Consumed(sequence);
            Assert.That(await link.Ack(), Is.EqualTo(first + 7));
        }
        Assert.That(link.Flow.Metrics.KernelBacklogMeasured, Is.False);
        Assert.That(link.Failures, Is.Empty);
    }

    [Test]
    public async Task DuplicateUnknownOrAlreadyConsumedReportsNeverCreateExtraCredit()
    {
        await using var link = await Link.Open(blockWriter: true);
        link.Flow.Receive(Input(1)); link.Flow.Receive(Input(2));
        Assert.Throws<IOException>(() => link.Flow.Consumed(0));
        Assert.Throws<IOException>(() => link.Flow.Consumed(3));
        link.Flow.Consumed(2);
        Assert.Throws<IOException>(() => link.Flow.Consumed(2));
        link.Flow.Consumed(1);
        Assert.Throws<IOException>(() => link.Flow.Consumed(1));
        link.Writes.Release();
        Assert.That(await link.Ack(), Is.EqualTo(2));
    }

    [Test]
    public async Task InvalidLeaseSequenceAndCounterSnapshotsDoNotAdvanceInput()
    {
        await using var link = await Link.Open();
        link.Flow.Receive(Input(1, 2, 100, 60));
        var original = JsonSerializer.Deserialize<Dictionary<string, JsonElement>>(Input(2, 3, 150, 80).GetRawText())!;
        foreach (var (name, raw) in new[] { ("leaseId", "\"ffffffffffffffffffffffffffffffff\""),
            ("sequence", "1"), ("sequence", "3"), ("sequence", "true"), ("sequence", "9223372036854775808"),
            ("creditBlockedCount", "1"), ("creditBlockedNs", "99"), ("creditMaxBlockedNs", "59"),
            ("creditMaxBlockedNs", "151"), ("creditBlockedNs", "-1"), ("creditBlockedNs", "1.0") })
        {
            var changed = new Dictionary<string, JsonElement>(original) { [name] = JsonSerializer.Deserialize<JsonElement>(raw) };
            Assert.Throws<IOException>(() => link.Flow.Receive(JsonSerializer.SerializeToElement(changed)), name + ":" + raw);
            Assert.That(link.Flow.Metrics.ReceivedSequence, Is.EqualTo(1));
        }
        Assert.That(link.Flow.Receive(Input(2, 3, 150, 80)), Is.EqualTo(2));
    }

    [Test]
    public async Task StopBeforeAcquiringWriterDoesNotSendALateAcknowledgement()
    {
        await using var link = await Link.Open(blockWriter: true);
        link.Flow.Receive(Input(1)); link.Flow.Consumed(1);
        link.Flow.Stop(); link.Writes.Release();
        await link.Flow.Completion.WaitAsync(TimeSpan.FromSeconds(3));
        Assert.That(link.Flow.Metrics.AcknowledgementFrames, Is.Zero);
        Assert.That(link.Flow.Metrics.AcknowledgedThrough, Is.Zero);
        Assert.That(link.Failures, Is.Empty);
    }
}
