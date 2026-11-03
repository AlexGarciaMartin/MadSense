using System.Net;
using System.Threading.Channels;
using System.Text.Json;
using DualSenseClient.Controllers;
using DualSenseClient.Controllers.Devices;
using DualSenseClient.Controllers.DualSense.Audio;
using DualSenseClient.Controllers.DualSense.Enum;
using DualSenseClient.Controllers.Emulation;
using DualSenseClient.Controllers.SpecialActions;
using DualSenseClient.Emulation;
using DualSenseClient.Hid;
using DualSenseClient.Logging;
using DualSenseClient.Settings;
using DualSenseClient.Settings.Sections;
using MadSense.Mac;
using MadSense.Platform;

namespace MadSense;

sealed record LinuxBridgeState(bool Ready, string Message, bool Haptics = false, bool Triggers = false, bool Audio = false, bool Rumble = false, int Battery = -1, bool Charging = false, bool Headset = false, bool RequiresResume = false);

// One worker owns all bridge transitions. HID readers and the existing audio
// pump remain dedicated; neither waits for the UI or its presentation tick.
sealed class LinuxBridgeController : IAsyncDisposable
{
    readonly Channel<Func<ValueTask>> commands = Channel.CreateUnbounded<Func<ValueTask>>(new() { SingleReader = true });
    readonly SemaphoreSlim wake = new(0, 1);
    readonly CancellationTokenSource lifetime = new();
    readonly Task worker;
    readonly bool preview;
    readonly Func<CancellationToken, ValueTask> connect;
    readonly Lock attemptLock = new();
    CancellationTokenSource? connectionAttempt;
    string lastMetrics = "No broker session has been opened.";
    LinuxBridgeState state = new(false, L.Text("Ui.PausedMessage"));
    string mode, speaker;
    bool paused = true, requestedHeadset, haveHeadset, authorizationDenied;
    long nextRetry, lastReport, started;
    int isolationLost;
    HidDeviceEnumerator? enumerator;
    LinuxBrokerClient? broker;
    LinuxPhysicalSession? physicalSession;
    ActivityHidDevice? metered;
    DualSenseDevice? pad;
    ControllerTracker? tracker;
    SpecialActionEngineRegistry? actions;
    ControllerInfoService? settings;
    EmulationService? emulation;
    public LinuxBridgeState State => Volatile.Read(ref state);
    public string DiagnosticText => $"MadSense Linux 0.5.0\n{Environment.OSVersion}\n{System.Runtime.InteropServices.RuntimeInformation.ProcessArchitecture}\n\n{State.Message}\n\n" + MetricsText() + "\n\n" + LinuxLog.Recent();
    string MetricsText()
    {
        var current = Volatile.Read(ref physicalSession);
        return current == null ? Volatile.Read(ref lastMetrics) : JsonSerializer.Serialize(new
        { scope = "Software broker IPC and client queue only; no radio, firmware or physical actuator latency", metrics = current.Metrics }, new JsonSerializerOptions { WriteIndented = true });
    }
    internal LinuxBridgeController(bool preview, string mode, string speaker, Func<CancellationToken, ValueTask>? connectOverride = null)
    {
        this.preview = preview; this.mode = mode; this.speaker = speaker;
        connect = connectOverride ?? Connect;
        if (!preview) DualSenseClientLogger.Configure(LogLevel.Info, new LinuxLog());
        worker = Task.Run(Run);
    }
    void Nudge() { try { if (wake.CurrentCount == 0) wake.Release(); } catch (ObjectDisposedException) { } catch (SemaphoreFullException) { } }
    void Send(Func<ValueTask> action) { if (commands.Writer.TryWrite(action)) Nudge(); }
    void CancelConnection() { lock (attemptLock) connectionAttempt?.Cancel(); }
    public void SetPaused(bool value)
    {
        if (value) CancelConnection();
        Send(async () => { paused = value; authorizationDenied = false; if (value) await Stop(); else nextRetry = 0; });
    }
    public void SetMode(string value)
    {
        CancelConnection();
        Send(async () => { if (value != mode) { await Stop(); mode = value; nextRetry = 0; } });
    }
    public void SetSpeaker(string value) => Send(() =>
    {
        speaker = value;
        if (pad != null && emulation != null)
        {
            var (volume, gain) = SpeakerLevel(value);
            emulation.SetForwardingSpeaker(pad, volume, gain);
            settings!.SaveEmulationSettings(pad.PairingInfo?.ClientMac, pad.Info.Path, SettingsFor(pad));
        }
        return ValueTask.CompletedTask;
    });
    static (byte Volume, byte Gain) SpeakerLevel(string level) => level switch { "Alto" => (0x64, 7), "Bajo" => (0x64, 2), _ => (0, 0) };
    EmulationSettings SettingsFor(DualSenseDevice controller)
    {
        var (volume, gain) = SpeakerLevel(speaker);
        return new()
        {
            Mode = mode == "Xbox" ? EmulationMode.Xbox360 : EmulationMode.DualSense,
            Variant = new VariantSettings { DualSense = controller.IsEdge ? DualSenseVariant.Edge : DualSenseVariant.Standard },
            Forward = new ForwardSettings { StrictNativeHaptics = true, Volume = volume, SpeakerPreGain = gain }
        };
    }
    async ValueTask Connect(CancellationToken cancellation)
    {
        if (!OperatingSystem.IsLinux()) throw new InvalidOperationException(LinuxText.Text("Linux.Unsupported"));
        broker = await LinuxBrokerClient.ConnectAsync(cancellation);
        var capability = await broker.PreflightAsync(cancellation);
        if (!capability.Available) throw new InvalidOperationException(capability.Detail);
        enumerator = new HidDeviceEnumerator();
        var devices = enumerator.Enumerate(0x054c).Where(info => info.ProductId is 0x0ce6 or 0x0df2 && info.UsagePage == 1).ToArray();
        var info = devices.FirstOrDefault(info => info.BusType == ConnectionType.Bluetooth);
        if (info == null) throw new InvalidOperationException(LinuxText.Text(devices.Any(info => info.BusType == ConnectionType.Usb) ? "Bridge.UsbConnected" : "Bridge.PairingNeeded"));
        physicalSession = await broker.AcquirePhysicalAsync(info.Path, cancellation);
        if (!physicalSession.IsolationVerified) throw new InvalidOperationException("Physical controller isolation was not verified.");
        Interlocked.Exchange(ref isolationLost, 0);
        physicalSession.IsolationLost += () => { Interlocked.Exchange(ref isolationLost, 1); Nudge(); };
        metered = new ActivityHidDevice(physicalSession.Device);
        pad = info.ProductId == 0x0df2 ? new DualSenseEdgeDevice(metered, info) : new DualSenseDevice(metered, info);
        // Same production session lengths as Windows; no new physical latency claim.
        pad.BluetoothAudioSessionBufferLengths = [0x40, 0x40, 0x40, 0x40, 0x20];
        lastReport = Environment.TickCount64;
        pad.InputReportReceived += (_, _) => Interlocked.Exchange(ref lastReport, Environment.TickCount64);
        pad.ConnectionStatusChanged += (_, _) => Nudge();
        Directory.CreateDirectory(AppPaths.Config);
        settings = new ControllerInfoService(controllersPath: Path.Combine(AppPaths.Config, "controllers.json"));
        settings.RegisterController(pad.PairingInfo?.ClientMac, info.Path, "DualSense Bluetooth");
        settings.SaveEmulationSettings(pad.PairingInfo?.ClientMac, info.Path, SettingsFor(pad));
        tracker = new ControllerTracker(); actions = new SpecialActionEngineRegistry();
        var session = physicalSession;
        emulation = new EmulationService(tracker, enumerator, settings, null!, actions,
            new AudioApplicationService(), () => new EmulationServer(new EmulationServerOptions
            {
                ListenEndPoint = new IPEndPoint(IPAddress.Loopback, 0),
                AttachStrategy = session.AttachStrategy, Logger = new EmulationLoggerAdapter(),
                ImmediateInputDelivery = true, PackIsoInPayload = true,
                MeasureIsoOutLatency = Program.Arguments.Contains("--diagnostic-audio")
            }), new VirtualControllerFactory(), () => session.IsolationVerified && Volatile.Read(ref isolationLost) == 0);
        emulation.StateChanged += (_, _) => Nudge();
        emulation.Start(); tracker.TrackController(pad); started = Environment.TickCount64;
        state = new(false, L.Text("Bridge.CreatingController"));
    }
    async ValueTask Update()
    {
        if (paused) { if (!authorizationDenied) state = new(false, L.Text("Ui.PausedMessage")); return; }
        if (preview) { state = new(false, LinuxText.Text("Linux.Preview")); return; }
        if (pad == null)
        {
            if (Environment.TickCount64 < nextRetry) return;
            nextRetry = Environment.TickCount64 + 3000;
            using var attempt = CancellationTokenSource.CreateLinkedTokenSource(lifetime.Token);
            lock (attemptLock) connectionAttempt = attempt;
            try { await connect(attempt.Token); }
            catch (UnauthorizedAccessException error)
            {
                await Stop(); paused = true; authorizationDenied = true;
                state = new(false, error.Message, RequiresResume: true); LinuxLog.Append(error.Message);
            }
            catch (OperationCanceledException) when (attempt.IsCancellationRequested && !lifetime.IsCancellationRequested)
            { await Stop(); }
            catch (Exception error) when (!lifetime.IsCancellationRequested)
            { await Stop(); state = new(false, UiMessage.FromException(error).Render()); LinuxLog.Append(state.Message); }
            finally { lock (attemptLock) connectionAttempt = null; }
            return;
        }
        if (pad.IsDisposed || !metered!.IsConnected || Volatile.Read(ref isolationLost) != 0 || !physicalSession!.IsolationVerified || Environment.TickCount64 - Interlocked.Read(ref lastReport) > 5000)
        { await Stop(); nextRetry = Environment.TickCount64 + 1000; state = new(false, L.Text("Bridge.LinkLost")); return; }
        var status = emulation!.GetStatus(pad);
        if (!status.Running && Environment.TickCount64 - started > 20000)
        { await Stop(); nextRetry = Environment.TickCount64 + 10000; state = new(false, L.Text("Bridge.CreationFailed")); return; }
        var headset = pad.InputReport?.Connection.Headphone ?? false;
        if (status.Running && mode != "Xbox" && (!haveHeadset || headset != requestedHeadset))
        { emulation.SetForwardingAudioOutput(pad, headset); requestedHeadset = headset; haveHeadset = true; }
        var activity = metered.Activity; var battery = pad.InputReport?.Battery;
        state = new(status.Running, status.Running ? L.Text(mode == "Xbox" ? "Bridge.ReadyXbox" : "Bridge.ReadyNative") : status.Detail ?? L.Text("Bridge.CreatingController"),
            activity.Haptics, activity.Triggers, activity.Audio, activity.Rumble, battery?.DisplayPercentage ?? -1,
            battery?.PowerState is BatteryPowerState.Charging or BatteryPowerState.ChargingComplete, headset);
    }
    async ValueTask Stop()
    {
        if (physicalSession != null) { lastMetrics = MetricsText(); LinuxLog.Append("Session metrics: " + lastMetrics); }
        state = new(false, L.Text("Ui.Releasing"));
        // Stop every virtual output writer before resetting/releasing the physical.
        try { emulation?.Dispose(); }
        finally
        {
            emulation = null;
            try
            {
                if (pad != null)
                {
                    var released = pad; pad = null;
                    try { tracker?.UntrackController(released); actions?.Remove(released); }
                    finally { try { released.ResetOutputs(); } catch (Exception error) { LinuxLog.Append(error.Message); } released.Dispose(); }
                }
                else metered?.Dispose();
            }
            finally
            {
                metered = null; haveHeadset = false;
                try
                {
                    try { actions?.Dispose(); }
                    finally { try { tracker?.Dispose(); } finally { enumerator?.Dispose(); } }
                }
                finally
                {
                    actions = null; tracker = null; enumerator = null; settings = null;
                    try { if (physicalSession != null) await physicalSession.DisposeAsync(); }
                    finally { physicalSession = null; if (broker != null) await broker.DisposeAsync(); broker = null; }
                }
            }
        }
    }
    async Task Run()
    {
        try
        {
            while (!lifetime.IsCancellationRequested)
            {
                try
                {
                    while (commands.Reader.TryRead(out var command)) await command();
                    await Update();
                }
                catch (OperationCanceledException) when (lifetime.IsCancellationRequested) { break; }
                catch (Exception error)
                {
                    LinuxLog.Append(error.ToString());
                    try { await Stop(); } catch (Exception cleanup) { LinuxLog.Append("Cleanup: " + cleanup); }
                    paused = true; authorizationDenied = true;
                    state = new(false, error.Message, RequiresResume: true);
                }
                await wake.WaitAsync(500, lifetime.Token);
            }
        }
        catch (OperationCanceledException) when (lifetime.IsCancellationRequested) { }
        finally { try { await Stop(); } catch (Exception error) { LinuxLog.Append("Final cleanup: " + error); } }
    }
    public async ValueTask DisposeAsync()
    {
        commands.Writer.TryComplete(); lifetime.Cancel(); Nudge();
        try { await worker; } finally { lifetime.Dispose(); wake.Dispose(); }
    }
}

sealed class LinuxLog : ILogSink
{
    static readonly Lock Gate = new();
    static string PathName => Path.Combine(AppPaths.State, "madsense.log");
    public void Write(in LogEntry entry) => Append($"{entry.Level} [{entry.Category}] {entry.Message}");
    public static void Append(string message)
    {
        try
        {
            lock (Gate)
            {
                Directory.CreateDirectory(AppPaths.State);
                if (File.Exists(PathName) && new FileInfo(PathName).Length >= 2 * 1024 * 1024) File.Move(PathName, PathName + ".previous", true);
                File.AppendAllText(PathName, $"{DateTimeOffset.Now:O} {message}\n");
            }
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException) { }
    }
    public static string Recent()
    {
        try { lock (Gate) return string.Join('\n', File.ReadLines(PathName).TakeLast(150)); }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException) { return ""; }
    }
}
