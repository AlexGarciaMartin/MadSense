using MadSense;
using NUnit.Framework;

namespace MadSense.Linux.App.Tests;

[NonParallelizable]
public sealed class BridgeLifecycleTests
{
    string? oldState;
    string directory = "";
    [SetUp]
    public void Setup()
    {
        oldState = Environment.GetEnvironmentVariable("XDG_STATE_HOME");
        directory = Path.Combine(TestContext.CurrentContext.WorkDirectory, "state-" + Guid.NewGuid().ToString("N"));
        Environment.SetEnvironmentVariable("XDG_STATE_HOME", directory);
    }
    [TearDown]
    public void Restore()
    {
        Environment.SetEnvironmentVariable("XDG_STATE_HOME", oldState);
        if (Directory.Exists(directory)) Directory.Delete(directory, true);
    }
    static async Task Until(Func<bool> condition)
    {
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(5));
        while (!condition()) await Task.Delay(20, timeout.Token);
    }
    [Test]
    public async Task DeniedAuthorizationWaitsForAnotherResume()
    {
        int acquisitions = 0;
        await using var bridge = new LinuxBridgeController(false, "Auto", "Alto", _ =>
        { Interlocked.Increment(ref acquisitions); throw new UnauthorizedAccessException("Authorization denied"); });
        bridge.SetPaused(false);
        await Until(() => bridge.State.RequiresResume);
        Assert.That(bridge.State.Ready, Is.False);
        Assert.That(bridge.State.Message, Is.EqualTo("Authorization denied"));
        await Task.Delay(3500); // Exceeds the ordinary reconnect backoff.
        Assert.That(acquisitions, Is.EqualTo(1), "Do not prompt again without a new action.");
        bridge.SetPaused(false);
        await Until(() => Volatile.Read(ref acquisitions) == 2);
    }
    [Test]
    public async Task PauseCancelsPendingAcquisitionWithoutWaitingForPolkitTimeout()
    {
        var entered = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        var cancelled = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        await using var bridge = new LinuxBridgeController(false, "Auto", "Alto", async token =>
        {
            entered.SetResult();
            try { await Task.Delay(Timeout.Infinite, token); }
            finally { cancelled.SetResult(); }
        });
        bridge.SetPaused(false);
        await entered.Task.WaitAsync(TimeSpan.FromSeconds(5));
        bridge.SetPaused(true);
        await cancelled.Task.WaitAsync(TimeSpan.FromSeconds(2));
        await Until(() => bridge.State.Message == L.Text("Ui.PausedMessage"));
        Assert.That(bridge.State.Ready, Is.False);
    }
    [Test]
    public async Task PreviewNeverConnectsAfterModeSpeakerAndResumeCommands()
    {
        int acquisitions = 0;
        await using var bridge = new LinuxBridgeController(true, "Auto", "Alto", _ =>
        { acquisitions++; throw new AssertionException("Preview opened the platform backend."); });
        bridge.SetMode("Xbox"); bridge.SetSpeaker("Off"); bridge.SetPaused(false);
        await Until(() => bridge.State.Message == LinuxText.Text("Linux.Preview"));
        Assert.That(acquisitions, Is.Zero);
        Assert.That(Directory.Exists(directory), Is.False, "Preview must not write logs or preferences.");
    }
    [Test]
    public async Task DisposeCancelsAcquisitionAndCompletesCleanup()
    {
        var entered = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        var bridge = new LinuxBridgeController(false, "Auto", "Alto", async token =>
        { entered.SetResult(); await Task.Delay(Timeout.Infinite, token); });
        bridge.SetPaused(false); await entered.Task.WaitAsync(TimeSpan.FromSeconds(5));
        await bridge.DisposeAsync().AsTask().WaitAsync(TimeSpan.FromSeconds(2));
        Assert.That(bridge.State.Ready, Is.False);
    }
}
