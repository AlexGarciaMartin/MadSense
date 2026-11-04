using MadSense.Games;

namespace MadSense.Installer.Tests;

[TestFixture]
public class GameFolderRootsTests
{
    string root = "";

    [SetUp]
    public void SetUp() { root = Path.Combine(Path.GetTempPath(), "madsense-roots-" + Guid.NewGuid().ToString("N")); Directory.CreateDirectory(root); }

    [TearDown]
    public void TearDown() { try { Directory.Delete(root, true); } catch (IOException) { } catch (UnauthorizedAccessException) { } }

    static LibraryGame Known(string source, string dir) => new("k:" + dir, Path.GetFileName(dir), source, null, dir, null);

    [Test]
    public void RootsCoverTheDriveRootOneLevelBelowAndTheUsersOwnFolders()
    {
        Directory.CreateDirectory(Path.Combine(root, "Alex"));
        Directory.CreateDirectory(Path.Combine(root, "Windows"));
        var roots = GameLibrary.FolderRoots([], [@"D:\Mis cosas\juegos"], [root]).ToList();
        Assert.Multiple(() =>
        {
            Assert.That(roots[0], Is.EqualTo(@"D:\Mis cosas\juegos"), "the user's folders come first");
            Assert.That(roots, Does.Contain(Path.Combine(root, "Games")));
            Assert.That(roots, Does.Contain(Path.Combine(root, "Juegos")));
            Assert.That(roots, Does.Contain(Path.Combine(root, "Alex", "Juegos")), "one level below the drive root");
            Assert.That(roots.Any(r => r.StartsWith(Path.Combine(root, "Windows"), StringComparison.OrdinalIgnoreCase)), Is.False, "system folders are never looked into");
        });
    }

    [Test]
    public void WhereALauncherPutAGameItsParentAndGrandparentAreLookedAt()
    {
        // Paths need not exist: only the shape matters here (the temp folder itself sits under AppData, which is skipped on purpose).
        const string epic = @"D:\Ocio\Epic Games\Alan Wake 2", steam = @"D:\SteamLibrary\steamapps\common\Portal", program = @"C:\Program Files\Rockstar Games\Grand Theft Auto V";
        var roots = GameLibrary.FolderRoots([Known("Epic Games", epic), Known("Steam", steam), Known("Rockstar Games", program), Known("GOG", @"C:\Users\Alex\AppData\Local\Oddity")], [], []).ToList();
        Assert.Multiple(() =>
        {
            Assert.That(roots, Does.Contain(@"D:\Ocio\Epic Games"));
            Assert.That(roots, Does.Contain(@"D:\Ocio"));
            Assert.That(roots, Does.Not.Contain(@"D:\"), "never a drive root");
            Assert.That(roots.Any(r => r.Contains("AppData", StringComparison.OrdinalIgnoreCase)), Is.False, "never AppData");
            Assert.That(roots.Any(r => r.Contains("steamapps", StringComparison.OrdinalIgnoreCase)), Is.False, "Steam libraries are the Steam scanner's");
            Assert.That(roots.Any(r => r.StartsWith(@"C:\Program Files", StringComparison.OrdinalIgnoreCase)), Is.False, "never Program Files");
        });
    }

    [Test]
    public void AGameInAFolderTheUserAddedIsFoundAndItsFolderCanBeForgotten()
    {
        var mine = Path.Combine(root, "Mis juegos");
        var exe = Path.Combine(mine, "Hades II", "Hades2.exe");
        Directory.CreateDirectory(Path.GetDirectoryName(exe)!);
        File.WriteAllBytes(exe, new byte[64]);
        var service = new GamesService(Path.Combine(root, "data"));
        try
        {
            Assert.That(service.AddFolder(mine), Is.True);
            Assert.That(service.AddFolder(mine + @"\"), Is.False, "same folder, trailing slash");
            Assert.That(service.IsCustomFolder(mine), Is.True);
            var again = new GamesService(Path.Combine(root, "data"));
            Assert.That(again.CustomFolders, Is.EqualTo(new[] { mine }), "remembered across restarts");
            again.Dispose();
            var found = GameLibrary.Folders(GameLibrary.FolderRoots([], service.CustomFolders, []), []).ToList();
            Assert.That(found.Select(g => (g.Name, g.Source)), Is.EqualTo(new[] { ("Hades II", mine) }));
            Assert.That(service.RemoveFolder(mine), Is.True);
            Assert.That(service.CustomFolders, Is.Empty);
        }
        finally { service.Dispose(); }
    }
}
