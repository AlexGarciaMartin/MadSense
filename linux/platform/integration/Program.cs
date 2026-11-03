using System.Diagnostics;
using System.Net;
using System.Runtime.InteropServices;
using System.Text.Json;
using DualSenseClient.Controllers;
using DualSenseClient.Controllers.Devices;
using DualSenseClient.Controllers.DualSense.Audio;
using DualSenseClient.Controllers.DualSense.Feature;
using DualSenseClient.Controllers.DualSense.Input;
using DualSenseClient.Controllers.Emulation;
using DualSenseClient.Controllers.SpecialActions;
using DualSenseClient.Emulation;
using DualSenseClient.Emulation.Core;
using DualSenseClient.Hid;
using DualSenseClient.Settings;
using DualSenseClient.Settings.Sections;
using MadSense.Platform;
using Persona = DualSenseClient.Emulation.Devices.DualSense.DualSenseDevice;

// Experiment entry point, not the application. All controller/parser/forwarder,
// exporter and broker implementations below are the production classes.
Dictionary<string, string> options = [];
for (int i = 0; i < args.Length; i += 2)
{
    if (i + 1 == args.Length || !args[i].StartsWith("--")) throw new ArgumentException("Expected --name value pairs.");
    options.Add(args[i], args[i + 1]);
}
if (options.Count == 1 && options.TryGetValue("--verify-fixtures", out var fixturePath))
{
    FixtureChecks.Verify(fixturePath);
    Console.WriteLine("Production parser, raw conversion and feature fixture checks passed.");
    return;
}
if (options.Keys.Any(key => key is not ("--owned-guest-token" or "--device" or "--output" or "--seconds" or "--persona")))
    throw new ArgumentException("Unknown option.");
string token = Guid.Parse(options["--owned-guest-token"]).ToString();
if (!OperatingSystem.IsLinux() || Native.GetUid() == 0 ||
    File.ReadAllText("/etc/madsense-ci-owner").Trim() != token ||
    File.ReadAllText("/sys/class/dmi/id/sys_vendor").Trim() != "QEMU")
    throw new InvalidOperationException("Ordinary user inside the explicitly owned QEMU guest required.");
string path = options["--device"];
string output = Path.GetFullPath(options["--output"]);
string mode = options.GetValueOrDefault("--persona", "dualsense");
if (mode is not ("dualsense" or "edge" or "xbox")) throw new ArgumentException("Unknown persona.");
int seconds = int.Parse(options.GetValueOrDefault("--seconds", "120"));
if (seconds is < 5 or > 300) throw new ArgumentException("Duration must be 5..300 seconds.");
Directory.CreateDirectory(output);
if (Directory.EnumerateFileSystemEntries(output).Any()) throw new IOException("Fresh output directory required.");
using Evidence evidence = new(output);
using CancellationTokenSource deadline = new(TimeSpan.FromSeconds(seconds + 30));
using HidDeviceEnumerator enumerator = new();
var info = enumerator.Enumerate(0x054c).Single(device => device.Path == path);
if (info.BusType != ConnectionType.Bluetooth || info.ProductId is not (0x0ce6 or 0x0df2))
    throw new InvalidOperationException("Expected the runner's Bluetooth UHID fixture.");
