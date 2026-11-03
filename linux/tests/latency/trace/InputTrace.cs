using System.Buffers.Binary;
using System.Diagnostics;
using System.Globalization;
using System.Runtime.InteropServices;
using System.Text.Json;

namespace MadSense.Linux.Research;

// Research copy only: ~256 KiB of fixed storage, no per-event allocation/I/O.
// Publication never waits for a slot; a collision makes the evidence invalid.
public static class InputTrace
{
    private const int Capacity = 4096;
    private struct Entry
    {
        internal long Sequence, Timestamp, BrokerReadNs, Received, ReadStarted;
        internal uint Tag;
        internal int QueueCount, Thread;
        internal byte Stage, Sequence8;
    }
    private static readonly Entry[] Entries = new Entry[Capacity];
    private static readonly int[] Busy = new int[Capacity];
    private static int _active, _writers, _collisions, _badSequence;
    private static long _next, _lastReadStarted, _minimumBracket, _maximumBracket;
    private static ushort _nonce;
    private static string? _directory;
    private static object? _gcBefore;

    [StructLayout(LayoutKind.Sequential)]
    private struct Timespec { internal long Seconds, Nanoseconds; }
    [DllImport("libc", EntryPoint = "clock_gettime", SetLastError = true)]
    private static extern int ClockGetTime(int clockId, out Timespec timestamp);

    private static object GcSnapshot() => new
    {
        collectionCounts = new[] { GC.CollectionCount(0), GC.CollectionCount(1), GC.CollectionCount(2) },
        totalAllocatedBytes = GC.GetTotalAllocatedBytes(precise: false),
        lastCollectionIndex = GC.GetGCMemoryInfo().Index
    };

    public static void Start(string directory)
    {
        if (_directory is not null || !OperatingSystem.IsLinux())
            throw new InvalidOperationException("One Linux research session per actor required.");
        _nonce = ushort.Parse(Environment.GetEnvironmentVariable("MADSENSE_TRACE_NONCE")
            ?? throw new InvalidOperationException("Explicit trace nonce required."), CultureInfo.InvariantCulture);
        if (_nonce < 0x8000) throw new ArgumentException("Research nonce outside the reserved range.");
        _directory = directory;
        Array.Clear(Entries);
        Array.Clear(Busy);
        // Sequence zero must be distinguishable from an unpublished slot.
        for (int i = 0; i < Capacity; i++) Entries[i].Sequence = -1;
        if (Stopwatch.Frequency != 1_000_000_000) throw new InvalidOperationException("Unexpected Stopwatch frequency.");
        _minimumBracket = long.MaxValue;
        for (int i = 0; i < 32; i++)
        {
            long before = Stopwatch.GetTimestamp();
            if (ClockGetTime(1, out var stamp) != 0) throw new InvalidOperationException("CLOCK_MONOTONIC unavailable.");
            long after = Stopwatch.GetTimestamp();
            long native = checked(stamp.Seconds * 1_000_000_000 + stamp.Nanoseconds);
            if (native < before || native > after) throw new InvalidOperationException("Stopwatch and CLOCK_MONOTONIC differ.");
            _minimumBracket = Math.Min(_minimumBracket, after - before);
            _maximumBracket = Math.Max(_maximumBracket, after - before);
        }
        _gcBefore = GcSnapshot();
        Volatile.Write(ref _active, 1);
    }

    public static void ReadStarting()
    {
        if (Volatile.Read(ref _active) != 0)
            Interlocked.Exchange(ref _lastReadStarted, Stopwatch.GetTimestamp());
    }

