using System.Buffers.Binary;
using System.Diagnostics;
using System.Globalization;
using System.Runtime.InteropServices;
using System.Text.Json;

namespace DualSenseClient.Emulation.Research;

// Compiled only into a separately staged research copy. The normal project
// does not include this file or any call to it. No per-report allocation/I/O.
public static class ReportTrace
{
    private const int Capacity = 131072;
    private struct Entry
    {
        internal long Timestamp, Generation;
        internal uint Tag;
        internal byte Stage, Origin, Sequence8;
    }
    private static readonly Entry[] Entries = new Entry[Capacity];
    private static readonly int[] Committed = new int[Capacity];
    // Delivery events carry no report payload: URB arrival, worker scheduling,
    // completion-gate entry, stale skips and reportless/URB-less gate visits.
    private const int EventCapacity = 262144;
    private struct EventEntry
    {
        internal long Timestamp, Generation, Extra;
        internal byte Kind, Origin;
    }
    private static readonly EventEntry[] Events = new EventEntry[EventCapacity];
    private static readonly int[] EventCommitted = new int[EventCapacity];
    private static int _active, _writers, _next, _overflow, _badSequence, _eventNext, _eventOverflow;
    private static ushort _nonce;
    private static string? _directory;
    private static long _minimumClockBracket, _maximumClockBracket;

    [StructLayout(LayoutKind.Sequential)]
    private struct Timespec { internal long Seconds, Nanoseconds; }
    [DllImport("libc", EntryPoint = "clock_gettime", SetLastError = true)]
    private static extern int ClockGetTime(int clockId, out Timespec timestamp);

    public static void Start(string directory)
    {
        if (_directory is not null || !OperatingSystem.IsLinux())
            throw new InvalidOperationException("One Linux research session per actor required.");
        _nonce = ushort.Parse(Environment.GetEnvironmentVariable("MADSENSE_TRACE_NONCE")
            ?? throw new InvalidOperationException("Explicit trace nonce required."), CultureInfo.InvariantCulture);
        if (_nonce < 0x8000) throw new ArgumentException("Research nonce outside the reserved range.");
        _directory = directory;
        // Allocate/touch all recording storage before the actor connects.
        Array.Clear(Entries);
        Array.Clear(Committed);
        Array.Clear(Events);
        Array.Clear(EventCommitted);
        if (Stopwatch.Frequency != 1_000_000_000) throw new InvalidOperationException("Unexpected Stopwatch frequency.");
        _minimumClockBracket = long.MaxValue;
        for (int i = 0; i < 32; i++)
        {
            long before = Stopwatch.GetTimestamp();
            if (ClockGetTime(1, out var stamp) != 0) throw new InvalidOperationException("CLOCK_MONOTONIC unavailable.");
            long after = Stopwatch.GetTimestamp();
            long native = checked(stamp.Seconds * 1_000_000_000 + stamp.Nanoseconds);
            if (native < before || native > after) throw new InvalidOperationException("Stopwatch and CLOCK_MONOTONIC differ.");
            _minimumClockBracket = Math.Min(_minimumClockBracket, after - before);
            _maximumClockBracket = Math.Max(_maximumClockBracket, after - before);
        }
        Volatile.Write(ref _active, 1);
    }

    public static void Record(byte stage, ReadOnlySpan<byte> report, long generation = 0, byte origin = 0)
    {
        if (Volatile.Read(ref _active) == 0 || report.Length != 64 || report[0] != 1) return;
        uint tag = BinaryPrimitives.ReadUInt32LittleEndian(report.Slice(28, 4));
        if (tag >> 16 != _nonce) return;
        long timestamp = Stopwatch.GetTimestamp();
        Interlocked.Increment(ref _writers);
        try
        {
            if (Volatile.Read(ref _active) == 0) return;
            if ((tag & 0xffff) >= 10000 || report[7] != (byte)tag)
            {
                Interlocked.Increment(ref _badSequence);
                return;
            }
            int slot = Interlocked.Increment(ref _next) - 1;
            if (slot >= Capacity)
            {
                Interlocked.Increment(ref _overflow);
                return;
            }
            Entries[slot] = new Entry { Timestamp = timestamp, Generation = generation,
                Tag = tag, Stage = stage, Origin = origin, Sequence8 = report[7] };
            Volatile.Write(ref Committed[slot], 1);
        }
        finally { Interlocked.Decrement(ref _writers); }
    }

