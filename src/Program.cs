using System.Diagnostics;
using System.Net;
using System.Text.Json;
using DualSenseClient.Controllers;
using DualSenseClient.Controllers.Devices;
using DualSenseClient.Controllers.DualSense.Audio;
using DualSenseClient.Controllers.DualSense.Enum;
using DualSenseClient.Controllers.Emulation;
using DualSenseClient.Controllers.SpecialActions;
using DualSenseClient.Emulation;
using DualSenseClient.Hid;
using DualSenseClient.HidHide;
using DualSenseClient.Settings;
using DualSenseClient.Settings.Sections;
using DualSenseClient.Logging;
using DualSenseClient.Controllers.DualSense.Output;
using DualSenseClient.Controllers.DualSense.Triggers;
using DualSenseClient.Core.Utilities;

namespace MadSense;

static class Program
{
    [STAThread]
    static int Main(string[] args)
    {
        if (InstallerMaintenance.TryRun(args, out var maintenanceExit)) return maintenanceExit;
        Directory.SetCurrentDirectory(AppContext.BaseDirectory);
        L.Use(Preferences.LoadLanguage());
        DualSenseClientLogger.Configure(LogLevel.Info, new LocalLog());
        if (args.Contains("--stop"))
        {
            if (EventWaitHandle.TryOpenExisting("Local\\MadSense-Stop", out var stop)) { using (stop) stop.Set(); }
            return 0;
        }
        using var singleton = new Mutex(true, "Local\\MadSense-0.1", out var first);
        if (!first)
        {
            if (EventWaitHandle.TryOpenExisting("Local\\MadSense-Show", out var show)) { using (show) show.Set(); }
            return 2;
        }
        if (args.Contains("--diagnose"))
        {
            using var enumerator = new HidDeviceEnumerator();
            var data = enumerator.Enumerate(0x054c).Select(d => new { d.ProductName, d.Path, d.ProductId, Bus = d.BusType.ToString(), d.UsagePage, Usage = d.Usage.ToString() });
            File.WriteAllText("diagnostic.json", JsonSerializer.Serialize(data, new JsonSerializerOptions { WriteIndented = true }));
            return 0;
        }
        // The bridge competes with a game for CPU time. Above-normal process priority keeps the
        // input reader, the Bluetooth pump and the USB/IP session scheduled ahead of the game's
        // worker threads; it needs no elevation and leaves the game's own priority untouched.
        try { using var self = Process.GetCurrentProcess(); self.PriorityClass = ProcessPriorityClass.AboveNormal; } catch { }
        if (args.Contains("--probe") || args.Contains("--soak") || args.Contains("--exercise"))
        {
            using var stop = new EventWaitHandle(false, EventResetMode.ManualReset, "Local\\MadSense-Stop");
            using var installerStop = InstallerStopSignal.Create();
            using var bridge = new Bridge { MeasureAudio = args.Contains("--diagnostic-audio") };
            bridge.SetMode(args.Contains("--xbox") ? "Xbox" : "Native", false);
            try
            {
                bridge.Start();
                for (var i = 0; i < (args.Contains("--soak") ? 900 : 20); i++)
                {
                    if (WaitHandle.WaitAny([stop, installerStop], 1000) != WaitHandle.WaitTimeout) break;
                    bridge.Update(); bridge.Log(bridge.Status);
                    if (i == 5 && args.Contains("--exercise")) bridge.ExerciseHostOutput();
                    if (i == 2 && args.Contains("--exercise-stream")) bridge.ExerciseHostOutput();
                }
                return bridge.Running ? 0 : 3;
            }
            catch (Exception e) { bridge.Log(e.ToString()); return 1; }
        }
        ApplicationConfiguration.Initialize();
        Application.Run(new MainWindow(startHidden: args.Contains("--tray")));
        return 0;
    }
}

/// <summary>Live feedback the physical pad is receiving, sampled for the UI.</summary>
sealed record BridgeSignals(bool Haptics, bool Triggers, bool Audio, bool Rumble, int Battery, bool Charging, bool Headset);

