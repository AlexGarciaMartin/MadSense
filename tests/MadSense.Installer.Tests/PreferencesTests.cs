using System.Text.Json;
using MadSense;

namespace MadSense.Installer.Tests;

[TestFixture]
public sealed class PreferencesTests
{
    string directory = "";
    string preferencesPath = "";
    string installerLanguagePath = "";
    PreferenceStore store = null!;

    [SetUp]
    public void CreateIsolatedStore()
    {
        directory = Path.Combine(Path.GetTempPath(), "MadSense-preferences-tests", Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(directory);
        preferencesPath = Path.Combine(directory, "preferences.json");
        installerLanguagePath = Path.Combine(directory, "installer-language.txt");
        store = new PreferenceStore(preferencesPath, installerLanguagePath);
    }

    [TearDown]
    public void RemoveIsolatedStore() => Directory.Delete(directory, recursive: true);

    [Test]
    public void FreshInstallUsesStableDefaults()
    {
        Assert.Multiple(() =>
        {
            Assert.That(store.LoadMode(), Is.EqualTo("Auto"));
            Assert.That(store.LoadSpeaker(), Is.EqualTo("Alto"));
            Assert.That(store.LoadLanguage(), Is.EqualTo("auto"));
        });
    }

    [TestCase("{")]
    [TestCase("null")]
    [TestCase("[]")]
    [TestCase("42")]
    [TestCase("{\"mode\":false,\"speaker\":[],\"language\":42}")]
    [TestCase("{\"mode\":\"obsolete\",\"speaker\":\"medium\",\"language\":\"unknown\"}")]
    public void CorruptOrInvalidPreferencesDoNotPreventStartup(string contents)
    {
        File.WriteAllText(preferencesPath, contents);
        Assert.Multiple(() =>
        {
            Assert.That(store.LoadMode(), Is.EqualTo("Auto"));
            Assert.That(store.LoadSpeaker(), Is.EqualTo("Alto"));
            Assert.That(store.LoadLanguage(), Is.EqualTo("auto"));
        });
        store.SaveLanguage("fr");
        Assert.That(new PreferenceStore(preferencesPath).LoadLanguage(), Is.EqualTo("fr"));
    }

    [TestCase("Auto")]
    [TestCase("Native")]
    [TestCase("Xbox")]
    public void LegacyBareModeStringIsMigratedWithoutChangingTheMode(string mode)
    {
        File.WriteAllText(preferencesPath, JsonSerializer.Serialize(mode));
        Assert.That(store.LoadMode(), Is.EqualTo(mode));
        Assert.That(store.LoadSpeaker(), Is.EqualTo("Alto"));
        store.SaveLanguage("de");
        var reloaded = new PreferenceStore(preferencesPath);
        Assert.That(reloaded.LoadMode(), Is.EqualTo(mode));
        Assert.That(reloaded.LoadLanguage(), Is.EqualTo("de"));
    }

    [Test]
    public void ExistingModeAndSpeakerSurviveAddingLanguage()
    {
        File.WriteAllText(preferencesPath, "{\"mode\":\"Native\",\"speaker\":\"Bajo\"}");
        store.SaveLanguage("pt-BR");
        var reloaded = new PreferenceStore(preferencesPath);
        Assert.That(reloaded.LoadMode(), Is.EqualTo("Native"));
        Assert.That(reloaded.LoadSpeaker(), Is.EqualTo("Bajo"));
        Assert.That(reloaded.LoadLanguage(), Is.EqualTo("pt-BR"));
        using var json = JsonDocument.Parse(File.ReadAllText(preferencesPath));
        Assert.That(json.RootElement.GetProperty("mode").GetString(), Is.EqualTo("Native"));
        Assert.That(json.RootElement.GetProperty("speaker").GetString(), Is.EqualTo("Bajo"));
    }

    [Test]
    public void UpdatingEachPreferencePreservesOtherPreferencesAndUnknownFields()
    {
        File.WriteAllText(preferencesPath,
            "{\"mode\":\"Native\",\"speaker\":\"Bajo\",\"language\":\"es\",\"future\":{\"enabled\":true,\"values\":[1,2]},\"other\":null}");
        store.SaveLanguage("it");
        store.SaveMode("Xbox");
        store.SaveSpeaker("Off");
        var reloaded = new PreferenceStore(preferencesPath);
        Assert.That(reloaded.LoadMode(), Is.EqualTo("Xbox"));
        Assert.That(reloaded.LoadSpeaker(), Is.EqualTo("Off"));
        Assert.That(reloaded.LoadLanguage(), Is.EqualTo("it"));
        using var json = JsonDocument.Parse(File.ReadAllText(preferencesPath));
        Assert.That(json.RootElement.GetProperty("future").GetProperty("enabled").GetBoolean(), Is.True);
        Assert.That(json.RootElement.GetProperty("future").GetProperty("values").EnumerateArray().Select(value => value.GetInt32()), Is.EqualTo(new[] { 1, 2 }));
        Assert.That(json.RootElement.GetProperty("other").ValueKind, Is.EqualTo(JsonValueKind.Null));
    }

    [Test]
    public void InstallerLanguageSeedsOnlyAnUnspecifiedAppPreference()
    {
        File.WriteAllText(installerLanguagePath, "fr");
        Assert.That(store.LoadLanguage(), Is.EqualTo("fr"));
        store.SaveLanguage("de");
        Assert.That(store.LoadLanguage(), Is.EqualTo("de"));
        store.SaveLanguage("auto");
        Assert.That(new PreferenceStore(preferencesPath, installerLanguagePath).LoadLanguage(), Is.EqualTo("auto"));
    }

    [Test]
    public void InvalidInstallerLanguageKeepsAutomaticSelection()
    {
        File.WriteAllText(installerLanguagePath, "unknown");
        Assert.That(store.LoadLanguage(), Is.EqualTo("auto"));
    }

    [Test]
    public void ConcurrentUpdatesDoNotLoseIndependentPreferences()
    {
        // Different UI actions can save concurrently. Each property has one writer, so all final
        // values are deterministic even though their read/modify/write operations overlap.
        using var ready = new CountdownEvent(3);
        using var start = new ManualResetEventSlim(false);
        Task SaveRepeatedly(Action save) => Task.Run(() =>
        {
            ready.Signal();
            start.Wait();
            for (var attempt = 0; attempt < 30; attempt++) save();
        });
        var tasks = new[]
        {
            SaveRepeatedly(() => store.SaveMode("Xbox")),
            SaveRepeatedly(() => store.SaveSpeaker("Bajo")),
            SaveRepeatedly(() => store.SaveLanguage("fr"))
        };
        var allWritersReady = ready.Wait(TimeSpan.FromSeconds(10));
        start.Set();
        Assert.That(Task.WaitAll(tasks, TimeSpan.FromSeconds(20)), Is.True);
        Assert.That(allWritersReady, Is.True);
        var reloaded = new PreferenceStore(preferencesPath);
        Assert.That(reloaded.LoadMode(), Is.EqualTo("Xbox"));
        Assert.That(reloaded.LoadSpeaker(), Is.EqualTo("Bajo"));
        Assert.That(reloaded.LoadLanguage(), Is.EqualTo("fr"));
    }
}
