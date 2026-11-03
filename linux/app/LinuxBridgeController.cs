using System.Threading.Channels;

namespace MadSense;

sealed record LinuxBridgeState(bool Ready, string Message, bool Haptics = false, bool Triggers = false, bool Audio = false, bool Rumble = false, int Battery = -1, bool Charging = false, bool Headset = false);

// UI commands and bridge lifetime have one worker; rendering never touches HID.
sealed class LinuxBridgeController : IAsyncDisposable
{
    readonly Channel<Action> commands = Channel.CreateUnbounded<Action>(new() { SingleReader = true });
    readonly CancellationTokenSource lifetime = new();
    readonly Task worker;
    readonly bool preview;
    LinuxBridgeState state = new(false, L.Text("Ui.PausedMessage"));
    string mode, speaker;
    bool paused = true;
    public LinuxBridgeState State => Volatile.Read(ref state);
    public string DiagnosticText => $"MadSense Linux 0.5.0\n{Environment.OSVersion}\n{System.Runtime.InteropServices.RuntimeInformation.ProcessArchitecture}\n\n{State.Message}";
    public LinuxBridgeController(bool preview, string mode, string speaker)
    {
        this.preview = preview; this.mode = mode; this.speaker = speaker;
        worker = Task.Run(Run);
    }
    public void SetPaused(bool value) => commands.Writer.TryWrite(() => { paused = value; Update(); });
    public void SetMode(string value) => commands.Writer.TryWrite(() => { mode = value; Update(); });
    public void SetSpeaker(string value) => commands.Writer.TryWrite(() => speaker = value);
    void Update()
    {
        // Until the privilege broker verifies attachment and physical isolation,
        // fail before acquiring a physical handle. No optimistic "ready" state.
        state = new(false, paused ? L.Text("Ui.PausedMessage") : preview ? LinuxText.Text("Linux.Preview") :
            LinuxText.Text(OperatingSystem.IsLinux() ? "Linux.SetupNeeded" : "Linux.Unsupported"));
    }
    async Task Run()
    {
        try
        {
            await foreach (var command in commands.Reader.ReadAllAsync(lifetime.Token))
                try { command(); } catch (Exception error) { state = new(false, error.Message); }
        }
        catch (OperationCanceledException) when (lifetime.IsCancellationRequested) { }
    }
    public async ValueTask DisposeAsync()
    {
        commands.Writer.TryComplete(); await worker; lifetime.Dispose();
    }
}