sealed class Bridge : IDisposable
{
    readonly HidDeviceEnumerator enumerator = new();
    readonly ControllerTracker tracker = new();
    readonly HidHideService hiding = new();
    readonly SpecialActionEngineRegistry actions = new();
    readonly ControllerInfoService settings = new(controllersPath: Path.Combine(AppContext.BaseDirectory, "controllers.json"));
    EmulationService? emulation;
    DualSenseDevice? controller;
    string? newlyHidden;
    bool reconnect;
    long nextRetry;
    UiMessage? lastConnectionError;
    bool? headphones;
    bool hideVerified, readyAnnounced;
    bool inputTimingRequested;
    long reports, lastReport;
    long connectingSince, nextHideAttempt;
    public string Mode { get; private set; } = Preferences.LoadMode();
    public void SetMode(string mode, bool persist = true)
    {
        if (mode is not ("Auto" or "Native" or "Xbox")) throw new ArgumentException(L.Text("Bridge.InvalidMode"));
        if (mode == Mode) return;
        var resume = reconnect;
        Stop(); Mode = mode;
        if (persist) { try { Preferences.SaveMode(mode); } catch (Exception e) { Log(L.Message("Bridge.SaveModeFailed", e.Message)); } }
        Log(L.Message("Bridge.ModeChanged", mode == "Xbox" ? "Xbox" : "DualSense"));
        if (resume) Start();
    }
    /// <summary>Speaker level ("Alto", "Medio", "Bajo", "Off"); applied live to the forwarder and written into controllers.json on connect.</summary>
    public string Speaker { get; private set; } = Preferences.LoadSpeaker();
    public void SetSpeaker(string level)
    {
        if (!SpeakerLevels.IsValid(level)) throw new ArgumentException(L.Text("Bridge.InvalidSpeaker"));
        if (level == Speaker) return;
        Speaker = level;
        try { Preferences.SaveSpeaker(level); } catch (Exception e) { Log(L.Message("Bridge.SaveSpeakerFailed", e.Message)); }
        var (volume, preGain) = SpeakerLevels.Resolve(level);
        if (controller != null && emulation != null)
        {
            emulation.SetForwardingSpeaker(controller, volume, preGain);
            if (padPath != null) settings.SaveEmulationSettings(controller.PairingInfo?.ClientMac, padPath, EmulationSettingsFor(controller));
        }
        Log(L.Message(level switch { "Alto" => "Bridge.SpeakerHigh", "Off" => "Bridge.SpeakerOff", _ => "Bridge.SpeakerLow" }));
    }
    /// <summary>The settings MadSense writes for the pad: mode, variant, strict native haptics and the chosen speaker level.</summary>
    EmulationSettings EmulationSettingsFor(DualSenseDevice pad)
    {
        var (volume, preGain) = SpeakerLevels.Resolve(Speaker);
        return new EmulationSettings
        {
            Mode = Mode == "Xbox" ? EmulationMode.Xbox360 : EmulationMode.DualSense,
            Variant = new VariantSettings { DualSense = pad.IsEdge ? DualSenseVariant.Edge : DualSenseVariant.Standard },
            // Speaker: volume byte at the top of the pad's usable range (0x3D-0x64) and pre-gain up to 7; both heard on hardware, 0x50/2 was clearly quiet.
            Forward = new ForwardSettings { StrictNativeHaptics = true, Volume = volume, SpeakerPreGain = preGain }
        };
    }
    MeteredHid? metered;
    string? padPath;
    /// <summary>Pad-side haptics buffer byte; MADSENSE_BT_HAPTIC_BUFFER (hex, 0x08-0x40) overrides the default for A/B tests.</summary>
    static readonly byte HapticBufferByte = ReadHapticBufferByte();
    static byte ReadHapticBufferByte()
    {
        try
        {
            var raw = Environment.GetEnvironmentVariable("MADSENSE_BT_HAPTIC_BUFFER");
            if (!string.IsNullOrWhiteSpace(raw)) return (byte)Math.Clamp(Convert.ToInt32(raw.Trim().Replace("0x", "", StringComparison.OrdinalIgnoreCase), 16), 0x08, 0x40);
        }
        catch { }
        return 0x20;
    }
    public event Action<UiMessage>? Message;
    /// <summary>Raised from device threads when the bridge wants its owner to call <see cref="Update"/> soon.</summary>
    public event Action? WakeRequested;
    public bool MeasureAudio { get; init; }
    public bool Running => controller != null && emulation?.GetStatus(controller).Running == true;
    public bool Ready => Running && hideVerified;
    public bool Waiting => controller == null && reconnect;
    /// <summary>Milliseconds until the next scheduled connection attempt; 1000 while connected or paused.</summary>
    public int MillisUntilDue => Waiting ? (int)Math.Clamp(nextRetry - Environment.TickCount64, 0, 1000) : 1000;
    /// <summary>A device appeared or vanished: retry the connection soon instead of waiting the full back-off.</summary>
    public void RequestRetry()
    {
        if (Waiting) nextRetry = Math.Min(nextRetry, Environment.TickCount64 + 400);
    }
    public BridgeSignals Signals
    {
        get
        {
            var report = controller?.InputReport;
            var battery = report?.Battery;
            var now = Environment.TickCount64;
            return new BridgeSignals(
                metered != null && now - metered.LastHapticTicks < 1500,
                metered?.TriggersActive == true,
                metered != null && now - metered.LastAudioTicks < 1500,
                metered?.RumbleActive == true,
                battery?.DisplayPercentage ?? -1,
                battery?.PowerState is BatteryPowerState.Charging or BatteryPowerState.ChargingComplete,
                report?.Connection.Headphone ?? false);
        }
    }
    public UiMessage DisplayStatus => controller == null ? (reconnect ? (lastConnectionError ?? L.Message("Bridge.Waiting")) : L.Message("Bridge.Paused")) :
        L.Message("Bridge.ConnectionDetails",
            L.Message("Bridge.Battery", controller.InputReport?.Battery.Percentage?.ToString() ?? "?"),
            controller.InputReport?.Connection.Headphone == true ? L.Message("Bridge.HeadsetSuffix") : UiMessage.Raw(""),
            Ready ? L.Message(Mode == "Xbox" ? "Bridge.ConnectedXbox" : "Bridge.ConnectedNative") : Running ? L.Message("Bridge.HidingController") : emulation?.GetStatus(controller).IsCreating == true ? L.Message("Bridge.CreatingController") : L.Message("Bridge.ActivationFailed"));
    public string Status => controller == null ? L.Text("Bridge.Disconnected") :
        L.Text("Bridge.Diagnostics", Interlocked.Read(ref reports), controller.InputReport?.Battery.Percentage?.ToString() ?? "?", metered?.Summary) + "\n" +
        (emulation?.GetStatus(controller) is { } s ? (s.Running ? L.Text("Bridge.VirtualActive") : s.IsCreating ? L.Text("Bridge.CreatingDualSense") : s.Detail ?? L.Text("Bridge.Pending")) : L.Text("Bridge.ControllerConnected"));
    public void Log(string text) => Log(UiMessage.Raw(text));
    public void Log(UiMessage message)
    {
        LogFiles.Append("madsense.log", $"{DateTimeOffset.Now:O} {message.Render()}\n");
        Message?.Invoke(message);
    }
    public void Start()
    {
        reconnect = true;
        nextRetry = 0; Update();
    }
    void Connect()
    {
        if (controller != null) return;
        var competitors = Process.GetProcessesByName("DSX").Concat(Process.GetProcessesByName("SenseBridge")).ToArray();
        var competing = competitors.Length > 0;
        foreach (var process in competitors) process.Dispose();
        if (competing)
            throw new LocalizedException(L.Message("Bridge.CompetingApp"));
        if (!hiding.IsAvailable) throw new LocalizedException(L.Message("Bridge.HidHideMissing"));
        RecoverHiddenController();
        hiding.EnsureSelfVisible();
        if (!hiding.IsAppWhitelisted) throw new LocalizedException(L.Message("Bridge.HidHideBlocked"));
        var pads = enumerator.Enumerate(0x054c).Where(x => (x.ProductId == 0x0ce6 || x.ProductId == 0x0df2) && x.UsagePage == 1).ToList();
        var info = pads.FirstOrDefault(x => x.BusType == ConnectionType.Bluetooth);
        if (info == null)
        {
            // The virtual pad is excluded from this enumeration, so a USB DualSense here is the
            // real one on a cable: it already works natively and does not need the bridge.
            if (pads.Any(x => x.BusType == ConnectionType.Usb))
                throw new LocalizedException(L.Message("Bridge.UsbConnected"));
            throw new LocalizedException(L.Message("Bridge.PairingNeeded"));
        }
        try
        {
            padPath = info.Path;
            metered = new MeteredHid(enumerator.OpenDevice(info.Path)) { Faulted = () => WakeRequested?.Invoke() };
            controller = info.ProductId == 0x0df2 ? new DualSenseEdgeDevice(metered, info) : new DualSenseDevice(metered, info);
            // Measured on 2026-10-31 with tools/HapticLatencyProbe --direct: the fifth session-block
            // byte sets how much haptics the pad's firmware buffers before playing. 0x40 (the
            // reference every bridge sends) = 74 ms from report to actuator; 0x20 = 42 ms with the
            // same continuity on a 3 s tone; 0x10 = 30 ms but ~4 % of 20 ms windows starve.
            // The other four bytes stay at 0x40 so the speaker lane is untouched.
            controller.BluetoothAudioSessionBufferLengths = [0x40, 0x40, 0x40, 0x40, HapticBufferByte];
            Interlocked.Exchange(ref lastReport, Environment.TickCount64);
            controller.InputReportReceived += (_, _) => { Interlocked.Increment(ref reports); Interlocked.Exchange(ref lastReport, Environment.TickCount64); };
            // Headset plugged/unplugged: reroute audio on the next worker pass instead of the next second.
            controller.ConnectionStatusChanged += (_, _) => WakeRequested?.Invoke();
            settings.RegisterController(controller.PairingInfo?.ClientMac, info.Path, "DualSense Bluetooth");
            settings.SaveEmulationSettings(controller.PairingInfo?.ClientMac, info.Path, EmulationSettingsFor(controller));
            // Bluetooth ControllerEndpoint is delivered directly by USB/IP. No local
            // audio engine is needed: that dependency serves wired pads / loopback only.
            emulation = new EmulationService(tracker, enumerator, settings, null!, actions,
                new AudioApplicationService(), () => new EmulationServer(new EmulationServerOptions { ListenEndPoint = new IPEndPoint(IPAddress.Loopback, 0), Logger = new EmulationLoggerAdapter(), ImmediateInputDelivery = true, MeasureIsoOutLatency = MeasureAudio }), new VirtualControllerFactory());
            // The USB/IP interrupt endpoint also uses 1 ms timers while audio is idle.
            // Keep a process-scoped timing lease for the connected bridge, not only audio.
            TimerResolution.AddRef(); inputTimingRequested = true;
            // The virtual pad appears a few hundred ms later on a background creation: run the
            // HidHide check and the ready announcement right then, not on the next 1 s pass.
            emulation.StateChanged += (_, _) => WakeRequested?.Invoke();
            emulation.Start();
            tracker.TrackController(controller);
            connectingSince = Environment.TickCount64;
            lastConnectionError = null;
            readyAnnounced = false;
            Log(L.Message(Mode == "Xbox" ? "Bridge.StartingXbox" : "Bridge.StartingDualSense"));
        }
        catch { try { Stop(); } finally { reconnect = true; } throw; }
    }
    public void Update()
    {
        if (controller == null)
        {
            if (reconnect && Environment.TickCount64 >= nextRetry)
            {
                nextRetry = Environment.TickCount64 + 3000;
                try { Connect(); }
                catch (Exception e)
                {
                    var message = UiMessage.FromException(e);
                    if (lastConnectionError?.Render() != message.Render()) Log(message);
                    lastConnectionError = message;
                }
            }
            return;
        }
        if (controller.IsDisposed || metered?.LinkLost == true || Environment.TickCount64 - Interlocked.Read(ref lastReport) > 5000)
        {
            Log(L.Message(metered?.LinkLost == true ? "Bridge.LinkLost" : "Bridge.InputTimedOut"));
            // The link is gone: reconnect as soon as the pad shows up again, no long back-off.
            try { Stop(); } finally { reconnect = true; nextRetry = Environment.TickCount64 + 1000; } return;
        }
        if (!Running && Environment.TickCount64 - connectingSince > 20000)
        {
            Log(L.Message("Bridge.CreationFailed"));
            try { Stop(); } finally { reconnect = true; nextRetry = Environment.TickCount64 + 10000; } return;
        }
        var headset = controller.InputReport?.Connection.Headphone ?? false;
        if (Running && Mode != "Xbox" && headphones != headset)
        {
            emulation!.SetForwardingAudioOutput(controller, headset);
            // Only a change during the session is news; the initial route is implied by "listo".
            if (headphones != null) Log(L.Message(headset ? "Bridge.HeadsetConnected" : "Bridge.HeadsetDisconnected"));
            headphones = headset;
        }
        // Hide only after virtual attachment succeeds. Never leave a controller hidden after a failed attach.
        hideVerified = false;
        if (Running && Environment.TickCount64 >= nextHideAttempt && hiding.IsAvailable && hiding.TryGetInstanceId(controller.Info.Path, out var id) && (!hiding.IsControllerHidden(id) || !hiding.GlobalHidingEnabled))
        {
            nextHideAttempt = Environment.TickCount64 + 10000;
            if (newlyHidden == null)
            {
                File.WriteAllText("hidden-controller.json.tmp", JsonSerializer.Serialize(new HideLease(id, hiding.IsControllerHidden(id), hiding.GlobalHidingEnabled)));
                File.Move("hidden-controller.json.tmp", "hidden-controller.json", true);
            }
            newlyHidden = id;
            hiding.SetControllerHidden(id, true);
            hiding.GlobalHidingEnabled = true;
            if (!hiding.IsControllerHidden(id) || !hiding.GlobalHidingEnabled) { Log(L.Message("Bridge.HideFailed")); return; }
            Log(L.Message("Bridge.Hidden"));
        }
        hideVerified = Running && hiding.TryGetInstanceId(controller.Info.Path, out var hiddenId) && hiding.IsControllerHidden(hiddenId) && hiding.GlobalHidingEnabled;
        if (hideVerified && !readyAnnounced)
        {
            readyAnnounced = true;
            Log(L.Message(Mode == "Xbox" ? "Bridge.ReadyXbox" : "Bridge.ReadyNative"));
        }
    }
    public void ExerciseHostOutput()
    {
        if (!Running || controller == null) throw new LocalizedException(L.Message("Bridge.NotRunning"));
        using var host = enumerator.OpenDevice(emulation!.GetStatus(controller).VirtualDevicePath!);
        var payload = new OutputReportBuilder { LedBlue = 80, L2TriggerEffect = TriggerEffectBuilder.Feedback(3, 2), R2TriggerEffect = TriggerEffectBuilder.Feedback(3, 2) }.Build();
        var raw = new byte[48];
        var report = OutputReport.ForUsb(payload); report.Raw.CopyTo(raw, 0);
        var before = metered!.Writes;
        var triggersBefore = metered.ActiveTriggerWrites;
        if (host.Write(raw, 0, raw.Length) < raw.Length) throw new IOException(L.Text("Bridge.IncompleteWrite"));
        Thread.Sleep(350);
        if (metered.Writes <= before || metered.ActiveTriggerWrites <= triggersBefore || metered.Errors != 0) throw new IOException(L.Text("Bridge.TriggerTestFailed"));
        controller.ResetOutputs();
        Log(L.Message("Bridge.OutputTest", before, metered.Writes, metered.Errors));
    }
    public void Stop()
    {
        reconnect = false;
        hideVerified = false;
        readyAnnounced = false;
        try
        {
            emulation?.Dispose();
        }
        finally
        {
            emulation = null;
            try
            {
                if (controller != null)
                {
                    var released = controller; controller = null;
                    try { tracker.UntrackController(released); }
                    finally
                    {
                        try { actions.Remove(released); }
                        finally { try { released.ResetOutputs(); } catch { } released.Dispose(); }
                    }
                }
                else metered?.Dispose();
            }
            finally
            {
                metered = null;
                headphones = null;
                try { if (newlyHidden != null) { RecoverHiddenController(); newlyHidden = null; } }
                finally
                {
                    Interlocked.Exchange(ref reports, 0);
                    if (inputTimingRequested) { inputTimingRequested = false; TimerResolution.Release(); }
                }
            }
        }
    }
    void RecoverHiddenController()
    {
        HiddenControllerRecovery.Recover(AppContext.BaseDirectory, () => new HidHideRecoveryState());
    }
    public void Dispose()
    {
        try { Stop(); }
        finally { try { actions.Dispose(); } finally { try { tracker.Dispose(); } finally { enumerator.Dispose(); } } }
    }
}

