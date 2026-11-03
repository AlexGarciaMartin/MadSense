using System.Diagnostics;
using System.Buffers.Binary;
using System.Net;
using System.Security.Cryptography;
using System.Text.Json;
using DualSenseClient.Emulation.Core;
using DualSenseClient.Emulation.Devices.DualSense;
using DualSenseClient.Emulation.Devices.Xbox360;
using DualSenseClient.Emulation.UsbIp;

// Exports synthetic neutral personas only. This project deliberately does not reference
// HIDAPI, Controllers, Bluetooth, a platform attach helper or any physical-device API.
Dictionary<string, string> options = new(StringComparer.Ordinal);
for (int i = 0; i < args.Length; i += 2)
{
    if (i + 1 == args.Length || !args[i].StartsWith("--")) throw new ArgumentException("Expected --name value pairs.");
    options.Add(args[i], args[i + 1]);
}
if (options.Keys.Any(key => key is not ("--persona" or "--output" or "--seconds")))
    throw new ArgumentException("Unknown probe option.");
string persona = options.GetValueOrDefault("--persona", "dualsense");
if (persona is not ("dualsense" or "edge" or "xbox")) throw new ArgumentException("Unknown persona.");
string output = Path.GetFullPath(options["--output"]);
int seconds = int.Parse(options.GetValueOrDefault("--seconds", "120"));
if (seconds is < 5 or > 300) throw new ArgumentOutOfRangeException("--seconds");
Directory.CreateDirectory(output);
if (File.Exists(Path.Combine(output, "ready.json"))) throw new IOException("Output directory already contains a probe run.");
JsonSerializerOptions json = new() { PropertyNamingPolicy = JsonNamingPolicy.CamelCase, WriteIndented = false };
void Atomic(string name, object value)
{
    string path = Path.Combine(output, name);
    File.WriteAllText(path + ".tmp", JsonSerializer.Serialize(value, json) + "\n");
    File.Move(path + ".tmp", path, true);
}
using Capture capture = new(output, json);
string serial = "MadSense-Probe-" + Guid.NewGuid().ToString("N");
byte[] address = [0x02, 0xD5, .. RandomNumberGenerator.GetBytes(4)];
VirtualUsbDevice device;
if (persona == "xbox")
{
    Xbox360Device xbox = new(new Xbox360Identity { SerialNumber = serial }, capture);
    xbox.OutputReportReceived += report => capture.Event("xbox-output", report);
    device = xbox;
}
else
{
    DualSenseDevice pad = new(new DualSenseIdentity { SerialNumber = serial, MacAddress = address }, persona == "edge", capture);
    pad.OutputReportReceived += report => capture.Event("hid-output", report);
    pad.AudioOutReceived += capture.Audio;
    pad.RealtimeHapticsReceived += capture.Haptics;
    pad.SpeakerResetReceived += () => capture.Event("speaker-reset", new { alternate = pad.GetAlternateSetting(1) });
    device = new ProbePersona(pad, capture);
}
File.WriteAllBytes(Path.Combine(output, "device-descriptor.bin"), device.Descriptors.DeviceDescriptor.ToArray());
File.WriteAllBytes(Path.Combine(output, "configuration-descriptor.bin"), device.Descriptors.ConfigDescriptor.ToArray());
if (device.Descriptors.HidReportDescriptor is { } descriptor)
    File.WriteAllBytes(Path.Combine(output, "hid-descriptor.bin"), descriptor.ToArray());
Dictionary<string, string> features = [];
if (persona != "xbox")
{
    foreach ((byte id, ushort length) in new (byte, ushort)[] { (0x09, 20), (0x20, 64), (0x05, 41) })
    {
        byte[] bytes = new byte[length];
        int count = device.HandleControlRequest(new UsbSetupPacket
        {
            RequestType = 0xA1, Request = 1, Value = (ushort)(0x0300 | id), Index = 3, Length = length
        }, [], bytes);
        if (count != length) throw new InvalidDataException($"Persona feature {id:X2} unavailable.");
        features[id.ToString("x2")] = Convert.ToHexString(bytes).ToLowerInvariant();
    }
}
using UsbIpServer server = new(new UsbIpServerOptions
{
    ListenEndPoint = new IPEndPoint(IPAddress.Loopback, 0),
    ImmediateInputDelivery = true, MeasureIsoOutLatency = true, PackIsoInPayload = true, Logger = capture,
    AcknowledgeInterruptOutBeforeDispatch = true
});
server.AddDevice("1-1", device);
server.Start();
Atomic("ready.json", new
{
    schema = 1, persona, port = server.BoundEndPoint.Port, busId = "1-1", serial,
    vendor = device.Descriptors.IdVendor, product = device.Descriptors.IdProduct,
    features, physicalDeviceAccess = false, radioInitialized = false, packedIsoIn = true,
    syntheticMicrophone = persona != "xbox"
});
Stopwatch runtime = Stopwatch.StartNew();
while (runtime.Elapsed.TotalSeconds < seconds && !File.Exists(Path.Combine(output, "stop")))
{
    Atomic("capture.json", capture.Snapshot());
    await Task.Delay(100);
}
server.Stop();
Atomic("capture.json", capture.Snapshot());
Atomic("stopped.json", new { completed = true, physicalDeviceAccess = false, radioInitialized = false });

