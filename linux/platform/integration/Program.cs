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
if (options.Keys.Any(key => key is not ("--owned-guest-token" or "--device" or "--output" or "--seconds" or "--persona" or "--observe-usb-output")))
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
if (seconds is < 5 or > 900) throw new ArgumentException("Duration must be 5..900 seconds.");
Directory.CreateDirectory(output);
if (Directory.EnumerateFileSystemEntries(output).Any()) throw new IOException("Fresh output directory required.");
bool observeUsbOutput = bool.Parse(options.GetValueOrDefault("--observe-usb-output", "false"));
using Evidence evidence = new(output, observeUsbOutput);
using CancellationTokenSource deadline = new(TimeSpan.FromSeconds(seconds + 30));
using HidDeviceEnumerator enumerator = new();
var info = enumerator.Enumerate(0x054c).Single(device => device.Path == path);
if (info.BusType != ConnectionType.Bluetooth || info.ProductId is not (0x0ce6 or 0x0df2))
    throw new InvalidOperationException("Expected the runner's Bluetooth UHID fixture.");
await using var broker = await LinuxBrokerClient.ConnectAsync(deadline.Token);
var preflight = await broker.PreflightAsync(deadline.Token);
if (!preflight.Available) throw new InvalidOperationException(preflight.Detail);
await using var session = await broker.AcquirePhysicalAsync(path, deadline.Token);
session.IsolationLost += () => evidence.Event("isolation-lost", new { reason = session.IsolationLostReason });
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
    string physicalMac = pad.PairingInfo?.ClientMac ?? throw new InvalidDataException("Physical pairing identity is missing.");
    if (VirtualDeviceFilter.IsKnownVirtualMac(physicalMac))
        throw new InvalidDataException("Synthetic physical fixture uses a reserved virtual identity.");
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
            ImmediateInputDelivery = true, PackIsoInPayload = true, MeasureIsoOutLatency = true,
            AcknowledgeInterruptOutBeforeDispatch = true, HidSetReportIncludesReportId = true
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
    // EmulationService seeds physical calibration after Create and before
    // attachment. Snapshot the final feature surface only once Running.
    factory.CaptureFeatures();
    string? virtualMac = mode == "xbox" ? null : new PairingInfo(Convert.FromHexString(factory.Features["09"])).ClientMac;
    if (virtualMac is not null) FixtureChecks.VerifyMacSeparation(physicalMac, virtualMac);
    evidence.Atomic("ready.json", new
    {
        schema = 1, scope = "production-core-synthetic-UHID", ownedGuestToken = token,
        physicalDevices = false, radioInitialized = false, applicationUiExercised = false,
        pid = Environment.ProcessId, mode, devicePath = path, port = server!.BoundEndPoint.Port,
        busIds = server.BusIds, virtualDevicePath = emulation.GetStatus(pad).VirtualDevicePath,
        vendorId = factory.Last!.Descriptors.IdVendor, productId = factory.Last.Descriptors.IdProduct,
        features = factory.Features, physicalMac, virtualMac,
        timestampFrequency = Stopwatch.Frequency, rawOutputObservationEnabled = observeUsbOutput
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
            evidence.CompleteObservations();
            evidence.Atomic("stopped.json", new { completed = stopped && !evidence.Overflow && session.CleanupConfirmed,
                cleanupConfirmed = session.CleanupConfirmed, evidenceOverflow = evidence.Overflow,
                isolationLostReason = session.IsolationLostReason, inputObservation = evidence.InputObservation,
                rawOutputObservation = evidence.RawOutputObservation,
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
    readonly object inputGate = new();
    readonly DeferredEvidence<(InputReport Report, long Timestamp)> inputObservation;
    readonly DeferredEvidence<(ReadOnlyMemory<byte> Report, long Timestamp)>? rawOutputObservation;
    long inputCount;
    int overflow;
    readonly HashSet<(int, int, int, bool)> inputStates = [];
    static readonly JsonSerializerOptions Json = new() { PropertyNamingPolicy = JsonNamingPolicy.CamelCase };
    public long InputCount => Interlocked.Read(ref inputCount);
    public bool Overflow => Volatile.Read(ref overflow) != 0 || inputObservation.Rejected != 0 || inputObservation.Error is not null ||
        (rawOutputObservation is not null && (rawOutputObservation.Rejected != 0 || rawOutputObservation.Error is not null));
    public bool RawOutputObservationEnabled => rawOutputObservation is not null;
    public object? RawOutputObservation => rawOutputObservation is null ? null : new
    {
        rejectedSnapshots = rawOutputObservation.Rejected, unwrittenSnapshots = rawOutputObservation.Unwritten,
        error = rawOutputObservation.Error?.Message
    };
    public object InputObservation => new { rejectedSnapshots = inputObservation.Rejected,
        unwrittenSnapshots = inputObservation.Unwritten, error = inputObservation.Error?.Message };
    public Evidence(string directory, bool observeRawOutput = false)
    {
        this.directory = directory; stream = new(Path.Combine(directory, "events.jsonl"));
        inputObservation = new(32, item =>
        {
            var report = item.Report;
            Event("parsed-input", new { report.Input, report.Motion, report.Touchpad, report.Battery,
                usbRaw64 = Convert.ToHexString(report.UsbRaw64 ?? []) }, item.Timestamp);
        });
        if (observeRawOutput)
            rawOutputObservation = new(64, item => Event("virtual-output-raw", new
            {
                hex = Convert.ToHexString(item.Report.Span), length = item.Report.Length
            }, item.Timestamp));
    }
    public void Atomic(string name, object value)
    {
        string path = Path.Combine(directory, name);
        File.WriteAllText(path + ".tmp", JsonSerializer.Serialize(value, Json) + "\n");
        File.Move(path + ".tmp", path, true);
    }
    public void Event(string kind, object value, long? timestamp = null)
    {
        lock (gate)
        { stream.WriteLine(JsonSerializer.Serialize(new { kind, timestamp = timestamp ?? Stopwatch.GetTimestamp(), value }, Json)); stream.Flush(); }
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
        long timestamp = Stopwatch.GetTimestamp();
        var state = ((int)report.Input.LeftStickX, (int)report.Input.L2, (int)report.Motion.GyroX, report.Input.Cross);
        lock (inputGate)
        {
            if (!inputStates.Add(state)) return;
            // Parsed members are values; copy the attached raw image as an
            // independent bounded snapshot before returning to the input reader.
            inputObservation.TryWrite((report with { UsbRaw64 = report.UsbRaw64?.ToArray() }, timestamp));
        }
    }
    // The persona owns this immutable snapshot. Retain it without another copy;
    // serialization and file I/O run only on the bounded observer's writer.
    public void RawOutput(ReadOnlyMemory<byte> report) => rawOutputObservation?.TryWrite((report, Stopwatch.GetTimestamp()));
    public void CompleteObservations()
    {
        inputObservation.Complete();
        rawOutputObservation?.Complete();
    }
    public void Dispose()
    {
        inputObservation.Dispose();
        rawOutputObservation?.Dispose();
        lock (gate) { stream.Dispose(); foreach (var file in binary.Values) file.Dispose(); }
    }
}

sealed class ObservedFactory(Evidence evidence) : IVirtualControllerFactory
{
    readonly VirtualControllerFactory production = new();
    public VirtualUsbDevice? Last { get; private set; }
    public Dictionary<string, string> Features { get; private set; } = [];
    public void CaptureFeatures() => Features = ReadFeatures(Last as Persona);
    internal static Dictionary<string, string> ReadFeatures(Persona? pad)
    {
        Dictionary<string, string> features = [];
        if (pad is null) return features;
        foreach (var (id, size) in new (byte, int)[] { (9, 20), (0x20, 64), (5, 41) })
        {
            byte[] data = new byte[size];
            int length = pad.HandleControlRequest(new UsbSetupPacket
            { RequestType = 0xA1, Request = 1, Value = (ushort)(0x0300 | id), Index = 3, Length = (ushort)size }, [], data);
            if (length != size) throw new InvalidDataException("Production persona feature missing.");
            features[id.ToString("x2")] = Convert.ToHexString(data);
        }
        return features;
    }
    public VirtualControllerCreation? Create(EmulationServer server, EmulationMode mode, IDualSenseOutputs outputs,
        bool vibrationV2, bool edge = false, DualShock4Variant ds4Variant = DualShock4Variant.V2)
    {
        var result = production.Create(server, mode, outputs, vibrationV2, edge, ds4Variant);
        Last = result?.Persona;
        if (Last?.Descriptors.HidReportDescriptor is { } descriptor) evidence.Bytes("hid-descriptor.bin", descriptor.Span);
        if (Last is Persona pad)
        {
            if (evidence.RawOutputObservationEnabled) pad.RawOutputReportReceived += evidence.RawOutput;
            pad.OutputReportReceived += report => evidence.Event("virtual-output", report);
            pad.AudioOutReceived += data => evidence.Bytes("audio-ingress.pcm", data.Span);
            pad.RealtimeHapticsReceived += data => evidence.Bytes("haptics-ingress.pcm", data.Span);
        }
        return result;
    }
}

static class FixtureChecks
{
    static void VerifyRawOutputObservation()
    {
        var directory = Directory.CreateTempSubdirectory("madsense-raw-observer-");
        try
        {
            using var evidence = new Evidence(directory.FullName, observeRawOutput: true);
            var persona = new Persona(new DualSenseClient.Emulation.Devices.DualSense.DualSenseIdentity());
            persona.RawOutputReportReceived += evidence.RawOutput;
            byte[] report = new byte[48];
            report[0] = 2; report[41] = 0xA7;
            string expected = Convert.ToHexString(report);
            long before = Stopwatch.GetTimestamp();
            persona.OnOutputReport(report);
            long after = Stopwatch.GetTimestamp();
            Array.Fill(report, (byte)0xFF);
            evidence.CompleteObservations();
            using var capture = new StreamReader(new FileStream(Path.Combine(directory.FullName, "events.jsonl"),
                FileMode.Open, FileAccess.Read, FileShare.ReadWrite));
            string[] rows = capture.ReadToEnd().Split('\n', StringSplitOptions.RemoveEmptyEntries);
            if (rows.Length != 1 || evidence.Overflow || !evidence.RawOutputObservationEnabled)
                throw new InvalidDataException("Raw output snapshot was lost or duplicated.");
            using var row = JsonDocument.Parse(rows[0]);
            var value = row.RootElement;
            long timestamp = value.GetProperty("timestamp").GetInt64();
            if (value.GetProperty("kind").GetString() != "virtual-output-raw" || timestamp < before || timestamp > after ||
                value.GetProperty("value").GetProperty("hex").GetString() != expected ||
                value.GetProperty("value").GetProperty("length").GetInt32() != 48)
                throw new InvalidDataException("Raw output bytes or callback timestamp changed in deferred capture.");
            using var counters = JsonDocument.Parse(JsonSerializer.Serialize(evidence.RawOutputObservation));
            if (counters.RootElement.GetProperty("rejectedSnapshots").GetInt64() != 0 ||
                counters.RootElement.GetProperty("unwrittenSnapshots").GetInt64() != 0 ||
                counters.RootElement.GetProperty("error").ValueKind != JsonValueKind.Null)
                throw new InvalidDataException("Raw observer did not flush cleanly.");
        }
        finally { directory.Delete(recursive: true); }
    }

    static void VerifyDeferredObservation()
    {
        using var entered = new ManualResetEventSlim();
        using var release = new ManualResetEventSlim();
        List<int> delivered = [];
        using var observer = new DeferredEvidence<int>(2, item =>
        {
            entered.Set();
            if (!release.Wait(TimeSpan.FromSeconds(10))) throw new TimeoutException("Observer verification stalled.");
            delivered.Add(item);
        });
        try
        {
            if (!observer.TryWrite(1) || !entered.Wait(TimeSpan.FromSeconds(3)))
                throw new InvalidDataException("Evidence writer failed to start.");
            var enqueue = Task.Run(() => new[] { observer.TryWrite(2), observer.TryWrite(3), observer.TryWrite(4) });
            if (!enqueue.Wait(TimeSpan.FromSeconds(3)) || !enqueue.Result.SequenceEqual([true, true, false]))
                throw new InvalidDataException("A slow evidence writer blocked or silently discarded input observations.");
        }
        finally { release.Set(); }
        observer.Complete();
        if (observer.Rejected != 1 || observer.Unwritten != 0 || observer.Error is not null || !delivered.SequenceEqual([1, 2, 3]))
            throw new InvalidDataException("Evidence queue accounting or drain failed.");
        using var failed = new DeferredEvidence<int>(1, _ => throw new IOException("deliberate observer failure"));
        failed.TryWrite(1); failed.Complete();
        if (failed.Error is not IOException || failed.Unwritten != 1)
            throw new InvalidDataException("Evidence writer failure was hidden.");
    }

    public static void VerifyMacSeparation(string? physical, string? virtualMac)
    {
        if (physical != "02:FA:11:22:33:44" || VirtualDeviceFilter.IsKnownVirtualMac(physical) ||
            !VirtualDeviceFilter.IsKnownVirtualMac(virtualMac) || string.Equals(physical, virtualMac, StringComparison.OrdinalIgnoreCase))
            throw new InvalidDataException("Physical and virtual fixture identities are not independently classified.");
    }

    public static void Verify(string path)
    {
        VerifyDeferredObservation();
        VerifyRawOutputObservation();
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
            if (parsed.Connection.Headphone != (item.Name == "headset") ||
                parsed.Input.EdgeFunctionLeft != (item.Name == "edge_fn_left") ||
                parsed.Input.EdgeFunctionRight != (item.Name == "edge_fn_right") ||
                parsed.Input.EdgePaddleLeft != (item.Name == "edge_paddle_left") ||
                parsed.Input.EdgePaddleRight != (item.Name == "edge_paddle_right"))
                throw new InvalidDataException("Headset/Edge input marker did not reach the production parser.");
        }
        var features = root.GetProperty("features");
        byte[] physicalCalibration = Convert.FromHexString(features.GetProperty("05").GetString()!);
        var persona = new Persona(new DualSenseClient.Emulation.Devices.DualSense.DualSenseIdentity());
        var beforeCalibration = ObservedFactory.ReadFeatures(persona);
        if (beforeCalibration["05"] == Convert.ToHexString(physicalCalibration) ||
            !persona.TrySetMotionCalibration(physicalCalibration))
            throw new InvalidDataException("Calibration regression requires distinct accepted physical data.");
        var afterCalibration = ObservedFactory.ReadFeatures(persona);
        if (afterCalibration["05"] != Convert.ToHexString(physicalCalibration) ||
            beforeCalibration["05"] == afterCalibration["05"] ||
            beforeCalibration["09"] != afterCalibration["09"] || beforeCalibration["20"] != afterCalibration["20"])
            throw new InvalidDataException("Feature observation retained stale calibration or changed identity.");
        var firmware = new FirmwareInfo(Convert.FromHexString(features.GetProperty("20").GetString()!));
        var pairing = new PairingInfo(Convert.FromHexString(features.GetProperty("09").GetString()!));
        if (!firmware.IsValid || firmware.UpdateVersionValue != 0x0224 || !pairing.IsValid || pairing.ClientMac != "02:FA:11:22:33:44")
            throw new InvalidDataException("Production feature parser mismatch.");
        VerifyMacSeparation(pairing.ClientMac, VirtualDeviceFilter.CreateOwnershipMac());
    }
}