    public static void Record(byte stage, ReadOnlySpan<byte> report, long received, long brokerReadNs, int queueCount)
    {
        if (Volatile.Read(ref _active) == 0 || report.Length != 78 || report[0] != 0x31) return;
        uint tag = BinaryPrimitives.ReadUInt32LittleEndian(report.Slice(29, 4));
        if (tag >> 16 != _nonce) return;
        Interlocked.Increment(ref _writers);
        try
        {
            if (Volatile.Read(ref _active) == 0) return;
            if ((tag & 0xffff) >= 10000 || report[8] != (byte)tag || stage is < 1 or > 4)
            {
                Interlocked.Increment(ref _badSequence);
                return;
            }
            long readStarted = Interlocked.Read(ref _lastReadStarted);
            long timestamp = Stopwatch.GetTimestamp();
            long sequence = Interlocked.Increment(ref _next) - 1;
            int slot = (int)(sequence % Capacity);
            if (Interlocked.CompareExchange(ref Busy[slot], 1, 0) != 0)
            {
                Interlocked.Increment(ref _collisions);
                return;
            }
            try
            {
                // A descheduled old writer must not overwrite a newer wrap.
                if (Entries[slot].Sequence > sequence)
                {
                    Interlocked.Increment(ref _collisions);
                    return;
                }
                Entries[slot] = new Entry { Sequence = sequence, Timestamp = timestamp,
                    BrokerReadNs = brokerReadNs, Received = received, ReadStarted = readStarted,
                    Tag = tag, QueueCount = queueCount, Thread = Environment.CurrentManagedThreadId,
                    Stage = stage, Sequence8 = report[8] };
            }
            finally { Volatile.Write(ref Busy[slot], 0); }
        }
        finally { Interlocked.Decrement(ref _writers); }
    }

    public static void StopAndSave()
    {
        Volatile.Write(ref _active, 0);
        if (_directory is null) throw new InvalidOperationException("Trace never started.");
        bool finished = SpinWait.SpinUntil(() => Volatile.Read(ref _writers) == 0, TimeSpan.FromSeconds(1));
        object gcAfter = GcSnapshot();
        long total = Interlocked.Read(ref _next), first = Math.Max(0, total - Capacity);
        int retained = 0, holes = 0;
        using (var stream = new StreamWriter(Path.Combine(_directory, "input-trace.csv")))
        {
            stream.WriteLine("slot,ticks,stage,tag,sequence8,broker_read_ns,received_ns,queue_count,last_read_started_ns,thread");
            // Do not read a struct while a live writer may still modify it.
            if (finished)
                for (long i = first; i < total; i++)
                {
                    Entry row = Entries[(int)(i % Capacity)];
                    if (row.Sequence != i) { holes++; continue; }
                    retained++;
                    stream.WriteLine(string.Create(CultureInfo.InvariantCulture,
                        $"{i},{row.Timestamp},{row.Stage},{row.Tag},{row.Sequence8},{row.BrokerReadNs},{row.Received},{row.QueueCount},{row.ReadStarted},{row.Thread}"));
                }
        }
        File.WriteAllText(Path.Combine(_directory, "input-trace.json"), JsonSerializer.Serialize(new
        {
            schema = 1, kind = "client-input-tail", nonce = _nonce, timestampFrequency = Stopwatch.Frequency,
            capacity = Capacity, totalAttempts = total, retained, overwritten = first, tailStart = first, tailEnd = total,
            holes, collisions = Volatile.Read(ref _collisions), badSequence = Volatile.Read(ref _badSequence),
            writersFinished = finished, dumpedAfterBridgeDispose = true, productionBinary = false,
            stages = new { accept = 1, enqueue = 2, enqueueFailed = 3, dequeue = 4 },
            queueCountSemantics = "unsynchronized Channel.Reader.Count snapshot; not the occupancy at TryWrite",
            dequeueSemantics = "removed before EnsureConnected and Copy; not proof of delivery or parsing",
            readStartedSemantics = "latest synchronous Read after validation/BeginReading, before wait; not a scheduling or work-duration measurement",
            clockValidation = new { nativeClockId = 1, bracketedSamples = 32,
                minimumBracketNs = _minimumBracket, maximumBracketNs = _maximumBracket },
            gcBefore = _gcBefore, gcAfter, gcPauseAttribution = false
        }) + "\n");
    }
}