// Delegates the production persona's control/HID/PCM handling unchanged. The sole
// synthetic source is deterministic microphone data inserted immediately before
// an ISO IN pull, so real Linux short-packet reassembly can be checked byte-for-byte.
sealed class ProbePersona(DualSenseDevice inner, Capture capture) : VirtualUsbDevice(inner.Descriptors)
{
    long microphoneFrame;
    protected override int OnClassRequest(in UsbSetupPacket setup, ReadOnlySpan<byte> data, Span<byte> response)
    {
        int length = inner.HandleControlRequest(setup, data, response);
        capture.Event("class-request", new { setup.RequestType, setup.Request, setup.Value, setup.Index, setup.Length, result = length });
        return length;
    }
    protected override void OnInterfaceChanged(byte number, byte alternate)
    {
        inner.HandleControlRequest(new UsbSetupPacket { RequestType = 1, Request = 11, Index = number, Value = alternate }, [], []);
        capture.Event("interface", new { number, alternate });
    }
    protected override void OnConfigurationChanged(byte value) =>
        inner.HandleControlRequest(new UsbSetupPacket { Request = 9, Value = value }, [], []);
    public override int ProvideInputReport(Span<byte> bytes) => inner.ProvideInputReport(bytes);
    public override void OnOutputReport(ReadOnlySpan<byte> bytes) => inner.OnOutputReport(bytes);
    public override void OnIsoPacketReceived(byte endpoint, ReadOnlySpan<byte> bytes) => inner.OnIsoPacketReceived(endpoint, bytes);
    public override int ProvideIsoPacket(byte endpoint, Span<byte> destination)
    {
        if (endpoint == 0x82)
        {
            Span<byte> generated = stackalloc byte[192];
            for (int offset = 0; offset < generated.Length; offset += 4, ++microphoneFrame)
            {
                BinaryPrimitives.WriteInt16LittleEndian(generated[offset..], (short)(1234 + microphoneFrame % 1009));
                BinaryPrimitives.WriteInt16LittleEndian(generated[(offset + 2)..], (short)(-4321 - microphoneFrame % 1013));
            }
            inner.SetMicrophonePcm(generated);
        }
        int count = inner.ProvideIsoPacket(endpoint, destination);
        if (endpoint == 0x82 && count > 0) capture.Microphone(destination[..count]);
        return count;
    }
}

sealed class Capture : IEmulationLogger, IDisposable
{
    readonly Lock gate = new();
    readonly StreamWriter events;
    readonly FileStream pcm, haptics, microphone;
    readonly JsonSerializerOptions json;
    long bytes, hapticBytes, microphoneBytes, packets, outputs;
    bool overflow;
    public Capture(string directory, JsonSerializerOptions options)
    {
        json = options;
        events = new(Path.Combine(directory, "events.jsonl")) { AutoFlush = true };
        pcm = new(Path.Combine(directory, "audio-ingress.pcm"), FileMode.CreateNew, FileAccess.Write, FileShare.Read);
        haptics = new(Path.Combine(directory, "haptics-ingress.pcm"), FileMode.CreateNew, FileAccess.Write, FileShare.Read);
        microphone = new(Path.Combine(directory, "microphone-source.pcm"), FileMode.CreateNew, FileAccess.Write, FileShare.Read);
    }
    public void Event(string kind, object value)
    {
        lock (gate)
        {
            if (kind.EndsWith("output")) ++outputs;
            if (events.BaseStream.Position < 2 * 1024 * 1024)
                events.WriteLine(JsonSerializer.Serialize(new { kind, timestamp = Stopwatch.GetTimestamp(), value }, json));
            else overflow = true;
        }
    }
    public void Log(EmulationLogLevel level, string message) => Event("server", new { level = level.ToString(), message });
    public void Audio(ReadOnlyMemory<byte> data)
    {
        lock (gate)
        {
            if (bytes + data.Length > 8 * 1024 * 1024) { overflow = true; return; }
            pcm.Write(data.Span); pcm.Flush(); bytes += data.Length; ++packets;
        }
    }
    public void Haptics(ReadOnlyMemory<byte> data)
    {
        lock (gate)
        {
            if (hapticBytes + data.Length > 4 * 1024 * 1024) { overflow = true; return; }
            haptics.Write(data.Span); haptics.Flush(); hapticBytes += data.Length;
        }
    }
    public void Microphone(ReadOnlySpan<byte> data)
    {
        lock (gate)
        {
            if (microphoneBytes + data.Length > 8 * 1024 * 1024) { overflow = true; return; }
            microphone.Write(data); microphone.Flush(); microphoneBytes += data.Length;
        }
    }
    public object Snapshot() { lock (gate) return new { bytes, hapticBytes, microphoneBytes, packets, outputs, overflow }; }
    public void Dispose() { lock (gate) { events.Dispose(); pcm.Dispose(); haptics.Dispose(); microphone.Dispose(); } }
}