await using var broker = await LinuxBrokerClient.ConnectAsync(deadline.Token);
var preflight = await broker.PreflightAsync(deadline.Token);
if (!preflight.Available) throw new InvalidOperationException(preflight.Detail);
await using var session = await broker.AcquirePhysicalAsync(path, deadline.Token);
session.IsolationLost += () => evidence.Event("isolation-lost", new { });
using var tracker = new ControllerTracker();
using var actions = new SpecialActionEngineRegistry();
DualSenseDevice? pad = null;
EmulationService? emulation = null;
EmulationServer? server = null;
var factory = new ObservedFactory(evidence);
bool stopped = false;
try
{
    evidence.Event("calibration-rpc", new { hex = Convert.ToHexString(session.Device.GetFeatureReport(0x05, 41)) });
    pad = info.ProductId == 0x0df2 ? new DualSenseEdgeDevice(session.Device, info) : new DualSenseDevice(session.Device, info);
    pad.BluetoothAudioSessionBufferLengths = [0x40, 0x40, 0x40, 0x40, 0x20];
    pad.InputReportReceived += (_, report) => evidence.Input(report);
    evidence.Event("physical-features", new { firmware = pad.FirmwareInfo, pairing = pad.PairingInfo });
    if (pad.FirmwareInfo is not { IsValid: true } || pad.PairingInfo is not { IsValid: true })
        throw new InvalidDataException("UHID firmware/pairing features did not reach the production constructor.");
    var settings = new ControllerInfoService(controllersPath: Path.Combine(output, "controllers.json"));
    settings.RegisterController(pad.PairingInfo?.ClientMac, info.Path, "Synthetic UHID core integration");
    settings.SaveEmulationSettings(pad.PairingInfo?.ClientMac, info.Path, new EmulationSettings
    {
        Mode = mode == "xbox" ? EmulationMode.Xbox360 : EmulationMode.DualSense,
        Variant = new VariantSettings { DualSense = mode == "edge" ? DualSenseVariant.Edge : DualSenseVariant.Standard },
        Forward = new ForwardSettings { StrictNativeHaptics = true, Volume = 0x64, SpeakerPreGain = 2 }
    });
    emulation = new EmulationService(tracker, enumerator, settings, null!, actions, new AudioApplicationService(),
        () => server = new EmulationServer(new EmulationServerOptions
        {
            ListenEndPoint = new IPEndPoint(IPAddress.Loopback, 0), AttachStrategy = session.AttachStrategy,
            ImmediateInputDelivery = true, PackIsoInPayload = true, MeasureIsoOutLatency = true
        }), factory, () => session.IsolationVerified);
    emulation.Start(); tracker.TrackController(pad);
    var start = Stopwatch.StartNew();
    while (!emulation.GetStatus(pad).Running)
    {
        deadline.Token.ThrowIfCancellationRequested();
        if (!session.IsolationVerified || start.Elapsed.TotalSeconds > 30)
            throw new InvalidOperationException("Production core did not attach: " + emulation.GetStatus(pad));
        await Task.Delay(50, deadline.Token);
    }
    evidence.Atomic("ready.json", new
    {
        schema = 1, scope = "production-core-synthetic-UHID", ownedGuestToken = token,
        physicalDevices = false, radioInitialized = false, applicationUiExercised = false,
        pid = Environment.ProcessId, mode, devicePath = path, port = server!.BoundEndPoint.Port,
        busIds = server.BusIds, virtualDevicePath = emulation.GetStatus(pad).VirtualDevicePath,
        vendorId = factory.Last!.Descriptors.IdVendor, productId = factory.Last.Descriptors.IdProduct,
        features = factory.Features, timestampFrequency = Stopwatch.Frequency
    });
    while (start.Elapsed.TotalSeconds < seconds && !File.Exists(Path.Combine(output, "stop")))
    {
        deadline.Token.ThrowIfCancellationRequested();
        if (!session.IsolationVerified || pad.IsDisposed) throw new IOException("Synthetic physical session ended unexpectedly.");
        evidence.Atomic("status.json", new { status = emulation.GetStatus(pad), metrics = session.Metrics, inputs = evidence.InputCount });
        await Task.Delay(100, deadline.Token);
    }
    stopped = true;
}
finally
{
    // Same ordering required by the app: stop all virtual writers, reset the
    // physical controller, then release the broker's isolated physical lease.
    session.CancelPendingAttach();
    try { emulation?.Dispose(); }
    finally
    {
        try
        {
            if (pad is not null)
            {
                tracker.UntrackController(pad); actions.Remove(pad);
                try { pad.ResetOutputs(); } finally { pad.Dispose(); }
            }
        }
        finally
        {
            var metrics = session.Metrics;
            await session.DisposeAsync();
            evidence.Atomic("stopped.json", new { completed = stopped && !evidence.Overflow && session.CleanupConfirmed,
                cleanupConfirmed = session.CleanupConfirmed, evidenceOverflow = evidence.Overflow,
                metrics, inputs = evidence.InputCount,
                physicalDevices = false, radioInitialized = false, applicationUiExercised = false });
        }
    }
}

static partial class Native
{
    [LibraryImport("libc", EntryPoint = "getuid")] internal static partial uint GetUid();
}

