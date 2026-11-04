using MadSense.Games;

namespace MadSense.Installer.Tests;

[TestFixture]
public class GameFoldersTests
{
    string root = "";

    [SetUp]
    public void SetUp() { root = Path.Combine(Path.GetTempPath(), "madsense-folders-" + Guid.NewGuid().ToString("N")); Directory.CreateDirectory(root); }

    [TearDown]
    public void TearDown() { try { Directory.Delete(root, true); } catch (IOException) { } catch (UnauthorizedAccessException) { } }

    void Exe(string relative, int size = 10)
    {
        var path = Path.Combine(root, relative);
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        File.WriteAllBytes(path, new byte[size]);
    }

    [Test]
    public void AGamesFolderYieldsOneGamePerSubfolderWithARealExecutable()
    {
        Exe(@"Games\Stellar Blade\SB.exe", 50);
        Exe(@"Games\Stellar Blade\SB\Binaries\Win64\SB-Win64-Shipping.exe", 900);
        Exe(@"Games\Stellar Blade\SB\Binaries\Win64\crs-handler.exe", 5000);
        Exe(@"Games\Stellar Blade\unins000.exe", 8000);
        Exe(@"Games\Stellar Blade\_Redist\dxwebsetup.exe", 9000);
        Exe(@"Games\Stellar Blade\Engine\Binaries\Win64\UnrealCEFSubProcess.exe", 7000);
        Exe(@"Games\Tools Only\setup.exe");
        Exe(@"Games\Hollow_Knight (GOG) v1.5.78\hollow_knight.exe", 300);
        Directory.CreateDirectory(Path.Combine(root, "Games", "Empty"));
        var games = GameLibrary.Folders([Path.Combine(root, "Games"), Path.Combine(root, "Juegos")], []).ToList();
        Assert.Multiple(() =>
        {
            Assert.That(games.Select(g => g.Name), Is.EquivalentTo(new[] { "Stellar Blade", "Hollow Knight" }));
            var stellar = games.Single(g => g.Name == "Stellar Blade");
            Assert.That(stellar.Exe, Does.EndWith(@"SB\Binaries\Win64\SB-Win64-Shipping.exe"), "the largest executable that is not a tool");
            Assert.That(stellar.InstallDir, Is.EqualTo(Path.Combine(root, "Games", "Stellar Blade")));
            Assert.That(stellar.Source, Is.EqualTo(Path.Combine(root, "Games")));
            Assert.That(stellar.Key, Does.StartWith("dir:"));
            Assert.That(stellar.SteamAppId, Is.Null);
        });
    }

    [Test]
    public void KnownGamesAndLauncherFoldersAreNotCountedTwice()
    {
        Exe(@"Games\Epic Games\Alan Wake 2\AlanWake2.exe", 100);
        Exe(@"Games\SteamLibrary\steamapps\common\Portal\portal.exe", 100);
        Exe(@"Games\Chernobylite\Chernobylite.exe", 100);
        Exe(@"Games\Loose\loose.exe", 100);
        var known = new List<LibraryGame>
        {
            new("steam:1", "Chernobylite", "Steam", "1", Path.Combine(root, "Games", "Chernobylite"), null),
            new("epic:aw2", "Alan Wake 2", "Epic Games", null, Path.Combine(root, "Games", "Epic Games", "Alan Wake 2"), null),
        };
        var games = GameLibrary.Folders([Path.Combine(root, "Games")], known).ToList();
        Assert.That(games.Select(g => g.Name), Is.EquivalentTo(new[] { "Loose" }), "Steam libraries are the Steam scanner's, launcher folders and known games are skipped");
    }

    [Test]
    public void AFolderThatHoldsAKnownGameIsLookedIntoOneLevelDeeper()
    {
        Exe(@"Games\Rockstar\GTA V\GTA5.exe", 100);
        Exe(@"Games\Rockstar\Max Payne 3\MaxPayne3.exe", 100);
        var known = new List<LibraryGame> { new("dir:gta", "Grand Theft Auto V", "Rockstar Games", null, Path.Combine(root, "Games", "Rockstar", "GTA V"), null) };
        var games = GameLibrary.Folders([Path.Combine(root, "Games")], known).ToList();
        Assert.That(games.Select(g => g.Name), Is.EquivalentTo(new[] { "Max Payne 3" }));
    }

    [TestCase("Stellar Blade", "Stellar Blade")]
    [TestCase("Hollow_Knight (GOG) v1.5.78", "Hollow Knight")]
    [TestCase("Cyberpunk 2077 [FitGirl Repack]", "Cyberpunk 2077")]
    [TestCase("DOOM.Eternal.v6.66", "DOOM.Eternal")]
    [TestCase("___", "___")]
    public void FolderNamesBecomeTitles(string folder, string expected) => Assert.That(GameLibrary.CleanName(folder), Is.EqualTo(expected));

    [Test]
    public void TheRunnerWatchesOnlyWhileAHelperIsInstalled()
    {
        var mods = Path.Combine(root, "mods");
        var installer = new ModInstaller(mods);
        using var runner = new ModRunner(installer, () => [], watch: true);
        Assert.Multiple(() =>
        {
            Assert.That(runner.Watching, Is.False, "nothing installed: no timer");
            Assert.That(ModInstaller.AnyHelperInstalled(mods), Is.False);
            Assert.That(ModInstaller.AnyHelperInstalled(Path.Combine(root, "missing")), Is.False);
        });
    }
}
