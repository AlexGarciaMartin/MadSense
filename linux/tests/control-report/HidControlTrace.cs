// Included only in an explicitly instrumented source-copy build.
using System.Diagnostics;
using System.Text.Json;
using System.Threading.Channels;
using DualSenseClient.Emulation.Core;

namespace DualSenseClient.Emulation.Research;

public static class HidControlTrace
{
    sealed record Row(long MonotonicTicks, UsbSetupPacket Setup, byte[] OutData);
    static readonly Channel<Row> Queue = Channel.CreateBounded<Row>(new BoundedChannelOptions(128)
    { FullMode = BoundedChannelFullMode.Wait, SingleReader = true, SingleWriter = false });
    static Task? worker;
    static string? directory, error;
    static int admitted, rejected, written;
    static readonly JsonSerializerOptions Json = new() { PropertyNamingPolicy = JsonNamingPolicy.CamelCase };
    public static void Start(string output)
    {
        if (worker is not null) throw new InvalidOperationException("A fresh control observer process is required.");
        directory = Path.GetFullPath(output);
        worker = Task.Run(async () =>
        {
            try
            {
                using var stream = new StreamWriter(new FileStream(Path.Combine(directory, "hid-control-trace.jsonl"),
                    FileMode.CreateNew, FileAccess.Write, FileShare.Read));
                await foreach (Row row in Queue.Reader.ReadAllAsync())
                {
                    var setup = row.Setup;
                    stream.WriteLine(JsonSerializer.Serialize(new { monotonicTicks = row.MonotonicTicks,
                        setup = new { setup.RequestType, setup.Request, setup.Value, setup.Index, setup.Length },
                        outDataHex = Convert.ToHexString(row.OutData), outDataLength = row.OutData.Length }, Json));
                    stream.Flush();
                    Interlocked.Increment(ref written);
                }
            }
            catch (Exception failure) { error = failure.ToString(); }
        });
    }
    public static void Record(in UsbSetupPacket setup, ReadOnlySpan<byte> outData)
    {
        // Only GET_REPORT/SET_REPORT at the HID class boundary; this hook does
        // not mutate any request, response, report or dispatch order.
        if (worker is null || setup.Request is not (1 or 9)) return;
        long timestamp = Stopwatch.GetTimestamp();
        if (outData.Length > 256 || Interlocked.Increment(ref admitted) > 256)
        { Interlocked.Increment(ref rejected); return; }
        if (!Queue.Writer.TryWrite(new(timestamp, setup, outData.ToArray())))
            Interlocked.Increment(ref rejected);
    }
    public static void StopAndSave()
    {
        Queue.Writer.TryComplete();
        if (worker is null || directory is null) return;
        if (!worker.Wait(TimeSpan.FromSeconds(3))) error ??= "Control observer did not drain within three seconds.";
        File.WriteAllText(Path.Combine(directory, "hid-control-summary.json"), JsonSerializer.Serialize(Snapshot(), Json)+"\n");
    }
    public static bool Complete => worker?.IsCompleted == true && error is null && rejected == 0 && admitted == written;
    public static object Snapshot() => new { sourceCopyOnly = true, latencyValidated = false,
        timestampFrequency = Stopwatch.Frequency, admitted, rejected, written, error, completed = Complete };
}