sealed class LocalLog : ILogSink
{
    public void Write(in LogEntry entry)
    {
        LogFiles.Append("engine.log", $"{entry.Timestamp:O} {entry.Level} [{entry.Category}] {entry.Message}\n");
    }
}

/// <summary>
/// Wraps the physical HID handle to count what actually reaches the pad (per report id, haptic
/// payloads, trigger effects) and to notice a dead link the moment a read fails, so the bridge
/// can tear down and wait for the pad without the five-second silence timeout.
/// </summary>
sealed class MeteredHid(IHidDevice inner) : IHidDevice
{
    readonly long[] counts = new long[256];
    long writes, errors, nonzeroHaptics, triggerEffects, lastHaptic, lastAudio;
    int linkLost, rightTrigger, leftTrigger, rumble;
    public long Writes => Interlocked.Read(ref writes);
    public long Errors => Interlocked.Read(ref errors);
    public long ActiveTriggerWrites => Interlocked.Read(ref triggerEffects);
    /// <summary>Tick of the last report that carried a non-silent haptic payload.</summary>
    public long LastHapticTicks => Interlocked.Read(ref lastHaptic);
    /// <summary>Tick of the last audio-lane report (0x35/0x36): the game has the speaker/haptics stream open.</summary>
    public long LastAudioTicks => Interlocked.Read(ref lastAudio);
    /// <summary>Whether either trigger currently holds a non-Off effect, as last written.</summary>
    public bool TriggersActive => Volatile.Read(ref rightTrigger) != 0 || Volatile.Read(ref leftTrigger) != 0;
    /// <summary>Whether the last classic-rumble write left a motor running.</summary>
    public bool RumbleActive => Volatile.Read(ref rumble) != 0;
    /// <summary>True once a read failed: the Bluetooth link is gone.</summary>
    public bool LinkLost => Volatile.Read(ref linkLost) != 0;
    /// <summary>Invoked once, on the reader thread, when the link is first seen dead.</summary>
    public Action? Faulted { get; init; }
    public string Summary => L.Text("Bridge.TxSummary", Interlocked.Read(ref counts[0x31]), Interlocked.Read(ref counts[0x32]), Interlocked.Read(ref counts[0x35]), Interlocked.Read(ref counts[0x36]), Errors, Interlocked.Read(ref nonzeroHaptics), Interlocked.Read(ref triggerEffects));
    public ushort VendorId => inner.VendorId;
    public ushort ProductId => inner.ProductId;
    public string DevicePath => inner.DevicePath;
    public bool IsConnected => inner.IsConnected;
    public string GetProductName() => inner.GetProductName();
    public int Read(byte[] b, int o, int n, int t)
    {
        try { return inner.Read(b, o, n, t); }
        catch (HidException) { MarkLinkLost(); throw; }
    }
    public Task<int> ReadAsync(byte[] b, int o, int n, CancellationToken ct) => inner.ReadAsync(b, o, n, ct);
    void MarkLinkLost()
    {
        if (Interlocked.Exchange(ref linkLost, 1) == 0)
            try { Faulted?.Invoke(); } catch { }
    }
    public int Write(byte[] b, int o, int n)
    {
        try
        {
            var result = inner.Write(b, o, n);
            if (result >= n)
            {
                Interlocked.Increment(ref counts[b[o]]); Interlocked.Increment(ref writes);
                var now = Environment.TickCount64;
                if (b[o] is 0x35 or 0x36) Interlocked.Exchange(ref lastAudio, now);
                int hapticOffset = b[o] == 0x36 && n >= 142 ? 78 : b[o] == 0x32 && n >= 77 && b[o + 11] == 0x92 ? 13 : -1;
                if (hapticOffset >= 0 && b.AsSpan(o + hapticOffset, 64).ContainsAnyExcept((byte)0)) { Interlocked.Increment(ref nonzeroHaptics); Interlocked.Exchange(ref lastHaptic, now); }
                int stateOffset = b[o] == 0x31 && n >= 50 ? 3 : b[o] == 0x36 && n >= 60 ? 13 : -1;
                if (stateOffset >= 0)
                {
                    var flags = b[o + stateOffset];
                    var right = b[o + stateOffset + 10]; var left = b[o + stateOffset + 21];
                    // The pad retains an effect until a report with the allow bit replaces it.
                    if ((flags & 4) != 0) Volatile.Write(ref rightTrigger, right is 0 or 5 ? 0 : 1);
                    if ((flags & 8) != 0) Volatile.Write(ref leftTrigger, left is 0 or 5 ? 0 : 1);
                    if (((flags & 4) != 0 && right is not (0 or 5)) || ((flags & 8) != 0 && left is not (0 or 5))) Interlocked.Increment(ref triggerEffects);
                    // Classic rumble only rides standalone 0x31 reports; the audio lane folds it into haptics.
                    if (b[o] == 0x31 && (flags & 3) != 0) Volatile.Write(ref rumble, (b[o + stateOffset + 2] | b[o + stateOffset + 3]) != 0 ? 1 : 0);
                }
            }
            else Interlocked.Increment(ref errors);
            return result;
        }
        catch { Interlocked.Increment(ref errors); throw; }
    }
    public byte[] GetFeatureReport(byte id, int size = 64) => inner.GetFeatureReport(id, size);
    public void SendFeatureReport(byte[] b, int o, int n) => inner.SendFeatureReport(b, o, n);
    public void Dispose() => inner.Dispose();
}
