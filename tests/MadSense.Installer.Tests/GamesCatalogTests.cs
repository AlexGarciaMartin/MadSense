using System.IO.Compression;
using System.Net;
using System.Net.Http;
using System.Text;
using MadSense.Games;

namespace MadSense.Installer.Tests;

[TestFixture]
public class GamesCatalogTests
{
    static LibraryGame Game(string name, string? steam = null, string? dir = null) => new("test:" + name, name, "Test", steam, dir ?? Path.GetTempPath(), null);

    [TestCase("3240220")]
    [TestCase(null)]
    public void LegacyGtaModIsNotOfferedForEnhanced(string? steam)
    {
        Assert.That(ModCatalog.ForGame(Game("Grand Theft Auto V Enhanced", steam)).Select(m => m.Id), Does.Not.Contain("gta5-adaptive-triggers"));
        Assert.That(ModCatalog.ForGame(Game("Grand Theft Auto V Legacy", "271590")).Select(m => m.Id), Does.Contain("gta5-adaptive-triggers"));
    }

    [Test]
    public void PreparationIsSpecificToTheSelectedGame()
    {
        var mod = ModCatalog.ById("racing-dsx")!;
        Assert.That(mod.Preparation(Game("DiRT Rally 2.0", "690790"), "es"), Does.Contain("5300").And.Not.Contain("Forza"));
        Assert.That(mod.Preparation(Game("Forza Horizon 5", "1551360"), "en"), Does.Contain("Data Out").And.Not.Contain("hardware_settings"));
        Assert.That(ModCatalog.ById("dr2ds")!.Available, Is.False);
    }

    [Test]
    public void EmbeddedCatalogParsesAndEveryInstallableEntryPinsAGitHubRelease()
    {
        Assert.That(ModCatalog.Mods, Is.Not.Empty);
        Assert.That(ModCatalog.Version, Does.Match(@"^\d{4}-\d{2}-\d{2}$"));
        foreach (var mod in ModCatalog.Mods)
        {
            Assert.That(mod.Id, Does.Match("^[a-z0-9-]+$"), mod.Id);
            Assert.That(mod.PageUri.Scheme, Is.EqualTo("https"));
            Assert.That(mod.Notes.ContainsKey("es") && mod.Notes.ContainsKey("en"), Is.True, mod.Id + " needs es and en notes");
            if (mod.Kind != ModKind.Manual)
            {
                Assert.That(mod.Installable, Is.True, mod.Id);
                Assert.That(mod.Package!.Url, Does.StartWith("https://github.com/").And.Contain("/releases/download/"), mod.Id);
                Assert.That(mod.Package.Size, Is.GreaterThan(0), mod.Id);
                if (mod.Package.Sha256 is { } digest) Assert.That(digest, Does.Match("^[0-9a-f]{64}$"), mod.Id);
            }
            if (mod.Kind == ModKind.Helper) Assert.That(mod.Run?.Exe, Does.EndWith(".exe"), mod.Id);
        }
        Assert.That(ModCatalog.Mods.Select(m => m.Id), Is.Unique);
    }

    [Test]
    public void MatchesBySteamIdFirstAndByNameOnlyWithoutOne()
    {
        var wreckfest = ModCatalog.ForGame(Game("Wreckfest 2", "1203190"));
        Assert.That(wreckfest.Select(m => m.Id), Does.Contain("wf2-dsx"));
        Assert.That(ModCatalog.ForGame(Game("Wreckfest 2", "999999")), Is.Empty, "a Steam game is matched by its AppID, never by name");
        Assert.That(ModCatalog.ForGame(Game("DiRT Rally 2.0 (Epic)")).Select(m => m.Id), Is.EquivalentTo(new[] { "racing-dsx" }));
        Assert.That(ModCatalog.ForGame(Game("Chernobylite Complete Edition", "1016800")), Is.Empty);
    }

    [Test]
    public void RejectsCatalogsWithInstallableEntriesOutsideGitHub()
    {
        const string json = """{"schemaVersion":1,"catalogVersion":"2026-01-01","mods":[{"id":"x","name":"X","author":"a","page":"https://example.com","games":[{"name":"G"}],"kind":"helper","package":{"url":"https://example.com/x.zip","size":1},"run":{"exe":"x.exe"}}]}""";
        Assert.Throws<FormatException>(() => ModCatalog.Parse(new MemoryStream(Encoding.UTF8.GetBytes(json))));
    }