    public static void Event(byte kind, long generation, long extra, byte origin = 0)
    {
        if (Volatile.Read(ref _active) == 0) return;
        long timestamp = Stopwatch.GetTimestamp();
        Interlocked.Increment(ref _writers);
        try
        {
            if (Volatile.Read(ref _active) == 0) return;
            int slot = Interlocked.Increment(ref _eventNext) - 1;
            if (slot >= EventCapacity)
            {
                Interlocked.Increment(ref _eventOverflow);
                return;
            }
            Events[slot] = new EventEntry { Timestamp = timestamp, Generation = generation, Extra = extra, Kind = kind, Origin = origin };
            Volatile.Write(ref EventCommitted[slot], 1);
        }
        finally { Interlocked.Decrement(ref _writers); }
    }

    public static void StopAndSave()
    {
        Volatile.Write(ref _active, 0);
        if (_directory is null) throw new InvalidOperationException("Trace never started.");
        // Called only after bridge disposal. Retain incomplete publication as an
        // error instead of waiting indefinitely or inventing an empty entry.
        if (!SpinWait.SpinUntil(() => Volatile.Read(ref _writers) == 0, TimeSpan.FromSeconds(1)))
            throw new InvalidOperationException("Trace writers did not finish after bridge disposal.");
        int count = Math.Min(Volatile.Read(ref _next), Capacity);
        using (var stream = new StreamWriter(Path.Combine(_directory, "report-trace.csv")))
        {
            stream.WriteLine("slot,ticks,stage,origin,generation,tag,sequence8");
            for (int i = 0; i < count; i++)
            {
                if (Volatile.Read(ref Committed[i]) != 1)
                    throw new InvalidOperationException("Unpublished trace slot after disposal.");
                Entry row = Entries[i];
                stream.WriteLine(string.Create(CultureInfo.InvariantCulture,
                    $"{i},{row.Timestamp},{row.Stage},{row.Origin},{row.Generation},{row.Tag},{row.Sequence8}"));
            }
        }
        int eventCount = Math.Min(Volatile.Read(ref _eventNext), EventCapacity);
        using (var stream = new StreamWriter(Path.Combine(_directory, "event-trace.csv")))
        {
            stream.WriteLine("slot,ticks,kind,origin,generation,extra");
            for (int i = 0; i < eventCount; i++)
            {
                if (Volatile.Read(ref EventCommitted[i]) != 1)
                    throw new InvalidOperationException("Unpublished event slot after disposal.");
                EventEntry row = Events[i];
                stream.WriteLine(string.Create(CultureInfo.InvariantCulture,
                    $"{i},{row.Timestamp},{row.Kind},{row.Origin},{row.Generation},{row.Extra}"));
            }
        }
        File.WriteAllText(Path.Combine(_directory, "report-trace.json"), JsonSerializer.Serialize(new
        {
            schema = 1, kind = "instrumented-research-copy", nonce = _nonce,
            timestampFrequency = Stopwatch.Frequency, capacity = Capacity, recorded = count,
            attempted = Volatile.Read(ref _next), overflow = Volatile.Read(ref _overflow),
            badSequence = Volatile.Read(ref _badSequence), writersFinished = true,
            dumpedAfterBridgeDispose = true, productionBinary = false,
            stages = new { parsedUsb = 1, rawSlot = 2, interruptPayload = 3, sendReturned = 4 },
            origins = new { none = 0, periodic = 1, freshWorker = 2 },
            events = new { urbEnqueued = 5, scheduleRequested = 6, workerStarted = 7, gateEntered = 8,
                skippedStale = 9, noUrbPending = 10, workerExit = 11, blockedNoReport = 12 },
            eventCapacity = EventCapacity, eventsRecorded = eventCount,
            eventsAttempted = Volatile.Read(ref _eventNext), eventOverflow = Volatile.Read(ref _eventOverflow),
            clockValidation = new { nativeClockId = 1, bracketedSamples = 32,
                minimumBracketNs = _minimumClockBracket, maximumBracketNs = _maximumClockBracket },
            clock = "Stopwatch; Linux CLOCK_MONOTONIC; compare recorded frequency before joining C timestamps"
        }) + "\n");
    }
}
