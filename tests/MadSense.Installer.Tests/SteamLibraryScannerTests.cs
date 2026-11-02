using System.Text;
using MadSense.Games;

namespace MadSense.Installer.Tests;

[TestFixture]
public sealed class SteamLibraryScannerTests
{
    string directory = "";
    string root = "";
    readonly SteamLibraryScanner scanner = new();

    [SetUp]
    public void CreateFixture()
    {
        directory = Path.Combine(Path.GetTempPath(), "MadSense-SteamDiscovery-tests", Guid.NewGuid().ToString("N"));
        root = Path.Combine(directory, "Steam");
        Directory.CreateDirectory(Path.Combine(root, "steamapps"));
    }

    [TearDown]
    public void RemoveFixture() => Directory.Delete(directory, recursive: true);

    [Test]
    public void FindsRootAndOtherLibrariesAndDeduplicatesAppIds()
    {
        var second = Path.Combine(directory, "Other Library");
        CreateGame(root, "42", "Root game", "RootGame");
        CreateGame(second, "84", "Other game", "OtherGame");
        CreateGame(second, "42", "Duplicate game", "Duplicate");
        File.WriteAllText(Path.Combine(root, "steamapps", "libraryfolders.vdf"),
            $"\"libraryfolders\" {{ \"0\" {{ \"path\" {Quote(root)} }} \"1\" {{ \"path\" {Quote(second)} \"apps\" {{ \"84\" \"12345\" }} }} }}");

        var result = scanner.Scan([root, root]);

        Assert.Multiple(() =>
        {
            Assert.That(result.Games.Select(game => game.AppId), Is.EquivalentTo(new[] { "42", "84" }));
            Assert.That(result.Games.Single(game => game.AppId == "42").Name, Is.EqualTo("Root game"));
            Assert.That(result.Games.Single(game => game.AppId == "84").InstallDirectory, Is.EqualTo(Path.Combine(second, "steamapps", "common", "OtherGame")));
            Assert.That(result.Warnings, Is.Empty);
        });
    }

    [Test]
    public void ReadsLegacyLibraryListCommentsBareKeysAndEscapedNames()
    {
        var second = Path.Combine(directory, "Second");
        CreateGame(second, "100", "A \"quoted\" title", "Folder");
        File.WriteAllText(Path.Combine(root, "steamapps", "libraryfolders.vdf"),
            $"\uFEFF// library list\r\nlibraryfolders {{ // old format\n 1 {Quote(second)} // trailing comment\n }}");

        var result = scanner.Scan([root]);

        Assert.That(result.Games.Single().Name, Is.EqualTo("A \"quoted\" title"));
        Assert.That(result.Warnings, Is.Empty);
    }

    [Test]
    public void ADownloadFolderOrMissingDirectoryIsNotAnInstalledGame()
    {
        CreateGame(root, "10", "Downloading", "Downloading", flags: "1026");
        CreateGame(root, "20", "Missing", "Missing", createDirectory: false);
        CreateGame(root, "30", "Installed needs update", "Installed", flags: "6");
        CreateGame(root, "40", "No state", "NoState", flags: "invalid");

        var result = scanner.Scan([root]);

        Assert.That(result.Games.Select(game => game.AppId), Is.EqualTo(new[] { "30" }));
    }

    [TestCase("../Outside")]
    [TestCase("..\\Outside")]
    [TestCase(".")]
    [TestCase("C:\\Windows")]
    [TestCase("\\\\server\\share")]
    public void RejectsInstallationDirectoriesOutsideCommon(string installDirectory)
    {
        CreateGame(root, "10", "Bad path", installDirectory, createDirectory: false);

        var result = scanner.Scan([root]);

        Assert.That(result.Games, Is.Empty);
        Assert.That(result.Warnings, Has.Count.EqualTo(1));
    }

    [TestCase("0")]
    [TestCase("-10")]
    [TestCase("0010")]
    [TestCase("10/../20")]
    [TestCase("99")]
    public void RejectsInvalidOrMismatchedAppIds(string appId)
    {
        var file = CreateGame(root, "10", "Game", "Game");
        File.WriteAllText(file, File.ReadAllText(file).Replace("\"appid\" \"10\"", $"\"appid\" {Quote(appId)}"));

        var result = scanner.Scan([root]);

        Assert.That(result.Games, Is.Empty);
        Assert.That(result.Warnings, Has.Count.EqualTo(1));
    }

