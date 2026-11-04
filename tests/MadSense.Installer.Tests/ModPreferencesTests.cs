using System.Text.Json;
using MadSense.Games;

namespace MadSense.Installer.Tests;

[TestFixture]
public class ModPreferencesTests
{
    string root = "";
    [SetUp] public void SetUp() => root = Path.Combine(Path.GetTempPath(), "madsense-mod-preferences-" + Guid.NewGuid().ToString("N"));
    [TearDown] public void TearDown() { if (Directory.Exists(root)) Directory.Delete(root, true); }

    [Test]
    public void DefaultsAreLazyAndEachGameAndModKeepIndependentPreferences()
    {
        using (var service = new GamesService(root))
        {
            Assert.That(service.IsAutoStartEnabled("racing-dsx", "dirt"), Is.True);
            Assert.That(service.IsSetupComplete("racing-dsx", "dirt"), Is.False);
            Assert.That(Directory.Exists(root), Is.False);
            service.SetAutoStartEnabled("racing-dsx", "dirt", false);
            service.SetSetupComplete("racing-dsx", "dirt", true);
            service.SetSetupComplete("dr2ds", "dirt", true);
        }
        using var reopened = new GamesService(root);
        Assert.Multiple(() =>
        {
            Assert.That(reopened.IsAutoStartEnabled("racing-dsx", "dirt"), Is.False);
            Assert.That(reopened.IsSetupComplete("racing-dsx", "dirt"), Is.True);
            Assert.That(reopened.IsAutoStartEnabled("dr2ds", "dirt"), Is.True);
            Assert.That(reopened.IsSetupComplete("dr2ds", "dirt"), Is.True);
            Assert.That(reopened.IsSetupComplete("racing-dsx", "forza"), Is.False);
        });
    }

    [Test]
    public void FailedSaveDoesNotPretendSettingWasPersisted()
    {
        Directory.CreateDirectory(root);
        var path = Path.Combine(root, "prefs.json");
        var preferences = new ModPreferences(path);
        preferences.SetSetupComplete("mod", "game", true);
        Directory.CreateDirectory(path + ".tmp");
        Assert.Throws<UnauthorizedAccessException>(() => preferences.SetAutoStartEnabled("mod", "game", false));
        Assert.That(preferences.IsAutoStartEnabled("mod", "game"), Is.True);
        Assert.That(new ModPreferences(path).IsSetupComplete("mod", "game"), Is.True);
    }

    [Test]
    public void StoppingAModLastsUntilItsGameSessionEndsAndExplicitStartOverridesIt()
    {
        var policy = new ModSessionPolicy();
        policy.Stop("racing-dsx", "dirt");
        policy.ObserveGames(new HashSet<string> { "dirt", "forza" });
        Assert.That(policy.IsStopped("racing-dsx", "dirt"), Is.True, "a new child process must not restart the stopped mod");
        Assert.That(policy.IsStopped("dr2ds", "dirt"), Is.False, "stopping one mod does not silently disable another");
        policy.ObserveGames(new HashSet<string> { "forza" });
        Assert.That(policy.IsStopped("racing-dsx", "dirt"), Is.False, "the next game session can start automatically");
        policy.Stop("racing-dsx", "dirt");
        policy.Resume("racing-dsx", "dirt");
        Assert.That(policy.IsStopped("racing-dsx", "dirt"), Is.False);
    }

    [Test]
    public void DisabledHelpersDoNotKeepTheProcessWatcherAwakeAndMissingExecutableHasActionableFailure()
    {
        Directory.CreateDirectory(root);
        File.WriteAllText(Path.Combine(root, "installed.json"), JsonSerializer.Serialize(new[]
        {
            new InstalledMod { Id = "wf2-dsx", GameKey = "wf2", Root = root, Exe = "missing-helper.exe", GameDir = root },
        }));
        bool enabled = false;
        using var runner = new ModRunner(new ModInstaller(root), () => [], autoStartEnabled: (_, _) => enabled);
        Assert.That(runner.Watching, Is.False);
        enabled = true;
        runner.RefreshPreferences();
        Assert.That(runner.Watching, Is.True);
        enabled = false;
        runner.RefreshPreferences();
        Assert.That(runner.Watching, Is.False);
        int failed = 0;
        runner.HelperFailed += (_, _) => failed++;
        Assert.That(runner.Start("wf2-dsx", "wf2"), Is.False);
        Assert.That(failed, Is.EqualTo(1));
        Assert.That(runner.Failure("wf2-dsx", "wf2"), Does.Contain("missing-helper.exe"));
    }
}
