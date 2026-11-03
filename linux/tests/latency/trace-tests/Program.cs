using System.Buffers.Binary;
using System.Text.Json;
using DualSenseClient.Emulation.Research;

if (!OperatingSystem.IsLinux()) throw new InvalidOperationException("Native Linux recorder checks required.");
if (args.Length != 1 || args[0] is not ("concurrent" or "overflow")) throw new ArgumentException("Explicit test case required.");
string directory = Path.Combine(Path.GetTempPath(), "madsense-trace-test-" + Guid.NewGuid().ToString("N"));
Directory.CreateDirectory(directory);
try
{
    Environment.SetEnvironmentVariable("MADSENSE_TRACE_NONCE", "33059");
    ReportTrace.Start(directory);
    int expected = args[0] == "concurrent" ? 4000 : 131072;
    if (args[0] == "concurrent")
    {
        Parallel.For(0, 1000, sequence =>
        {
            byte[] report = new byte[64];
            report[0] = 1;
            report[7] = (byte)sequence;
            BinaryPrimitives.WriteUInt32LittleEndian(report.AsSpan(28), (33059u << 16) | (uint)sequence);
            for (byte stage = 1; stage <= 4; stage++)
                ReportTrace.Record(stage, report, stage >= 3 ? sequence + 1 : 0, stage >= 3 ? (byte)2 : (byte)0);
        });
    }
    else
    {
        byte[] report = new byte[64]; report[0] = 1;
        BinaryPrimitives.WriteUInt32LittleEndian(report.AsSpan(28), 33059u << 16);
        for (int i = 0; i <= expected; i++) ReportTrace.Record(1, report);
    }
    ReportTrace.StopAndSave();
    using var meta = JsonDocument.Parse(File.ReadAllText(Path.Combine(directory, "report-trace.json")));
    var value = meta.RootElement;
    if (value.GetProperty("recorded").GetInt32() != expected ||
        value.GetProperty("overflow").GetInt32() != (args[0] == "overflow" ? 1 : 0) ||
        value.GetProperty("badSequence").GetInt32() != 0 ||
        !value.GetProperty("writersFinished").GetBoolean())
        throw new Exception("Bounded concurrent recording counters differ.");
    string[] rows = File.ReadAllLines(Path.Combine(directory, "report-trace.csv"));
    if (rows.Length != expected + 1) throw new Exception("Lost publication.");
    var tags = new Dictionary<uint, HashSet<int>>();
    for (int i = 1; i < rows.Length; i++)
    {
        string[] columns = rows[i].Split(',');
        if (int.Parse(columns[0]) != i - 1 || long.Parse(columns[1]) <= 0)
            throw new Exception("Unpublished or repeated slot.");
        uint tag = uint.Parse(columns[5]);
        if (!tags.TryGetValue(tag, out var stages)) tags[tag] = stages = [];
        if (args[0] == "concurrent" && !stages.Add(int.Parse(columns[2])))
            throw new Exception("Duplicated tag/stage under concurrency.");
    }
    if (args[0] == "concurrent" && (tags.Count != 1000 || tags.Values.Any(stages => stages.Count != 4)))
        throw new Exception("Concurrent stage/tag mismatch.");
    Console.WriteLine(JsonSerializer.Serialize(new { result = "passed", test = args[0], recorded = expected }));
}
finally { Directory.Delete(directory, recursive: true); }
