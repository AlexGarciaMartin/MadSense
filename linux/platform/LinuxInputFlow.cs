using System.Buffers.Text;
using System.Diagnostics;
using System.Text;
using System.Text.Json;

namespace MadSense.Platform;

public sealed record LinuxInputFlowMetrics(int Version, int Window, long ReceivedSequence, long ConsumedThrough,
    long AcknowledgedThrough, long AcknowledgementFrames, long CreditBlockedCount, long CreditBlockedNs,
    long CreditMaxBlockedNs, bool KernelBacklogMeasured = false);

/// <summary>One bounded cumulative ACK writer. Input/RPC readers never perform its socket writes.</summary>
internal sealed class LinuxInputFlow
{
    internal const int Version = 1, Window = 8;
    readonly object gate = new();
    readonly AutoResetEvent wake = new(false);
    readonly Stream stream;
    readonly SemaphoreSlim writes;
    readonly CancellationToken cancellation;
    readonly Action<string> fail;
    readonly byte[] frame = new byte[160];
    readonly int prefixLength;
    readonly string leaseId;
    bool active = true;
    byte completed;
    long received, consumed, sent, frames, blockedCount, blockedNs, maxBlockedNs;
    internal Task Completion { get; }

    internal static void RequireCapability(JsonElement result)
    {
        if (result.ValueKind != JsonValueKind.Object || !result.TryGetProperty("inputFlow", out var flow) || flow.ValueKind != JsonValueKind.Object ||
            !flow.TryGetProperty("version", out var version) || version.ValueKind != JsonValueKind.Number || !version.TryGetInt32(out int v) || v != Version ||
            !flow.TryGetProperty("window", out var window) || window.ValueKind != JsonValueKind.Number || !window.TryGetInt32(out int w) || w != Window)
            throw new IOException("Update the MadSense system helper: input flow-control version 1 with an eight-report window is required.");
    }

    internal LinuxInputFlow(JsonElement result, Stream stream, SemaphoreSlim writes, CancellationToken cancellation, Action<string> fail)
    {
        RequireCapability(result);
        JsonElement description = result.GetProperty("inputFlow");
        if (!description.TryGetProperty("leaseId", out var identity) || identity.ValueKind != JsonValueKind.String)
            throw new IOException("Missing input flow-control lease identity.");
        leaseId = identity.GetString() ?? "";
        if (!Guid.TryParseExact(leaseId, "N", out var id) || id.ToString("N") != leaseId)
            throw new IOException("Invalid input flow-control lease identity.");
        this.stream = stream; this.writes = writes; this.cancellation = cancellation; this.fail = fail;
        prefixLength = Encoding.UTF8.GetBytes("{\"id\":0,\"op\":\"input_ack\",\"leaseId\":\"" + leaseId + "\",\"through\":", frame);
        Completion = Task.Factory.StartNew(Pump, CancellationToken.None, TaskCreationOptions.LongRunning, TaskScheduler.Default);
    }

    internal long Receive(JsonElement message)
    {
        long sequence = Positive(message, "sequence"), count = Nonnegative(message, "creditBlockedCount"),
            duration = Nonnegative(message, "creditBlockedNs"), maximum = Nonnegative(message, "creditMaxBlockedNs");
        if (!message.TryGetProperty("leaseId", out var identity) || identity.ValueKind != JsonValueKind.String || identity.GetString() != leaseId)
            throw new IOException("Input belongs to a different broker lease.");
        lock (gate)
        {
            if (received == long.MaxValue || sequence != received + 1 || sequence - consumed > Window ||
                count < blockedCount || duration < blockedNs || maximum < maxBlockedNs || maximum > duration)
                throw new IOException("Invalid input sequence, credit window or backpressure counters.");
            received = sequence; blockedCount = count; blockedNs = duration; maxBlockedNs = maximum;
            return sequence;
        }
    }

    static long Nonnegative(JsonElement value, string name)
    {
        if (!value.TryGetProperty(name, out var field) || field.ValueKind != JsonValueKind.Number ||
            !field.TryGetInt64(out long number) || number < 0)
            throw new IOException("Invalid input flow-control counter: " + name);
        return number;
    }
    static long Positive(JsonElement value, string name)
    {
        long number = Nonnegative(value, name);
        return number > 0 ? number : throw new IOException("Invalid input sequence.");
    }

    internal void Consumed(long sequence)
    {
        lock (gate)
        {
            if (!active) return;
            long distance = sequence - consumed;
            if (sequence <= consumed || distance is < 1 or > Window || sequence > received || (completed & (1 << ((int)distance - 1))) != 0)
                throw new IOException("Input consumption cannot acknowledge an unknown or repeated report.");
            completed |= (byte)(1 << ((int)distance - 1));
            long previous = consumed;
            while ((completed & 1) != 0) { ++consumed; completed >>= 1; }
            if (consumed != previous) wake.Set();
        }
    }

    internal LinuxInputFlowMetrics Metrics
    {
        get { lock (gate) return new(Version, Window, received, consumed, sent, frames, blockedCount, blockedNs, maxBlockedNs); }
    }

    internal void Stop()
    {
        lock (gate)
        {
            if (!active) return;
            active = false;
            wake.Set();
        }
    }

    void Pump()
    {
        try
        {
            while (true)
            {
                wake.WaitOne();
                while (true)
                {
                    lock (gate) { if (!active) return; if (consumed == sent) break; }
                    long start = Stopwatch.GetTimestamp();
                    if (!writes.Wait(TimeSpan.FromSeconds(5), cancellation))
                        throw new IOException("Input credit acknowledgement could not acquire the broker writer within five seconds.");
                    try
                    {
                        long through;
                        lock (gate) { if (!active) return; through = consumed; }
                        int remaining = Math.Max(0, 5000 - (int)Stopwatch.GetElapsedTime(start).TotalMilliseconds);
                        if (remaining == 0) throw new IOException("Input credit acknowledgement write deadline expired.");
                        if (!Utf8Formatter.TryFormat(through, frame.AsSpan(prefixLength), out int digits))
                            throw new IOException("Input credit acknowledgement exceeds its fixed frame buffer.");
                        frame[prefixLength + digits] = (byte)'}'; frame[prefixLength + digits + 1] = (byte)'\n';
                        if (stream.CanTimeout) stream.WriteTimeout = remaining;
                        stream.Write(frame, 0, prefixLength + digits + 2); stream.Flush();
                        lock (gate) { sent = through; ++frames; }
                    }
                    finally { writes.Release(); }
                }
            }
        }
        catch (Exception error)
        {
            bool report;
            lock (gate) report = active && !cancellation.IsCancellationRequested;
            if (report) fail("Input credit acknowledgement failed: " + error.Message);
        }
        finally
        {
            lock (gate) { active = false; wake.Dispose(); }
        }
    }
}