    [Test]
    public void ExtractRefusesPathsOutsideTheDestinationAndOverwritesOnlyWhenAllowed()
    {
        var root = Path.Combine(Path.GetTempPath(), "madsense-extract-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        var package = Path.Combine(root, "pkg.zip");
        try
        {
            using (var zip = ZipFile.Open(package, ZipArchiveMode.Create))
            {
                AddEntry(zip, "tool/helper.exe", "MZ");
                AddEntry(zip, "readme.txt", "hi");
            }
            var target = Path.Combine(root, "out");
            var files = ModInstaller.Extract(package, target, overwrite: true);
            Assert.That(files, Is.EquivalentTo(new[] { "tool\\helper.exe", "readme.txt" }));
            Assert.Throws<InvalidOperationException>(() => ModInstaller.Extract(package, target, overwrite: false), "existing files are never replaced in a game folder");

            var hostile = Path.Combine(root, "hostile.zip");
            using (var zip = ZipFile.Open(hostile, ZipArchiveMode.Create)) AddEntry(zip, "../escape.txt", "x");
            Assert.Throws<InvalidOperationException>(() => ModInstaller.Extract(hostile, Path.Combine(root, "out2"), overwrite: true));
            Assert.That(File.Exists(Path.Combine(root, "escape.txt")), Is.False);
        }
        finally { DeleteTree(root); }
    }

    [Test]
    public async Task InstallsAHelperFromAVerifiedDownloadAndRemovesOnlyWhatItWrote()
    {
        var root = Path.Combine(Path.GetTempPath(), "madsense-install-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        try
        {
            var bytes = ZipBytes(("WF2_DSX.exe", "MZ-fake"), ("config/default.ini", "a=1"));
            var digest = Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(bytes)).ToLowerInvariant();
            var mod = new ModEntry
            {
                Id = "fake-helper", Name = "Fake", Author = "t", Page = "https://github.com/t/fake", KindName = "helper",
                Games = [new ModGame { Name = "G", Steam = ["1"] }],
                Package = new ModPackage { Url = "https://github.com/t/fake/releases/download/v1/fake.zip", Size = bytes.Length, Sha256 = digest, Version = "v1", Type = "zip" },
                Run = new ModRun { Exe = "WF2_DSX.exe" },
            };
            var installer = new ModInstaller(Path.Combine(root, "mods"), new HttpClient(new StubHandler(bytes)));
            var game = Game("G", "1");
            var record = await installer.InstallAsync(mod, game, null, CancellationToken.None);
            Assert.Multiple(() =>
            {
                Assert.That(record.Exe, Is.EqualTo("WF2_DSX.exe"));
                Assert.That(record.GameDir, Is.EqualTo(game.InstallDir), "the runner follows the game from the record alone");
                Assert.That(File.Exists(Path.Combine(record.Root, "WF2_DSX.exe")), Is.True);
                Assert.That(installer.IsInstalled("fake-helper", game.Key), Is.True);
                Assert.That(record.Sha256, Is.EqualTo(digest));
            });
            var reloaded = new ModInstaller(Path.Combine(root, "mods"), new HttpClient(new StubHandler(bytes)));
            Assert.That(reloaded.IsInstalled("fake-helper", game.Key), Is.True, "the registry survives a restart");
            reloaded.Uninstall("fake-helper", game.Key);
            Assert.That(Directory.Exists(record.Root), Is.False);
            Assert.That(reloaded.IsInstalled("fake-helper", game.Key), Is.False);

            // A tampered download (wrong digest) never lands.
            var badInstaller = new ModInstaller(Path.Combine(root, "mods2"), new HttpClient(new StubHandler(ZipBytes(("WF2_DSX.exe", "other")))));
            Assert.ThrowsAsync<InvalidOperationException>(() => badInstaller.InstallAsync(mod, game, null, CancellationToken.None));
            Assert.That(Directory.Exists(Path.Combine(root, "mods2", "fake-helper")), Is.False);
        }
        finally { DeleteTree(root); }
    }

    [Test]
    public async Task GameFolderModsNeverOverwriteGameFiles()
    {
        var root = Path.Combine(Path.GetTempPath(), "madsense-gamefolder-" + Guid.NewGuid().ToString("N"));
        var gameDir = Path.Combine(root, "game");
        Directory.CreateDirectory(gameDir);
        File.WriteAllText(Path.Combine(gameDir, "dinput8.dll"), "the game's own");
        try
        {
            var bytes = ZipBytes(("dinput8.dll", "mod"), ("mod.ini", "x"));
            var mod = new ModEntry
            {
                Id = "fake-folder", Name = "F", Author = "t", Page = "https://github.com/t/f", KindName = "gamefolder",
                Games = [new ModGame { Name = "G", Steam = ["2"] }],
                Package = new ModPackage { Url = "https://github.com/t/f/releases/download/v1/f.zip", Size = bytes.Length, Version = "v1", Type = "zip" },
            };
            var installer = new ModInstaller(Path.Combine(root, "mods"), new HttpClient(new StubHandler(bytes)));
            Exception? error = null;
            try { await installer.InstallAsync(mod, Game("G", "2", gameDir), null, CancellationToken.None); }
            catch (Exception e) { error = e; }
            TestContext.Out.WriteLine("error: " + (error?.GetType().Name ?? "none") + " " + error?.Message + " | game dir: " + string.Join(", ", Directory.GetFiles(gameDir).Select(Path.GetFileName)));
            Assert.That(error, Is.TypeOf<InvalidOperationException>());
            Assert.That(File.ReadAllText(Path.Combine(gameDir, "dinput8.dll")), Is.EqualTo("the game's own"));
        }
        finally { DeleteTree(root); }
    }

    [Test]
    public void WikiTemplateParsingReadsInputFieldsAndSteamAppId()
    {
        const string wikitext = """
            {{Infobox game
            |cover = x.png
            |steam appid  = 3489700
            |steam appid side = 123, 456
            }}
            {{Input
            |controller support        = true
            |playstation controllers   = true <!-- also DS4 -->
            |dualsense haptics support = true
            |dualsense adaptive trigger support = true
            |dualsense adaptive trigger support notes = Only with [[#Wired|wired]] in older builds<ref>src</ref>.
            |playstation speaker       = false
            |light bar support         = hackable
            |playstation connection modes = Wired, Wireless (Bluetooth)
            }}
            """;
        var input = PcGamingWikiClient.ParseTemplate(wikitext, "Input");
        var infobox = PcGamingWikiClient.ParseTemplate(wikitext, "Infobox game");
        var support = new WikiSupport { Fields = input, SteamAppId = infobox["steam appid"] };
        Assert.Multiple(() =>
        {
            Assert.That(support.SteamAppId, Is.EqualTo("3489700"));
            Assert.That(support.Haptics, Is.EqualTo(WikiValue.Yes));
            Assert.That(support.Triggers, Is.EqualTo(WikiValue.Yes));
            Assert.That(support.Speaker, Is.EqualTo(WikiValue.No));
            Assert.That(support.Lightbar, Is.EqualTo(WikiValue.ModRequired));
            Assert.That(support.Controller, Is.EqualTo(WikiValue.Yes), "trailing comments are stripped");
            Assert.That(support.NativeDualSense, Is.True);
            Assert.That(support.Note("dualsense adaptive trigger support"), Does.Not.Contain("<ref>"));
            Assert.That(support.ConnectionModes, Does.Contain("Bluetooth"));
        });
        Assert.That(PcGamingWikiClient.Similarity("Kingdom Come: Deliverance II", "Kingdom Come: Deliverance II"), Is.EqualTo(1));
        Assert.That(PcGamingWikiClient.Similarity("Resident Evil 4", "Resident Evil 4 (2005)"), Is.GreaterThanOrEqualTo(0.6));
        Assert.That(PcGamingWikiClient.Similarity("Control", "Control Ultimate Edition"), Is.LessThan(1));
    }

    [Test]
    public void ScansThisPcsLibrariesWithoutThrowingAndWithUniqueKeys()
    {
        var games = GameLibrary.Scan();
        Assert.That(games.Select(g => g.Key), Is.Unique);
        Assert.That(games.All(g => Directory.Exists(g.InstallDir)), Is.True);
    }

    /// <summary>Freshly written files can be held by a virus scanner for a moment; keep trying briefly.</summary>
    static void DeleteTree(string root)
    {
        for (int attempt = 0; ; attempt++)
        {
            try { Directory.Delete(root, true); return; }
            catch (IOException) when (attempt < 20) { Thread.Sleep(150); }
            catch (UnauthorizedAccessException) when (attempt < 20) { Thread.Sleep(150); }
        }
    }

    static void AddEntry(ZipArchive zip, string name, string content)
    {
        var entry = zip.CreateEntry(name);
        using var writer = new StreamWriter(entry.Open());
        writer.Write(content);
    }

    static byte[] ZipBytes(params (string Name, string Content)[] entries)
    {
        using var stream = new MemoryStream();
        using (var zip = new ZipArchive(stream, ZipArchiveMode.Create, true))
            foreach (var (name, content) in entries) AddEntry(zip, name, content);
        return stream.ToArray();
    }

    sealed class StubHandler(byte[] body) : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
        {
            var response = new HttpResponseMessage(HttpStatusCode.OK) { Content = new ByteArrayContent(body) };
            response.Content.Headers.ContentLength = body.Length;
            return Task.FromResult(response);
        }
    }
}
