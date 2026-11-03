using MadSense;
using MadSense.Platform;
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

    [Test]
    public async Task PauseReportsReleasingUntilTheCancelledAttemptHasFinished()
    {
        var entered = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        var cancelled = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        var finish = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        await using var bridge = new LinuxBridgeController(false, "Auto", "Alto", async token =>
        {
            entered.SetResult();
            try { await Task.Delay(Timeout.Infinite, token); }
            catch (OperationCanceledException)
            { cancelled.SetResult(); await finish.Task; throw; }
        });
        bridge.SetPaused(false);
        await entered.Task.WaitAsync(TimeSpan.FromSeconds(5));
        bridge.SetPaused(true);
        try
        {
            await cancelled.Task.WaitAsync(TimeSpan.FromSeconds(2));
            Assert.That(bridge.State.Releasing, Is.True);
            Assert.That(bridge.State.Paused, Is.False, "Do not claim the controller has been released while cleanup is pending.");
            Assert.That(bridge.State.Ready, Is.False);
            Assert.That(bridge.State.Message, Is.EqualTo(L.Text("Ui.Releasing")));
        }
        finally { finish.TrySetResult(); }
        await Until(() => bridge.State.Paused && !bridge.State.Releasing);
    }

    [Test]
    public async Task ModeChangeThenPauseDuringCancellationDoesNotStartAnotherAuthorization()
    {
        var entered = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        var cancelled = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        var finish = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        int attempts = 0;
        await using var bridge = new LinuxBridgeController(false, "Auto", "Alto", async token =>
        {
            Interlocked.Increment(ref attempts); entered.TrySetResult();
            try { await Task.Delay(Timeout.Infinite, token); }
            catch (OperationCanceledException)
            { cancelled.TrySetResult(); await finish.Task; throw; }
        });
        bridge.SetPaused(false);
        await entered.Task.WaitAsync(TimeSpan.FromSeconds(5));
        bridge.SetMode("Xbox");
        try
        {
            await cancelled.Task.WaitAsync(TimeSpan.FromSeconds(2));
            bridge.SetPaused(true);
            Assert.That(bridge.State.Releasing, Is.True);
        }
        finally { finish.TrySetResult(); }
        await Until(() => bridge.State.Paused && !bridge.State.Releasing);
        await Task.Delay(600);
        Assert.That(attempts, Is.EqualTo(1));
    }

    [Test]
    public async Task RepeatedDisposalSharesCleanupAndLateCommandsAreIgnored()
    {
        var entered = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        var bridge = new LinuxBridgeController(false, "Auto", "Alto", async token =>
        { entered.TrySetResult(); await Task.Delay(Timeout.Infinite, token); });
        bridge.SetPaused(false);
        await entered.Task.WaitAsync(TimeSpan.FromSeconds(5));
        var first = bridge.DisposeAsync().AsTask();
        var second = bridge.DisposeAsync().AsTask();
        Assert.That(second, Is.SameAs(first));
        await Task.WhenAll(first, second).WaitAsync(TimeSpan.FromSeconds(2));
        await bridge.DisposeAsync();
        Assert.DoesNotThrow(() => { bridge.SetPaused(true); bridge.SetMode("Xbox"); bridge.SetSpeaker("Off"); });
    }

    [Test]
    public async Task TransportLossDoesNotClaimPausedUntilAFreshHelperConfirmsCleanup()
    {
        var entered = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        int attempts = 0, probes = 0, recovered = 0;
        LinuxBridgeController? bridge = null;
        bridge = new LinuxBridgeController(false, "Auto", "Alto", async token =>
        {
            Interlocked.Increment(ref attempts); bridge!.MarkAcquisitionRequested(); entered.TrySetResult();
            await Task.Delay(Timeout.Infinite, token);
        }, _ =>
        {
            Interlocked.Increment(ref probes);
            return ValueTask.FromResult(Volatile.Read(ref recovered) == 0
                ? new LinuxBrokerPreflight(false, "Helper is offline during recovery")
                : new LinuxBrokerPreflight(true, "Idle", LeaseActive: false, RecoveryPending: false));
        });
        await using (bridge)
        {
            bridge.SetPaused(false); await entered.Task.WaitAsync(TimeSpan.FromSeconds(3));
            bridge.SetPaused(true);
            await Until(() => Volatile.Read(ref probes) > 0);
            Assert.That(bridge.State.Paused, Is.False);
            Assert.That(bridge.State.Releasing, Is.True);
            Assert.That(bridge.State.Message, Does.Contain("offline during recovery"));
            Assert.That(LinuxLog.Recent(), Does.Contain("offline during recovery"));
            await Task.Delay(1100);
            Assert.That(attempts, Is.EqualTo(1));
            Volatile.Write(ref recovered, 1);
            await Until(() => bridge.State.Paused);
            Assert.That(bridge.State.Releasing, Is.False);
            Assert.That(attempts, Is.EqualTo(1), "Recovery polling must never request authorization.");
        }
    }

    [Test]
    public async Task ResumeWaitsForCleanupConfirmationAndAnOlderHelperCannotSupplyIt()
    {
        var entered = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        int attempts = 0, probes = 0, recovered = 0;
        LinuxBridgeController? bridge = null;
        bridge = new LinuxBridgeController(false, "Auto", "Alto", async token =>
        {
            Interlocked.Increment(ref attempts); bridge!.MarkAcquisitionRequested(); entered.TrySetResult();
            await Task.Delay(Timeout.Infinite, token);
        }, _ =>
        {
            Interlocked.Increment(ref probes);
            return ValueTask.FromResult(Volatile.Read(ref recovered) == 0
                ? new LinuxBrokerPreflight(true, "Legacy helper reports availability only")
                : new LinuxBrokerPreflight(true, "Idle", LeaseActive: false, RecoveryPending: false));
        });
        await using (bridge)
        {
            bridge.SetPaused(false); await entered.Task.WaitAsync(TimeSpan.FromSeconds(3));
            bridge.SetPaused(true); await Until(() => Volatile.Read(ref probes) > 0);
            bridge.SetPaused(false);
            await Task.Delay(1100);
            Assert.That(attempts, Is.EqualTo(1));
            Assert.That(bridge.State.Releasing, Is.True);
            Assert.That(bridge.State.Ready, Is.False);
            Volatile.Write(ref recovered, 1);
            await Until(() => Volatile.Read(ref attempts) == 2);
        }
    }
}
