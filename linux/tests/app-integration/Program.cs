using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text.Json;
using DualSenseClient.Hid;
using MadSense.Platform;

namespace MadSense;

// Only the executable entry point is different. SettingsFor, Connect, Update,
// Stop, queues, enumerator, factory and fixed broker endpoint are linked verbatim.
static class Program
{
    internal static string[] Arguments = [];
    static readonly JsonSerializerOptions Json = new() { PropertyNamingPolicy = JsonNamingPolicy.CamelCase };
    public static async Task<int> Main(string[] args)
    {
        Arguments = args;
        Dictionary<string, string> options = [];
        for (int i = 0; i < args.Length; i += 2)
        {
            if (i + 1 == args.Length || args[i] is not ("--owned-guest-token" or "--device" or "--fixture-label" or "--output"))
                throw new ArgumentException("Expected owned guest token, exact fixture device/label, and output directory.");
            options.Add(args[i], args[i + 1]);
        }
        string token = Guid.Parse(options["--owned-guest-token"]).ToString();
        if (!OperatingSystem.IsLinux() || Native.GetUid() == 0 ||
            File.ReadAllText("/etc/madsense-ci-owner").Trim() != token ||
            File.ReadAllText("/sys/class/dmi/id/sys_vendor").Trim() != "QEMU")
            throw new InvalidOperationException("Ordinary actor inside the explicitly owned QEMU guest required.");
        string path = options["--device"], label = options["--fixture-label"];
        if (!System.Text.RegularExpressions.Regex.IsMatch(path, "^/dev/hidraw[0-9]+$") || !label.StartsWith("madsense-app-", StringComparison.Ordinal))
            throw new ArgumentException("Expected exact synthetic fixture identity.");
        string[] uevent = File.ReadAllLines("/sys/class/hidraw/" + Path.GetFileName(path) + "/device/uevent");
        if (!uevent.Contains("HID_UNIQ=" + label)) throw new InvalidOperationException("Device is not the runner's UHID fixture.");
        using (var enumerator = new HidDeviceEnumerator())
        {
            var candidates = enumerator.Enumerate(0x054c).Where(info => info.ProductId is 0x0ce6 or 0x0df2 &&
                info.UsagePage == 1 && info.BusType == ConnectionType.Bluetooth).ToArray();
            if (candidates.Length != 1 || candidates[0].Path != path)
                throw new InvalidOperationException("The real application must discover exactly this one Bluetooth fixture.");
        }
        string output = Path.GetFullPath(options["--output"]);
        if (Directory.EnumerateFileSystemEntries(output).Any()) throw new IOException("Fresh actor output required.");
        Directory.CreateDirectory(Path.Combine(output, "commands"));
        Environment.SetEnvironmentVariable("XDG_CONFIG_HOME", Path.Combine(output, "config"));
        Environment.SetEnvironmentVariable("XDG_STATE_HOME", Path.Combine(output, "state"));
        L.Use("en");
        using var events = new StreamWriter(Path.Combine(output, "events.jsonl")) { AutoFlush = true };
        void Event(string kind, object value) => events.WriteLine(JsonSerializer.Serialize(new
            { kind, timestamp = Stopwatch.GetTimestamp(), value }, Json));
        void Atomic(string name, object value)
        {
            string target = Path.Combine(output, name);
            File.WriteAllText(target + ".tmp", JsonSerializer.Serialize(value, Json) + "\n");
            File.Move(target + ".tmp", target, true);
        }
        long queued = 0;
        var bridge = new LinuxBridgeController(false, "Auto", "Alto");
        LinuxBridgeState? previous = null;
        long nextSnapshot = 0, recordedCommand = -1;
        bool requestedExit = false;
        int exitCode = 1;
        Event("scope", new { ownedGuestToken = token, applicationControllerExercised = true, applicationUiExercised = false,
            physicalDevices = false, radioInitialized = false, pid = Environment.ProcessId, path, label,
            timestampFrequency = Stopwatch.Frequency,
            assemblyInformationalVersion = typeof(Program).Assembly.GetCustomAttributes(false)
                .OfType<System.Reflection.AssemblyInformationalVersionAttribute>().Single().InformationalVersion });
        try
        {
            using var deadline = new CancellationTokenSource(TimeSpan.FromMinutes(10));
            while (!requestedExit)
            {
                deadline.Token.ThrowIfCancellationRequested();
                string commandPath = Path.Combine(output, "commands", (queued + 1).ToString("D6") + ".json");
                if (File.Exists(commandPath))
                {
                    using var command = JsonDocument.Parse(File.ReadAllText(commandPath));
                    var root = command.RootElement;
                    string operation = root.GetProperty("operation").GetString()!;
                    string? value = root.TryGetProperty("value", out var item) ? item.GetString() : null;
                    switch (operation)
                    {
                        case "resume": bridge.SetPaused(false); break;
                        case "pause": bridge.SetPaused(true); break;
                        case "mode" when value is "Auto" or "Xbox": bridge.SetMode(value); break;
                        case "speaker" when value is "Alto" or "Bajo" or "Off": bridge.SetSpeaker(value); break;
                        case "exit": requestedExit = true; break;
                        default: throw new ArgumentException("Unknown actor operation/value.");
                    }
                    queued++;
                    Event("command-queued", new { id = queued, operation, value });
                }
                var state = bridge.State;
                bool changed = state != previous;
                if (changed) { Event("state", state); previous = state; }
                // Queue acknowledgement is explicitly not completion. The root
                // observer must wait for state plus kernel/permission evidence.
                if (changed || recordedCommand != queued || Environment.TickCount64 >= nextSnapshot)
                {
                    Atomic("status.json", new { lastQueuedCommand = queued, timestamp = Stopwatch.GetTimestamp(), state,
                        diagnostic = bridge.DiagnosticText });
                    nextSnapshot = Environment.TickCount64 + 500;
                    recordedCommand = queued;
                }
                await Task.Delay(25, deadline.Token);
            }
            exitCode = 0;
        }
        catch (Exception error) { Event("error", new { message = error.ToString() }); }
        finally
        {
            try
            {
                await bridge.DisposeAsync();
                using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(3));
                await using var probe = await LinuxBrokerClient.ConnectAsync(timeout.Token);
                var cleanup = await probe.PreflightAsync(timeout.Token);
                Atomic("stopped.json", new { requestedExit, cleanupConfirmed = cleanup.CleanupConfirmed,
                    cleanup, state = bridge.State, diagnostic = bridge.DiagnosticText });
                if (!cleanup.CleanupConfirmed) exitCode = 1;
            }
            catch (Exception error) { Event("cleanup-error", new { message = error.ToString() }); exitCode = 1; }
        }
        return exitCode;
    }
}

static partial class Native
{
    [LibraryImport("libc", EntryPoint = "getuid")] internal static partial uint GetUid();
}