    [TestCase("\"AppState\" { \"appid\" \"20\"")]
    [TestCase("\"AppState\" { \"appid\" \"20\" \"appid\" \"21\" }")]
    [TestCase("}")]
    [TestCase("\"AppState\" { \"appid\" \"20 }")]
    public void OneMalformedManifestDoesNotHideOtherGames(string corrupt)
    {
        CreateGame(root, "10", "Valid", "Valid");
        File.WriteAllText(Path.Combine(root, "steamapps", "appmanifest_20.acf"), corrupt);

        var result = scanner.Scan([root]);

        Assert.That(result.Games.Single().AppId, Is.EqualTo("10"));
        Assert.That(result.Warnings, Has.Count.EqualTo(1));
    }

    [Test]
    public void MalformedLibraryListStillAllowsTheRootLibrary()
    {
        CreateGame(root, "10", "Valid", "Valid");
        File.WriteAllText(Path.Combine(root, "steamapps", "libraryfolders.vdf"), "libraryfolders {");

        var result = scanner.Scan([root]);

        Assert.That(result.Games.Single().AppId, Is.EqualTo("10"));
        Assert.That(result.Warnings, Has.Count.EqualTo(1));
    }

    [Test]
    public void UnavailableLibraryDoesNotPreventDiscovery()
    {
        CreateGame(root, "10", "Valid", "Valid");
        var missing = Path.Combine(directory, "Unavailable");
        File.WriteAllText(Path.Combine(root, "steamapps", "libraryfolders.vdf"), $"libraryfolders {{ 1 {{ path {Quote(missing)} }} }}");

        var result = scanner.Scan([root]);

        Assert.That(result.Games.Single().AppId, Is.EqualTo("10"));
        Assert.That(result.Warnings, Has.Count.EqualTo(1));
    }

    [Test]
    public void RejectsOversizeAndDeeplyNestedMetadataWithoutLosingOtherGames()
    {
        CreateGame(root, "10", "Valid", "Valid");
        File.WriteAllText(Path.Combine(root, "steamapps", "appmanifest_20.acf"), new string(' ', 1024 * 1024 + 1));
        File.WriteAllText(Path.Combine(root, "steamapps", "appmanifest_30.acf"), string.Concat(Enumerable.Repeat("key { ", 40)) + new string('}', 40));

        var result = scanner.Scan([root]);

        Assert.That(result.Games.Single().AppId, Is.EqualTo("10"));
        Assert.That(result.Warnings, Has.Count.EqualTo(2));
    }

    [Test]
    public void DiscoveryLeavesManifestsUnchanged()
    {
        var manifest = CreateGame(root, "10", "Valid", "Valid");
        var before = File.ReadAllBytes(manifest);
        var modified = File.GetLastWriteTimeUtc(manifest);

        scanner.Scan([root]);

        Assert.That(File.ReadAllBytes(manifest), Is.EqualTo(before));
        Assert.That(File.GetLastWriteTimeUtc(manifest), Is.EqualTo(modified));
    }

    [Test]
    public void CancelledScanDoesNotReturnPartialResults()
    {
        using var cancellation = new CancellationTokenSource();
        cancellation.Cancel();
        Assert.That(() => scanner.Scan([root], cancellation.Token), Throws.InstanceOf<OperationCanceledException>());
    }

    [Test]
    public void InvalidRootsAreIgnored()
    {
        var result = scanner.Scan(["", "relative", "C:relative"]);
        Assert.That(result.Games, Is.Empty);
        Assert.That(result.Warnings, Is.Empty);
    }

    static string Quote(string value) => '"' + value.Replace("\\", "\\\\").Replace("\"", "\\\"") + '"';

    static string CreateGame(string library, string id, string name, string directory, string flags = "4", bool createDirectory = true)
    {
        var steamApps = Path.Combine(library, "steamapps");
        Directory.CreateDirectory(steamApps);
        if (createDirectory) Directory.CreateDirectory(Path.Combine(steamApps, "common", directory));
        var file = Path.Combine(steamApps, $"appmanifest_{id}.acf");
        File.WriteAllText(file, $"\"AppState\" {{ \"appid\" {Quote(id)} \"name\" {Quote(name)} \"StateFlags\" {Quote(flags)} \"installdir\" {Quote(directory)} }}", new UTF8Encoding(false));
        return file;
    }
}
