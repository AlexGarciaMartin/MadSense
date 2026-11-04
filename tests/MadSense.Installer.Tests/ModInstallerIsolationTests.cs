using System.Net;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using MadSense.Games;

namespace MadSense.Installer.Tests;

[TestFixture]
public class ModInstallerIsolationTests
{
    string root = "";
    readonly byte[] package = Encoding.UTF8.GetBytes("MZ-fake-test-helper-never-executed");
    [SetUp] public void SetUp() { root = Path.Combine(Path.GetTempPath(), "madsense-isolation-" + Guid.NewGuid().ToString("N")); Directory.CreateDirectory(root); }
    [TearDown] public void TearDown() { if (Directory.Exists(root)) Directory.Delete(root, true); }
    LibraryGame Game(string key) => new(key, key, "Test", null, Path.Combine(root, key), null);
    ModEntry Mod() => new()
    {
        Id = "shared-helper", Name = "Test helper", KindName = "helper", Run = new ModRun { Exe = "helper.exe" },
        Package = new ModPackage { Url = "https://github.com/test/helper/releases/download/v1/helper.exe", Type = "exe", Version = "1", Size = package.Length },
    };
    ModInstaller Installer() => new(root, new HttpClient(new StubHandler(package)));

    [Test]
    public async Task SameHelperForTwoGamesHasIndependentFilesAndPreservesUserFilesOnRemoval()
    {
        var installer = Installer(); var mod = Mod();
        var first = await installer.InstallAsync(mod, Game("one"), null, CancellationToken.None);
        File.WriteAllText(Path.Combine(first.Root, "my-settings.ini"), "user preferences");
        var second = await installer.InstallAsync(mod, Game("two"), null, CancellationToken.None);
        Assert.That(first.Root, Is.Not.EqualTo(second.Root));
        Assert.That(File.Exists(Path.Combine(first.Root, "helper.exe")), Is.True);
        Assert.That(File.ReadAllText(Path.Combine(first.Root, "my-settings.ini")), Is.EqualTo("user preferences"));
        installer.Uninstall(mod.Id, "one");
        Assert.That(File.Exists(Path.Combine(second.Root, "helper.exe")), Is.True);
        Assert.That(File.Exists(Path.Combine(first.Root, "helper.exe")), Is.False);
        Assert.That(File.Exists(Path.Combine(first.Root, "my-settings.ini")), Is.True);
        Assert.That(new ModInstaller(root).IsInstalled(mod.Id, "two"), Is.True);
        var reinstalled = await installer.InstallAsync(mod, Game("one"), null, CancellationToken.None);
        Assert.That(reinstalled.Root, Is.EqualTo(first.Root), "stable per-game folder across reinstalls");
        Assert.That(File.Exists(Path.Combine(first.Root, "my-settings.ini")), Is.True);
    }

    [Test]
    public void LegacySharedRootRemainsUsableForTheOtherGame()
    {
        var shared = Path.Combine(root, "legacy"); Directory.CreateDirectory(shared);
        File.WriteAllText(Path.Combine(shared, "helper.exe"), "legacy helper");
        File.WriteAllText(Path.Combine(shared, "user.ini"), "keep");
        File.WriteAllText(Path.Combine(root, "installed.json"), JsonSerializer.Serialize(new[]
        {
            new InstalledMod { Id = "legacy", GameKey = "one", Root = shared, Exe = "helper.exe", Files = ["helper.exe"] },
            new InstalledMod { Id = "legacy", GameKey = "two", Root = shared, Exe = "helper.exe", Files = ["helper.exe"] },
        }));
        var installer = new ModInstaller(root);
        installer.Uninstall("legacy", "one");
        Assert.That(File.Exists(Path.Combine(shared, "helper.exe")), Is.True);
        Assert.That(installer.IsInstalled("legacy", "two"), Is.True);
        installer.Uninstall("legacy", "two");
        Assert.That(File.Exists(Path.Combine(shared, "helper.exe")), Is.False);
        Assert.That(File.ReadAllText(Path.Combine(shared, "user.ini")), Is.EqualTo("keep"));
    }

    [Test]
    public async Task LockedFileFailureKeepsRegistrationAndCanBeRetried()
    {
        var installer = Installer();
        var record = await installer.InstallAsync(Mod(), Game("locked"), null, CancellationToken.None);
        int notifications = 0; installer.Changed += () => notifications++;
        using (var locked = File.Open(Path.Combine(record.Root, "helper.exe"), FileMode.Open, FileAccess.Read, FileShare.None))
        {
            Assert.Throws<IOException>(() => installer.Uninstall(record.Id, record.GameKey));
            Assert.That(installer.IsInstalled(record.Id, record.GameKey), Is.True);
            Assert.That(new ModInstaller(root).IsInstalled(record.Id, record.GameKey), Is.True);
            Assert.That(notifications, Is.Zero);
        }
        installer.Uninstall(record.Id, record.GameKey);
        Assert.That(installer.IsInstalled(record.Id, record.GameKey), Is.False);
        Assert.That(notifications, Is.EqualTo(1));
    }

    [Test]
    public void CancellationAtInstallTransitionWritesNoHelperOrRegistry()
    {
        using var cancellation = new CancellationTokenSource();
        var installer = Installer();
        var progress = new InlineProgress(p => { if (p.Stage == "install") cancellation.Cancel(); });
        Assert.ThrowsAsync<OperationCanceledException>(() => installer.InstallAsync(Mod(), Game("cancelled"), progress, cancellation.Token));
        Assert.That(installer.Installed, Is.Empty);
        Assert.That(Directory.Exists(Path.Combine(root, "shared-helper")), Is.False);
        Assert.That(File.Exists(Path.Combine(root, "installed.json")), Is.False);
    }

    sealed class InlineProgress(Action<InstallProgress> callback) : IProgress<InstallProgress>
    {
        public void Report(InstallProgress value) => callback(value);
    }
    sealed class StubHandler(byte[] bytes) : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
            => Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK) { Content = new ByteArrayContent(bytes) });
    }
}
