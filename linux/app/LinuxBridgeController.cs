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

sealed record LinuxBridgeState(bool Ready, string Message, bool Haptics = false, bool Triggers = false, bool Audio = false, bool Rumble = false, int Battery = -1, bool Charging = false, bool Headset = false, bool RequiresResume = false, bool Paused = false, bool Releasing = false);

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
    readonly Func<CancellationToken, ValueTask<LinuxBrokerPreflight>> cleanupProbe;
    readonly Lock attemptLock = new();
    CancellationTokenSource? connectionAttempt;
    Task? disposal;
    int transitions;
    string lastMetrics = "No broker session has been opened.";
    LinuxBridgeState state = new(false, L.Text("Ui.PausedMessage"), Paused: true);
    string mode, speaker;
    bool paused = true, requestedHeadset, haveHeadset, authorizationDenied;
    bool acquisitionRequested, cleanupPending;
    LinuxBridgeState? resumeRequiredState;
    string lastCleanupError = "";
    long nextRetry, lastReport, started, nextCleanupCheck;
    HidDeviceEnumerator? enumerator;
    LinuxBrokerClient? broker;
    LinuxPhysicalSession? physicalSession;
    ActivityHidDevice? metered;
    DualSenseDevice? pad;
    ControllerTracker? tracker;
    SpecialActionEngineRegistry? actions;
    ControllerInfoService? settings;
    EmulationService? emulation;
    public LinuxBridgeState State => Volatile.Read(ref transitions) > 0
        ? new(false, L.Text("Ui.Releasing"), Releasing: true) : Volatile.Read(ref state);
    public string DiagnosticText => $"MadSense Linux 0.5.0\n{Environment.OSVersion}\n{System.Runtime.InteropServices.RuntimeInformation.ProcessArchitecture}\n\n{State.Message}\n\n" + MetricsText() + "\n\n" + LinuxLog.Recent();
    string MetricsText()
    {
        var current = Volatile.Read(ref physicalSession);
        return current == null ? Volatile.Read(ref lastMetrics) : JsonSerializer.Serialize(new
        {
            scope = "Software broker IPC and client queue only; no radio, firmware or physical actuator latency",
            isolationVerified = current.IsolationVerified, isolationLostReason = current.IsolationLostReason,
            metrics = current.Metrics
        }, new JsonSerializerOptions { WriteIndented = true });
    }
    internal LinuxBridgeController(bool preview, string mode, string speaker, Func<CancellationToken, ValueTask>? connectOverride = null,
        Func<CancellationToken, ValueTask<LinuxBrokerPreflight>>? cleanupOverride = null)
    {
        this.preview = preview; this.mode = mode; this.speaker = speaker;
        connect = connectOverride ?? Connect;
        cleanupProbe = cleanupOverride ?? ProbeCleanup;
        if (!preview) DualSenseClientLogger.Configure(LogLevel.Info, new LinuxLog());
        worker = Task.Run(Run);
    }
    void Nudge() { try { if (wake.CurrentCount == 0) wake.Release(); } catch (ObjectDisposedException) { } catch (SemaphoreFullException) { } }
    void Send(Func<ValueTask> action, bool cancelConnection = false, bool transition = false)
    {
        lock (attemptLock)
        {
            if (disposal != null || lifetime.IsCancellationRequested) return;
            if (transition) Interlocked.Increment(ref transitions);
            if (!commands.Writer.TryWrite(async () =>
            {
                try { await action(); }
                finally { if (transition) Interlocked.Decrement(ref transitions); }
            }))
            { if (transition) Interlocked.Decrement(ref transitions); return; }
            // Publishing the command and cancelling an attempt share its
            // registration lock; a new attempt cannot slip between the two.
            if (cancelConnection) connectionAttempt?.Cancel();
        }
        Nudge();
    }
    public void SetPaused(bool value)
    {
        Send(async () => { paused = value; authorizationDenied = false; resumeRequiredState = null; if (value) await Stop(); else nextRetry = 0; },
            cancelConnection: value, transition: value);
    }
    public void SetMode(string value)
    {
        Send(async () => { if (value != mode) { await Stop(); mode = value; nextRetry = 0; } },
            cancelConnection: true, transition: true);
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
        MarkAcquisitionRequested();
        physicalSession = await broker.AcquirePhysicalAsync(info.Path, cancellation);
        // Setup performs synchronous feature RPCs before its input reader and
        // virtual creation start. Cancel this exact setup connection to wake
        // them as well; an established session no longer has this registration.
        var setupBroker = broker;
        using var abortSetup = cancellation.Register(() => { _ = setupBroker.DisposeAsync(); });
        cancellation.ThrowIfCancellationRequested();
        if (!physicalSession.IsolationVerified) throw new InvalidOperationException("Physical controller isolation was not verified.");
        // A queued notification from an old session may wake this worker but
        // must never mark a newly acquired session as lost.
        physicalSession.IsolationLost += Nudge;
        metered = new ActivityHidDevice(physicalSession.Device);
        pad = info.ProductId == 0x0df2 ? new DualSenseEdgeDevice(metered, info) : new DualSenseDevice(metered, info);
        cancellation.ThrowIfCancellationRequested();
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
                AcknowledgeInterruptOutBeforeDispatch = true,
                MeasureIsoOutLatency = Program.Arguments.Contains("--diagnostic-audio")
            }), new VirtualControllerFactory(), () => session.IsolationVerified);
        emulation.StateChanged += (_, _) => Nudge();
        cancellation.ThrowIfCancellationRequested();
        emulation.Start(); tracker.TrackController(pad); started = Environment.TickCount64;
        state = new(false, L.Text("Bridge.CreatingController"));
    }
    async ValueTask Update()
    {
        if (cleanupPending) { await CheckCleanup(); return; }
        if (paused)
        {
            state = authorizationDenied && resumeRequiredState != null
                ? resumeRequiredState with { Paused = true }
                : new(false, L.Text("Ui.PausedMessage"), Paused: true);
            return;
        }
        if (preview) { state = new(false, LinuxText.Text("Linux.Preview")); return; }
        if (pad == null)
        {
            if (Environment.TickCount64 < nextRetry) return;
            nextRetry = Environment.TickCount64 + 3000;
            using var attempt = CancellationTokenSource.CreateLinkedTokenSource(lifetime.Token);
            lock (attemptLock)
            {
                if (commands.Reader.TryPeek(out _)) return;
                connectionAttempt = attempt;
            }
            try { await connect(attempt.Token); }
            catch (UnauthorizedAccessException error)
            {
                await Stop(); paused = true; authorizationDenied = true;
                resumeRequiredState = new(false, error.Message, RequiresResume: true, Paused: !cleanupPending);
                state = cleanupPending ? RecoveryState(error.Message) : resumeRequiredState; LinuxLog.Append(error.Message);
            }
            catch (OperationCanceledException) when (attempt.IsCancellationRequested && !lifetime.IsCancellationRequested)
            { await Stop(); }
            catch (Exception error) when (!lifetime.IsCancellationRequested)
            {
                await Stop(); string detail = UiMessage.FromException(error).Render();
                state = cleanupPending ? RecoveryState(detail) : new(false, detail); LinuxLog.Append(detail);
            }
            finally { lock (attemptLock) connectionAttempt = null; }
            return;
        }
        if (pad.IsDisposed || !metered!.IsConnected || !physicalSession!.IsolationVerified || Environment.TickCount64 - Interlocked.Read(ref lastReport) > 5000)
        { await Stop(); nextRetry = Environment.TickCount64 + 1000; if (!cleanupPending) state = new(false, L.Text("Bridge.LinkLost")); return; }
        var status = emulation!.GetStatus(pad);
        if (!status.Running && Environment.TickCount64 - started > 20000)
        { await Stop(); nextRetry = Environment.TickCount64 + 10000; if (!cleanupPending) state = new(false, L.Text("Bridge.CreationFailed")); return; }
        var headset = pad.InputReport?.Connection.Headphone ?? false;
        if (status.Running && mode != "Xbox" && (!haveHeadset || headset != requestedHeadset))
        { emulation.SetForwardingAudioOutput(pad, headset); requestedHeadset = headset; haveHeadset = true; }
        var activity = metered.Activity; var battery = pad.InputReport?.Battery;
        bool ready = status.Running && pad.InputReport != null;
        state = new(ready, ready ? L.Text(mode == "Xbox" ? "Bridge.ReadyXbox" : "Bridge.ReadyNative") : status.Detail ?? L.Text(status.Running ? "Bridge.Waiting" : "Bridge.CreatingController"),
            activity.Haptics, activity.Triggers, activity.Audio, activity.Rumble, battery?.DisplayPercentage ?? -1,
            battery?.PowerState is BatteryPowerState.Charging or BatteryPowerState.ChargingComplete, headset);
    }
    async ValueTask Stop()
    {
        cleanupPending |= acquisitionRequested;
        acquisitionRequested = false;
        bool acknowledged = false;
        if (physicalSession != null) { lastMetrics = MetricsText(); LinuxLog.Append("Session metrics: " + lastMetrics); }
        state = new(false, L.Text("Ui.Releasing"), Releasing: true);
        // A creation may hold EmulationService's disposal gate while waiting
        // for attach. Abort only that pending RPC before waiting for the gate.
        physicalSession?.CancelPendingAttach();
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
                    try
                    {
                        if (physicalSession != null)
                        {
                            await physicalSession.DisposeAsync();
                            acknowledged = physicalSession.CleanupConfirmed;
                        }
                    }
                    finally { physicalSession = null; if (broker != null) await broker.DisposeAsync(); broker = null; }
                }
            }
        }
        if (acknowledged) cleanupPending = false;
        if (cleanupPending) LinuxLog.Append("Controller release is unconfirmed; waiting for the helper's recovery status.");
        state = cleanupPending ? RecoveryState(lastCleanupError)
            : new(false, L.Text(paused ? "Ui.PausedMessage" : "Bridge.Waiting"), Paused: paused);
    }
    internal void MarkAcquisitionRequested() => acquisitionRequested = true;
    static LinuxBridgeState RecoveryState(string detail) => new(false,
        LinuxText.Text("Linux.RecoveryPending") + (detail.Length == 0 ? "" : " · " + detail), Releasing: true);
    static async ValueTask<LinuxBrokerPreflight> ProbeCleanup(CancellationToken cancellation)
    {
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellation);
        timeout.CancelAfter(TimeSpan.FromSeconds(2));
        await using var probe = await LinuxBrokerClient.ConnectAsync(timeout.Token);
        return await probe.PreflightAsync(timeout.Token);
    }
    async ValueTask CheckCleanup()
    {
        if (Environment.TickCount64 < nextCleanupCheck) return;
        LinuxBrokerPreflight result;
        try { result = await cleanupProbe(lifetime.Token); }
        catch (OperationCanceledException) when (lifetime.IsCancellationRequested) { throw; }
        catch (Exception error) { result = new(false, error.Message); }
        nextCleanupCheck = Environment.TickCount64 + 1000;
        if (result.CleanupConfirmed)
        {
            cleanupPending = false; lastCleanupError = ""; nextRetry = 0;
            LinuxLog.Append("The helper confirmed no active lease or pending recovery.");
            state = paused ? resumeRequiredState is { } reason ? reason with { Paused = true, Releasing = false }
                : new(false, L.Text("Ui.PausedMessage"), Paused: true) : new(false, L.Text("Bridge.Waiting"));
            return;
        }
        string detail = result.Available && (result.LeaseActive == null || result.RecoveryPending == null)
            ? LinuxText.Text("Linux.RecoveryStatusUnsupported") : result.Detail;
        if (detail != lastCleanupError) LinuxLog.Append("Controller release remains unconfirmed: " + detail);
        lastCleanupError = detail;
        state = RecoveryState(detail);
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
                    resumeRequiredState = new(false, error.Message, RequiresResume: true);
                    state = cleanupPending ? RecoveryState(error.Message) : resumeRequiredState;
                }
                await wake.WaitAsync(500, lifetime.Token);
            }
        }
        catch (OperationCanceledException) when (lifetime.IsCancellationRequested) { }
        finally { try { await Stop(); } catch (Exception error) { LinuxLog.Append("Final cleanup: " + error); } }
    }
    public ValueTask DisposeAsync()
    {
        lock (attemptLock)
        {
            if (disposal == null)
            {
                commands.Writer.TryComplete(); lifetime.Cancel(); Nudge();
                disposal = FinishDisposal();
            }
            return new(disposal);
        }
    }
    async Task FinishDisposal() { try { await worker; } finally { lifetime.Dispose(); wake.Dispose(); } }
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
