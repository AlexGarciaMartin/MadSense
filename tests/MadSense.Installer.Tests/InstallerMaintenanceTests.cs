using MadSense;

namespace MadSense.Installer.Tests;

[TestFixture]
public sealed class InstallerMaintenanceTests
{
    private const string Target = @"C:\Users\Player\AppData\Local\Programs\MadSense\MadSense.exe";
    private const string Development = @"C:\Source\MadSense\app\MadSense.exe";

    [Test]
    public void OtherInstallationIsNeverSignalled()
    {
        var processes = new FakeProcesses(new InstalledProcess(17, 1, 123, Development));
        var recovered = false;
        var result = InstallerMaintenance.Prepare(Target, processes, _ => false, _ => recovered = true);
        Assert.Multiple(() =>
        {
            Assert.That(result.ExitCode, Is.Zero);
            Assert.That(processes.Signalled, Is.Empty);
            Assert.That(processes.Processes.Select(p => p.Id), Is.EqualTo(new[] { 17 }));
            Assert.That(recovered, Is.True);
        });
    }

    [Test]
    public void OnlyTargetPathIsStoppedAndAwaited()
    {
        var processes = new FakeProcesses(new InstalledProcess(17, 1, 123, Development), new InstalledProcess(18, 1, 456, Target.ToUpperInvariant()));
        var result = InstallerMaintenance.Prepare(Target, processes, _ => false, _ => { });
        Assert.Multiple(() =>
        {
            Assert.That(result.ExitCode, Is.Zero);
            Assert.That(processes.Signalled, Is.EqualTo(new[] { 18 }));
            Assert.That(processes.Processes.Select(p => p.Id), Is.EqualTo(new[] { 17 }));
        });
    }

    [Test]
    public void HelperProcessIsExcludedEvenIfItRunsFromTarget()
    {
        var processes = new FakeProcesses(new InstalledProcess(1, 1, 123, Target));
        var result = InstallerMaintenance.Prepare(Target, processes, _ => false, _ => { });
        Assert.That(result.ExitCode, Is.Zero);
        Assert.That(processes.Signalled, Is.Empty);
    }

    [Test]
    public void OlderVersionWithoutScopedEndpointRequiresManualClose()
    {
        var processes = new FakeProcesses(new InstalledProcess(18, 1, 456, Target)) { CanSignal = false };
        var result = InstallerMaintenance.Prepare(Target, processes, _ => true, _ => Assert.Fail("Recovery must not run."));
        Assert.That(result.ExitCode, Is.EqualTo(InstallerMaintenance.Busy));
        Assert.That(processes.Processes, Has.Count.EqualTo(1));
    }

    [Test]
    public void SlowShutdownIsNotKilledOrRecoveredWhileStillRunning()
    {
        var processes = new FakeProcesses(new InstalledProcess(18, 1, 456, Target)) { Exits = false };
        var result = InstallerMaintenance.Prepare(Target, processes, _ => true, _ => Assert.Fail("Recovery must wait for exit."));
        Assert.That(result.ExitCode, Is.EqualTo(InstallerMaintenance.Busy));
        Assert.That(processes.Processes, Has.Count.EqualTo(1));
    }

    [Test]
    public void OtherSessionIsNotSignalled()
    {
        var processes = new FakeProcesses(new InstalledProcess(18, 2, 456, Target));
        var result = InstallerMaintenance.Prepare(Target, processes, _ => false, _ => Assert.Fail());
        Assert.That(result.ExitCode, Is.EqualTo(InstallerMaintenance.Busy));
        Assert.That(processes.Signalled, Is.Empty);
    }

    [Test]
    public void UnknownProcessLocationFailsClosed()
    {
        var processes = new FakeProcesses(new InstalledProcess(18, -1, 0, null));
        var result = InstallerMaintenance.Prepare(Target, processes, _ => false, _ => Assert.Fail());
        Assert.That(result.ExitCode, Is.EqualTo(InstallerMaintenance.Busy));
        Assert.That(processes.Signalled, Is.Empty);
    }

    [Test]
    public void StaleJournalCannotChangeHidHideWhileDevelopmentCopyRuns()
    {
        var processes = new FakeProcesses(new InstalledProcess(17, 1, 123, Development));
        var result = InstallerMaintenance.Prepare(Target, processes, _ => true, _ => Assert.Fail());
        Assert.That(result.ExitCode, Is.EqualTo(InstallerMaintenance.Busy));
        Assert.That(processes.Signalled, Is.Empty);
    }

    [Test]
    public void RecoveryFailureIsReturnedToInstaller()
    {
        var result = InstallerMaintenance.Prepare(Target, new FakeProcesses(), _ => true, _ => throw new IOException("device unavailable"));
        Assert.That(result.ExitCode, Is.EqualTo(InstallerMaintenance.RecoveryFailed));
        Assert.That(result.Message, Does.Contain("conservan"));
    }

    [Test]
    public void NewInstanceStartingAfterEnumerationBlocksRecovery()
    {
        var processes = new FakeProcesses { CanGuard = false };
        var result = InstallerMaintenance.Prepare(Target, processes, _ => true, _ => Assert.Fail("Must not restore during a new bridge startup."));
        Assert.That(result.ExitCode, Is.EqualTo(InstallerMaintenance.Busy));
    }

    [TestCase(@"relative\MadSense.exe")]
    [TestCase(@"C:\Windows\other.exe")]
    public void InvalidTargetIsRejectedBeforeProcessEnumeration(string target)
    {
        var processes = new FakeProcesses();
        var result = InstallerMaintenance.Prepare(target, processes, _ => false, _ => Assert.Fail());
        Assert.That(result.ExitCode, Is.EqualTo(InstallerMaintenance.InvalidArguments));
        Assert.That(processes.Enumerations, Is.Zero);
    }

    [Test]
    public void ScopedSignalIncludesProcessStartToAvoidPidReuse()
    {
        Assert.That(InstallerStopSignal.Name(123, 100), Is.Not.EqualTo(InstallerStopSignal.Name(123, 101)));
        Assert.That(InstallerStopSignal.Name(123, 100), Is.Not.EqualTo(@"Local\MadSense-Stop"));
    }

    private sealed class FakeProcesses(params InstalledProcess[] entries) : IInstallationProcesses
    {
        public int CurrentId => 1;
        public int CurrentSessionId => 1;
        public List<InstalledProcess> Processes { get; } = entries.ToList();
        public List<int> Signalled { get; } = [];
        public bool CanSignal { get; init; } = true;
        public bool Exits { get; init; } = true;
        public bool CanGuard { get; init; } = true;
        public int Enumerations { get; private set; }
        public IReadOnlyList<InstalledProcess> Find() { Enumerations++; return Processes.ToArray(); }
        public bool RequestStop(InstalledProcess process) { Signalled.Add(process.Id); return CanSignal; }
        public bool WaitForExit(InstalledProcess process, TimeSpan timeout)
        {
            if (Exits) Processes.Remove(process);
            return Exits;
        }
        public IDisposable? TryGuardRecovery() => CanGuard ? new FakeGuard() : null;
        private sealed class FakeGuard : IDisposable { public void Dispose() { } }
    }
}