sealed class Evidence : IDisposable
{
    readonly string directory;
    readonly StreamWriter stream;
    readonly Lock gate = new();
    readonly Dictionary<string, FileStream> binary = [];
    long inputCount;
    int overflow;
    readonly HashSet<string> inputStates = [];
    static readonly JsonSerializerOptions Json = new() { PropertyNamingPolicy = JsonNamingPolicy.CamelCase };
    public long InputCount => Interlocked.Read(ref inputCount);
    public bool Overflow => Volatile.Read(ref overflow) != 0;
    public Evidence(string directory) { this.directory = directory; stream = new(Path.Combine(directory, "events.jsonl")); }
    public void Atomic(string name, object value)
    {
        string path = Path.Combine(directory, name);
        File.WriteAllText(path + ".tmp", JsonSerializer.Serialize(value, Json) + "\n");
        File.Move(path + ".tmp", path, true);
    }
    public void Event(string kind, object value)
    {
        lock (gate)
        { stream.WriteLine(JsonSerializer.Serialize(new { kind, timestamp = Stopwatch.GetTimestamp(), value }, Json)); stream.Flush(); }
    }
    public void Bytes(string name, ReadOnlySpan<byte> data)
    {
        lock (gate)
        {
            if (!binary.TryGetValue(name, out var file)) binary[name] = file = new(Path.Combine(directory, name), FileMode.CreateNew);
            if (file.Length + data.Length > 16 * 1024 * 1024) { Interlocked.Exchange(ref overflow, 1); return; }
            file.Write(data); file.Flush();
        }
    }
    public void Input(InputReport report)
    {
        Interlocked.Increment(ref inputCount);
        string state = $"{report.Input.LeftStickX}:{report.Input.L2}:{report.Motion.GyroX}:{report.Input.Cross}";
        lock (gate)
        {
            if (!inputStates.Add(state)) return;
            Event("parsed-input", new { report.Input, report.Motion, report.Touchpad, report.Battery,
                usbRaw64 = Convert.ToHexString(report.UsbRaw64 ?? []) });
        }
    }
    public void Dispose() { lock (gate) { stream.Dispose(); foreach (var file in binary.Values) file.Dispose(); } }
}

sealed class ObservedFactory(Evidence evidence) : IVirtualControllerFactory
{
    readonly VirtualControllerFactory production = new();
    public VirtualUsbDevice? Last { get; private set; }
    public Dictionary<string, string> Features { get; } = [];
    public VirtualControllerCreation? Create(EmulationServer server, EmulationMode mode, IDualSenseOutputs outputs,
        bool vibrationV2, bool edge = false, DualShock4Variant ds4Variant = DualShock4Variant.V2)
    {
        var result = production.Create(server, mode, outputs, vibrationV2, edge, ds4Variant);
        Last = result?.Persona;
        if (Last?.Descriptors.HidReportDescriptor is { } descriptor) evidence.Bytes("hid-descriptor.bin", descriptor.Span);
        if (Last is Persona pad)
        {
            foreach (var (id, size) in new (byte, int)[] { (9, 20), (0x20, 64), (5, 41) })
            {
                byte[] data = new byte[size];
                int length = pad.HandleControlRequest(new UsbSetupPacket
                { RequestType = 0xA1, Request = 1, Value = (ushort)(0x0300 | id), Index = 3, Length = (ushort)size }, [], data);
                if (length != size) throw new InvalidDataException("Production persona feature missing.");
                Features[id.ToString("x2")] = Convert.ToHexString(data);
            }
            pad.OutputReportReceived += report => evidence.Event("virtual-output", report);
            pad.AudioOutReceived += data => evidence.Bytes("audio-ingress.pcm", data.Span);
            pad.RealtimeHapticsReceived += data => evidence.Bytes("haptics-ingress.pcm", data.Span);
        }
        return result;
    }
}

static class FixtureChecks
{
    public static void Verify(string path)
    {
        using var json = JsonDocument.Parse(File.ReadAllText(path));
        var root = json.RootElement;
        foreach (var item in root.GetProperty("input").EnumerateObject())
        {
            byte[] bytes = Convert.FromHexString(item.Value.GetProperty("bluetooth").GetString()!);
            byte[] expected = Convert.FromHexString(item.Value.GetProperty("usb").GetString()!);
            if (!DualSenseRawReport.TryBuildUsbRaw64(bytes, bytes.Length, ConnectionType.Bluetooth, out var raw) || !raw!.SequenceEqual(expected))
                throw new InvalidDataException("Raw Bluetooth to USB conversion mismatch.");
            var parsed = new InputReport(bytes, 2);
            bool marker = item.Name == "marker";
            if (parsed.Input.LeftStickX != (marker ? 17 : 128) || parsed.Input.L2 != (marker ? 91 : 0) ||
                parsed.Motion.GyroX != (marker ? -1234 : 0) || parsed.Motion.AccelZ != 8192 ||
                parsed.Touchpad.Touch1.IsActive || parsed.Touchpad.Touch2.IsActive ||
                parsed.Input.Cross != (marker || item.Name == "south") || !parsed.Input.DPadNeutral)
                throw new InvalidDataException("Production typed input parser mismatch.");
        }
        var features = root.GetProperty("features");
        var firmware = new FirmwareInfo(Convert.FromHexString(features.GetProperty("20").GetString()!));
        var pairing = new PairingInfo(Convert.FromHexString(features.GetProperty("09").GetString()!));
        if (!firmware.IsValid || firmware.UpdateVersionValue != 0x0224 || !pairing.IsValid || pairing.ClientMac != "02:D5:11:22:33:44")
            throw new InvalidDataException("Production feature parser mismatch.");
    }
}
