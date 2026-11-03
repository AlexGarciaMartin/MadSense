using System.Diagnostics;
using System.Security.Cryptography;
using System.Text.Json;

namespace DualSenseClient.Emulation.Research;

// Compiled only into the separately hashed PCM research source copy.
// Append-only storage: the ISO callback never waits, allocates, or performs I/O.
public sealed class PcmIngressRecorder
{
    public readonly record struct Packet(int Offset, int Length, long Timestamp);
    public readonly record struct Boundary(int Packets, int Bytes, long Timestamp);
    readonly byte[] bytes;
    readonly Packet[] packets;
    int writing, stopped, published, activeCalls;
    long attempts, capacityRejected, concurrentRejected, malformedRejected;

    public PcmIngressRecorder(int byteCapacity, int packetCapacity)
    {
        if (byteCapacity < 8 || packetCapacity < 1) throw new ArgumentOutOfRangeException();
        bytes = new byte[byteCapacity]; packets = new Packet[packetCapacity];
        bytes.AsSpan().Clear(); packets.AsSpan().Clear(); // Touch storage before bridge startup.
    }

    public bool TryCapture(ReadOnlySpan<byte> value, long timestamp)
    {
        if (Volatile.Read(ref stopped) != 0) return false;
        Interlocked.Increment(ref activeCalls);
        try
        {
            if (Volatile.Read(ref stopped) != 0) return false;
            Interlocked.Increment(ref attempts);
            if (Interlocked.CompareExchange(ref writing, 1, 0) != 0)
            { Interlocked.Increment(ref concurrentRejected); return false; }
            try
            {
                if (value.Length == 0 || value.Length % 8 != 0)
                { Interlocked.Increment(ref malformedRejected); return false; }
                int index = published;
                int offset = index == 0 ? 0 : packets[index - 1].Offset + packets[index - 1].Length;
                if (index == packets.Length || value.Length > bytes.Length - offset)
                { Interlocked.Increment(ref capacityRejected); return false; }
                value.CopyTo(bytes.AsSpan(offset));
                packets[index] = new(offset, value.Length, timestamp);
                Volatile.Write(ref published, index + 1);
                return true;
            }
            finally { Volatile.Write(ref writing, 0); }
        }
        finally { Interlocked.Decrement(ref activeCalls); }
    }

    public Boundary Mark()
    {
        int count = Volatile.Read(ref published);
        return new(count, count == 0 ? 0 : packets[count - 1].Offset + packets[count - 1].Length,
            Stopwatch.GetTimestamp());
    }

    public void Stop()
    {
        Volatile.Write(ref stopped, 1);
        if (!SpinWait.SpinUntil(() => Volatile.Read(ref activeCalls) == 0, TimeSpan.FromSeconds(2)))
            throw new TimeoutException("PCM observer writer did not stop; capture is invalid.");
    }

    public (byte[] Bytes, Packet[] Packets, long Attempts, long CapacityRejected, long ConcurrentRejected, long MalformedRejected) Snapshot()
    {
        if (Volatile.Read(ref stopped) == 0 || Volatile.Read(ref writing) != 0)
            throw new InvalidOperationException("Stop the observer before reading its storage.");
        var boundary = Mark();
        return (bytes.AsSpan(0, boundary.Bytes).ToArray(), packets.AsSpan(0, boundary.Packets).ToArray(),
            Interlocked.Read(ref attempts), Interlocked.Read(ref capacityRejected),
            Interlocked.Read(ref concurrentRejected), Interlocked.Read(ref malformedRejected));
    }
}

public static class ExportPcmCapture
{
    static readonly JsonSerializerOptions Json = new() { PropertyNamingPolicy = JsonNamingPolicy.CamelCase };
    static PcmIngressRecorder? recorder;
    public static void Start()
    {
        if (recorder is not null) throw new InvalidOperationException("Only one PCM research capture per actor.");
        recorder = new(16 * 1024 * 1024, 65536);
    }
    public static void Observe(ReadOnlySpan<byte> packet) => recorder?.TryCapture(packet, Stopwatch.GetTimestamp());
    public static PcmIngressRecorder.Boundary Mark() => (recorder ?? throw new InvalidOperationException("PCM capture not started.")).Mark();

    // Called after the real controller has stopped; the actor owns this directory.
    public static object StopAndWrite(string output)
    {
        var active = recorder ?? throw new InvalidOperationException("PCM capture not started.");
        active.Stop(); var value = active.Snapshot();
        List<object> chunks = [];
        for (int offset = 0, index = 0; offset < value.Bytes.Length; index++)
        {
            int length = Math.Min(4 * 1024 * 1024, value.Bytes.Length - offset);
            string name = $"pcm-ingress-{index:D3}.bin";
            byte[] part = value.Bytes.AsSpan(offset, length).ToArray();
            File.WriteAllBytes(Path.Combine(output, name), part);
            chunks.Add(new { name, offset, length, sha256 = Convert.ToHexString(SHA256.HashData(part)).ToLowerInvariant() });
            offset += length;
        }
        var result = new
        {
            schema = 1, scope = "research-copy-usb-quad-ingress", productionBinary = false,
            timestampFrequency = Stopwatch.Frequency, byteCapacity = 16 * 1024 * 1024, packetCapacity = 65536,
            bytes = value.Bytes.Length, packets = value.Packets, attempts = value.Attempts,
            capacityRejected = value.CapacityRejected, concurrentRejected = value.ConcurrentRejected,
            malformedRejected = value.MalformedRejected, chunks,
            clean = value.Attempts == value.Packets.Length && value.CapacityRejected == 0
                && value.ConcurrentRejected == 0 && value.MalformedRejected == 0,
            physicalAudioValidated = false
        };
        File.WriteAllText(Path.Combine(output, "pcm-ingress.json"), JsonSerializer.Serialize(result, Json) + "\n");
        return result;
    }
}
