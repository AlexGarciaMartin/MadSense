using System.Text.Json.Nodes;
using NUnit.Framework;

namespace MadSense.Tests;

public sealed class PreferencesTests
{
    [Test]
    public void FailedSaveKeepsLiveSelectionAndReportsThePersistenceFailure()
    {
        var directory = Path.Combine(Path.GetTempPath(), "madsense-preferences-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(directory);
        try
        {
            // A regular file as the parent fails even when CI runs with extra
            // permissions, so this exercises a real filesystem error on both OSes.
            var blocker = Path.Combine(directory, "blocked");
            File.WriteAllText(blocker, "preserve this file");
            var preferences = new LinuxPreferences(false, Path.Combine(blocker, "preferences.json"));
            Assert.DoesNotThrow(() => preferences.Save("speaker", "Bajo"));
            Assert.That(preferences.Speaker, Is.EqualTo("Bajo"));
            Assert.That(LinuxLog.Recent(), Does.Contain("Could not save preferences"));
            Assert.That(File.ReadAllText(blocker), Is.EqualTo("preserve this file"));
        }
        finally { Directory.Delete(directory, recursive: true); }
    }

    [Test]
    public void SuccessfulSaveRetainsUnknownSettingsAndLeavesNoTemporaryFile()
    {
        var directory = Path.Combine(Path.GetTempPath(), "madsense-preferences-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(directory);
        try
        {
            var path = Path.Combine(directory, "preferences.json");
            File.WriteAllText(path, "{\"mode\":\"Auto\",\"futureSetting\":{\"value\":7}}");
            var preferences = new LinuxPreferences(false, path);
            preferences.Load();
            preferences.Save("mode", "Xbox");
            var restored = new LinuxPreferences(false, path);
            restored.Load();
            Assert.That(restored.Mode, Is.EqualTo("Xbox"));
            Assert.That(JsonNode.Parse(File.ReadAllText(path))!["futureSetting"]!["value"]!.GetValue<int>(), Is.EqualTo(7));
            Assert.That(Directory.GetFiles(directory), Is.EqualTo(new[] { path }));
        }
        finally { Directory.Delete(directory, recursive: true); }
    }
}
