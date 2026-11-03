using System.Buffers.Binary;
using System.Text.Json;
using DualSenseClient.Controllers.DualSense.Audio;
using DualSenseClient.Controllers.DualSense.Output;

if (args.Length != 1) throw new ArgumentException("Expected a fresh output directory");
string directory = Path.GetFullPath(args[0]);
if (Directory.Exists(directory)) throw new IOException("Fresh output directory required");
Directory.CreateDirectory(directory);
using var trace = new StreamWriter(Path.Combine(directory, "uhid-format-events.jsonl"));
using var sourceFile = File.Create(Path.Combine(directory, "speaker-source-512.pcm"));
using var encoderFile = File.Create(Path.Combine(directory, "encoder-input-480.pcm"));
using var rearFile = File.Create(Path.Combine(directory, "rear-source-48000.pcm"));
using var pipeline = new DualSenseBtAudioPipeline();
var builder = new BluetoothAudioReportBuilder();
var native = new NativeHapticsQueue();
var state = new SetStateData();
long counter = 0;
void Capture(byte[] report) => trace.WriteLine(JsonSerializer.Serialize(new {
    kind = "output", monotonicNs = ++counter, timestampKind = "synthetic-order-only", hex = Convert.ToHexString(report)
}));
void WritePcm(Stream target, ReadOnlySpan<short> samples) {
    byte[] bytes = new byte[samples.Length * 2];
    for (int i = 0; i < samples.Length; i++) BinaryPrimitives.WriteInt16LittleEndian(bytes.AsSpan(i * 2), samples[i]);
    target.Write(bytes);
}
Capture(builder.BuildInitPrime(state));
const int preroll = 8, active = 96, tail = 8;
short[] source = new short[1024], encoder = new short[960];
byte[] opus = new byte[200], haptics = new byte[64], rear = new byte[2048];
double leftPhase = 0, rightPhase = 0;
uint random = 0x31a2c004;
for (int block = 0; block < preroll + active + tail; block++) {
    source.AsSpan().Clear(); rear.AsSpan().Clear();
    if (block >= preroll && block < preroll + active) {
        for (int frame = 0; frame < 512; frame++) {
            int position = (block - preroll) * 512 + frame;
            // Different slowly changing tones make channel swaps and ordering
            // errors observable without a white-noise codec stress test.
            leftPhase += 2 * Math.PI * (250 + position * 0.011) / 48000;
            rightPhase += 2 * Math.PI * (1100 - position * 0.009) / 48000;
            source[frame * 2] = (short)(10000 * Math.Sin(leftPhase) + 2000 * Math.Sin(leftPhase * 2.17));
            source[frame * 2 + 1] = (short)(14000 * Math.Sin(rightPhase) + 1500 * Math.Sin(rightPhase * 1.71));
            // Independent signed markers, held for each 16-frame averaging
            // window, create unique exact haptics blocks after quantization.
            if (frame % 16 == 0) {
                random = unchecked(random * 1664525 + 1013904223);
                short left = (short)(((int)(random >> 16) % 201 - 100) * 256);
                random = unchecked(random * 1664525 + 1013904223);
                short right = (short)(((int)(random >> 16) % 201 - 100) * 256);
                for (int offset = frame; offset < frame + 16; offset++) {
                    BinaryPrimitives.WriteInt16LittleEndian(rear.AsSpan(offset * 4), left);
                    BinaryPrimitives.WriteInt16LittleEndian(rear.AsSpan(offset * 4 + 2), right);
                }
            }
        }
    }
    WritePcm(sourceFile, source);
    // Deliberately fragmented USB-like chunks test the production continuous
    // native queue rather than a duplicate decimator in this actor.
    for (int offset = 0; offset < rear.Length; offset += 192)
        native.Feed(rear.AsSpan(offset, Math.Min(192, rear.Length - offset)));
    rearFile.Write(rear);
    native.Read(haptics);
    DualSenseBtAudioPipeline.ResampleToOpusBlock(source, encoder);
    WritePcm(encoderFile, encoder);
    pipeline.EncodeOpus(encoder, opus);
    Capture(builder.BuildCombinedReport(state, opus, haptics, BluetoothAudioRoute.Speaker));
}
File.WriteAllText(Path.Combine(directory, "scope.json"), JsonSerializer.Serialize(new {
    scope = "offline-production-pipeline-only", physicalDevices = false, radioInitialized = false,
    applicationControllerExercised = false, uhidExercised = false, timestampsAreLatencyEvidence = false,
    blocks = preroll + active + tail, preroll, active, tail,
    productionClasses = new[] { nameof(DualSenseBtAudioPipeline), nameof(NativeHapticsQueue), nameof(BluetoothAudioReportBuilder) }
}, new JsonSerializerOptions { WriteIndented = true }));
