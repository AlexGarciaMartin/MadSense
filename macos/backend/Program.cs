using System.Runtime.InteropServices;
using System.Text.Json;
using System.Threading.Channels;
using DualSenseClient.Emulation.Devices.DualSense;
using DualSenseClient.Hid;
using DualSenseClient.Logging;

namespace MadSense.Mac;

internal static class Program
{
    internal static object Capabilities(bool audioAvailable = false) => new
    {
        hidInput = true, hidFeedback = true, exclusiveAccess = true,
        nativeAudio = audioAvailable, nativeHaptics = audioAvailable, xinput = false,
        compositeUsb = false, hardwareValidated = false
    };

    public static async Task<int> Main(string[] args)
    {
        DualSenseClientLogger.Configure(LogLevel.Warning, new StderrLog());
        if (args.Contains("--protocol-check")) return ProtocolCheck();
        if (!OperatingSystem.IsMacOS())
        {
            WriteStatus("unsupported", "The macOS backend requires macOS.");
            return 2;
        }
        SpeakerPreferences? preferences = null;
        try
        {
            if (args.Contains("--probe-native"))
            {
                using var probe = new MacVirtualHid(new DualSenseDevice(new DualSenseIdentity()));
                var bytes = new byte[64];
                new DualSenseDevice(new DualSenseIdentity()).ProvideInputReport(bytes);
                probe.Send(bytes);
                WriteStatus("native-probe-passed", "Virtual HID creation and input submission succeeded. No physical controller was tested.");
                return 0;
            }
            using var enumerator = new HidDeviceEnumerator();
            if (args.Contains("--diagnose")) { Diagnose(enumerator); return 0; }
            if (!args.Contains("--service") && !args.Contains("--run"))
            {
                Console.Error.WriteLine("Usage: MadSense.Backend --service | --run | --diagnose | --probe-native | --protocol-check");
                return 2;
            }
            string stateDirectory = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "Library", "Application Support", "MadSense");
            Directory.CreateDirectory(stateDirectory);
            using var instance = new FileStream(Path.Combine(stateDirectory, "backend.lock"), FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None);
            preferences = new SpeakerPreferences(Path.Combine(stateDirectory, "preferences.json"), Console.Error.WriteLine);
            return await Run(enumerator, args.Contains("--service"), preferences);
        }
        catch (Exception e)
        {
            WriteStatus("error", e.Message, speaker: preferences?.Speaker ?? "high");
            Console.Error.WriteLine(e);
            return 1;
        }
    }

    private static async Task<int> Run(HidDeviceEnumerator enumerator, bool service, SpeakerPreferences preferences)
    {
        using var stop = new CancellationTokenSource();
        Console.CancelKeyPress += (_, e) => { e.Cancel = true; stop.Cancel(); };
        using var terminate = PosixSignalRegistration.Create(PosixSignal.SIGTERM, c => { c.Cancel = true; stop.Cancel(); });
        var commands = Channel.CreateBounded<string>(new BoundedChannelOptions(32) { FullMode = BoundedChannelFullMode.DropOldest });
        if (service)
        {
            _ = Task.Run(async () =>
            {
                try
                {
                    while (!stop.IsCancellationRequested && await Console.In.ReadLineAsync(stop.Token) is { } line)
                        commands.Writer.TryWrite(line.Trim());
                }
                catch (OperationCanceledException) { }
                finally { stop.Cancel(); } // Parent closed its pipe: never keep a controller seized.
            });
        }
        BridgeSession? session = null;
        bool paused = false;
        long nextAttempt = 0;
        string state = "waiting", detail = "Pair a DualSense in Bluetooth settings, then press PS.";
        (string State, string Detail, string? Audio)? lastLogged = null;
        try
        {
            while (!stop.IsCancellationRequested)
            {
                while (commands.Reader.TryRead(out string? command))
                {
                    switch (command)
                    {
                        case "quit": stop.Cancel(); break;
                        case "pause": paused = true; session?.Dispose(); session = null; state = "paused"; detail = "Controller released."; break;
                        case "resume": paused = false; nextAttempt = 0; state = "waiting"; detail = "Waiting for a paired Bluetooth DualSense."; break;
                        case "diagnose": Diagnose(enumerator, session, preferences.Speaker, state, detail); break;
                        default:
                            if (!preferences.TryApplyCommand(command, value => session?.SetSpeaker(value)))
                                Console.Error.WriteLine("Ignored unknown command or invalid speaker level.");
                            break;
                    }
                }
                if (stop.IsCancellationRequested) break;
                try
                {
                    if (session != null && !session.Connected)
                    {
                        detail = session.Fault ?? "Controller disconnected. Waiting to reconnect.";
                        session.Dispose(); session = null; state = "waiting"; nextAttempt = Environment.TickCount64 + 1000;
                    }
                    if (!paused && session == null && Environment.TickCount64 >= nextAttempt)
                    {
                        nextAttempt = Environment.TickCount64 + 3000;
                        var pads = enumerator.Enumerate(0x054c)
                            .Where(d => d.ProductId is 0x0ce6 or 0x0df2 && d.UsagePage == 1).ToArray();
                        var info = pads.FirstOrDefault(d => d.BusType == ConnectionType.Bluetooth);
                        if (info != null)
                        {
                            session = new BridgeSession(enumerator, info, preferences.Speaker);
                            state = "connecting"; detail = "Waiting for the first controller input report.";
                        }
                        else
                        {
                            state = "waiting";
                            detail = pads.Any(d => d.BusType == ConnectionType.Usb)
                                ? "USB controller detected. Use it natively, or unplug it to bridge Bluetooth."
                                : "Pair a DualSense in Bluetooth settings, then press PS.";
                        }
                    }
                    if (session?.Reports > 0)
                    {
                        state = "ready";
                        detail = session.AudioAvailable
                            ? "HID bridge active. Dedicated four-channel CoreAudio capture available; game association requires validation."
                            : "HID bridge active. The optional CoreAudio endpoint is unavailable. XInput is not supported on macOS.";
                    }
                }
                catch (Exception e)
                {
                    try { session?.Dispose(); } finally { session = null; }
                    state = "error"; detail = e.Message; nextAttempt = Environment.TickCount64 + 10000;
                }
                var health = (state, detail, session?.AudioDetail);
                if (lastLogged != health)
                {
                    Console.Error.WriteLine($"[{state}] {detail}");
                    if (health.Item3 is { } audioDetail) Console.Error.WriteLine($"[audio] {audioDetail}");
                    lastLogged = health;
                }
                WriteStatus(state, detail, session?.Battery ?? -1, session?.Reports ?? 0,
                    session?.AudioAvailable ?? false, session?.AudioStatus, preferences.Speaker);
                // Wake for commands immediately; periodic health/discovery remains bounded.
                using var wake = CancellationTokenSource.CreateLinkedTokenSource(stop.Token);
                wake.CancelAfter(1000);
                try { await commands.Reader.WaitToReadAsync(wake.Token); }
                catch (OperationCanceledException) when (!stop.IsCancellationRequested) { }
            }
        }
        catch (OperationCanceledException) when (stop.IsCancellationRequested) { }
        finally { session?.Dispose(); }
        WriteStatus("stopped", "Controller released.", speaker: preferences.Speaker);
        return 0;
    }

    private static void Diagnose(HidDeviceEnumerator enumerator, BridgeSession? session = null,
        string speaker = "high", string? bridgeState = null, string? detail = null) => Console.WriteLine(JsonSerializer.Serialize(new
    {
        state = "diagnostic", version = "0.5.0-macos-dev", os = RuntimeInformation.OSDescription,
        architecture = RuntimeInformation.ProcessArchitecture.ToString(), capabilities = Capabilities(session?.AudioAvailable ?? false),
        speaker, bridgeState, detail, fault = session?.Fault, audio = session?.AudioStatus,
        devices = enumerator.Enumerate(0x054c).Select(d => new { d.ProductName, d.ProductId, bus = d.BusType.ToString(), d.UsagePage })
    }));

    private static int ProtocolCheck()
    {
        var router = new HidReportRouter(new DualSenseDevice(new DualSenseIdentity()));
        byte[] bytes = new byte[64];
        if (router.Get(0, 1, bytes, out int count) != 0 || count != 64 || bytes[0] != 1) return 1;
        if (router.Get(2, 5, bytes, out count) != 0 || count != 41 || bytes[0] != 5) return 1;
        WriteStatus("protocol-check-passed", "Managed input and feature-report smoke checks passed. No hardware tested.");
        return 0;
    }

    private static void WriteStatus(string state, string detail, int battery = -1, long reports = 0,
        bool audioAvailable = false, object? audio = null, string speaker = "high") =>
        Console.WriteLine(JsonSerializer.Serialize(new { state, detail, battery, reports, speaker,
            version = "0.5.0-macos-dev", capabilities = Capabilities(audioAvailable), audio }));

    private sealed class StderrLog : ILogSink
    {
        public void Write(in LogEntry entry) => Console.Error.WriteLine($"[{entry.Level}] {entry.Category}: {entry.Message}");
    }
}
