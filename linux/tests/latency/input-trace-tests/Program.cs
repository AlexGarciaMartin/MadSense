using System.Buffers.Binary;
using System.Diagnostics;
using System.Reflection;
using System.Text.Json;
using MadSense.Linux.Research;

if (!OperatingSystem.IsLinux()) throw new InvalidOperationException("Native Linux recorder checks required.");
if (args.Length != 1 || args[0] is not ("concurrent" or "tail" or "collision"))
    throw new ArgumentException("Explicit test case required.");
string directory = Path.Combine(Path.GetTempPath(), "madsense-input-trace-" + Guid.NewGuid().ToString("N"));
Directory.CreateDirectory(directory);
try
{
    Environment.SetEnvironmentVariable("MADSENSE_TRACE_NONCE", "33059");
    InputTrace.Start(directory);
    byte[] Report(int sequence)
    {
        byte[] report = new byte[78]; report[0] = 0x31; report[8] = (byte)sequence;
        BinaryPrimitives.WriteUInt32LittleEndian(report.AsSpan(29), (33059u << 16) | (uint)sequence);
        return report;
    }
    void Record(int sequence)
    {
        byte[] report = Report(sequence);
        long before = Stopwatch.GetTimestamp();
        InputTrace.ReadStarting();
        // Publish before the enqueue hook to exercise that legitimate ordering.
        InputTrace.Record(1, report, before, before, 0);
        InputTrace.Record(4, report, before, before, 0);
        InputTrace.Record(2, report, before, before, 0);
    }
    int total;
    if (args[0] == "concurrent") { Parallel.For(0, 1000, Record); total = 3000; }
    else if (args[0] == "tail") { for (int i = 0; i < 8000; i++) Record(i); total = 24000; }
    else
    {
        // Hold exactly the first slot, without changing the recorder's public API.
        var field = typeof(InputTrace).GetField("Busy", BindingFlags.NonPublic | BindingFlags.Static)!;
        int[] busy = (int[])field.GetValue(null)!;
        busy[0] = 1;
        long before = Stopwatch.GetTimestamp();
        InputTrace.Record(1, Report(0), before, before, 0);
        busy[0] = 0;
        total = 1;
    }
    InputTrace.StopAndSave();
    using var document = JsonDocument.Parse(File.ReadAllText(Path.Combine(directory, "input-trace.json")));
    var meta = document.RootElement;
    bool collision = args[0] == "collision";
    int first = Math.Max(0, total - 4096), retained = collision ? 0 : total - first;
    foreach (var expected in new Dictionary<string, int> { ["totalAttempts"] = total, ["retained"] = retained,
        ["overwritten"] = first, ["tailStart"] = first, ["tailEnd"] = total,
        ["collisions"] = collision ? 1 : 0, ["holes"] = collision ? 1 : 0, ["badSequence"] = 0 })
        if (meta.GetProperty(expected.Key).GetInt32() != expected.Value)
            throw new Exception("Incorrect publication metadata: " + expected.Key);
    if (!meta.GetProperty("writersFinished").GetBoolean()) throw new Exception("Writer still alive.");
    var rows = File.ReadAllLines(Path.Combine(directory, "input-trace.csv"));
    if (rows.Length != retained + 1) throw new Exception("Incomplete tail.");
    var seen = new HashSet<(uint, int)>();
    for (int i = 1; i < rows.Length; i++)
    {
        string[] cells = rows[i].Split(',');
        uint tag = uint.Parse(cells[3]);
        if (long.Parse(cells[0]) != first + i - 1 || long.Parse(cells[1]) <= 0
            || tag >> 16 != 33059 || byte.Parse(cells[4]) != (byte)tag
            || !seen.Add((tag, int.Parse(cells[2])))) throw new Exception("Unpublished/duplicate/torn tail row.");
    }
    Console.WriteLine(JsonSerializer.Serialize(new { result = "passed", test = args[0], total, retained, overwritten = first }));
}
finally { Directory.Delete(directory, recursive: true); }
