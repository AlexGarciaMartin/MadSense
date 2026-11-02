using System.Text.Json;
using MadSense;

namespace MadSense.Installer.Tests;

[TestFixture]
public sealed class HiddenControllerRecoveryTests
{
    private string directory = null!;
    private string journal => Path.Combine(directory, HiddenControllerRecovery.JournalName);

    [SetUp]
    public void Setup()
    {
        directory = Path.Combine(Path.GetTempPath(), "MadSense-Installer-Tests", Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(directory);
    }

    [TearDown]
    public void Teardown()
    {
        // Only the fresh test-specific directory is owned by this test.
        Directory.Delete(directory, true);
    }

    [Test]
    public void NoJournalDoesNotOpenDriver()
    {
        HiddenControllerRecovery.Recover(directory, () => throw new AssertionException("Must not access HidHide."));
    }

    [Test]
    public void VerifiedRestorationDeletesJournalButPreservesPreferences()
    {
        File.WriteAllText(journal, JsonSerializer.Serialize(new HideLease(@"HID\TEST", false, false)));
        File.WriteAllText(Path.Combine(directory, "preferences.json"), "keep");
        var driver = new FakeState { Hidden = true, Active = true };
        HiddenControllerRecovery.Recover(directory, () => driver);
        Assert.Multiple(() =>
        {
            Assert.That(driver.Hidden, Is.False);
            Assert.That(driver.Active, Is.False);
            Assert.That(File.Exists(journal), Is.False);
            Assert.That(File.ReadAllText(Path.Combine(directory, "preferences.json")), Is.EqualTo("keep"));
        });
    }

    [Test]
    public void LegacyStringJournalRestoresItsOriginalSemantics()
    {
        File.WriteAllText(journal, JsonSerializer.Serialize(@"HID\OLD"));
        var driver = new FakeState { Hidden = true, Active = false };
        HiddenControllerRecovery.Recover(directory, () => driver);
        Assert.That(driver.Hidden, Is.False);
        Assert.That(driver.Active, Is.True);
    }

    [Test]
    public void UnavailableDriverPreservesRecoveryJournal()
    {
        var content = JsonSerializer.Serialize(new HideLease(@"HID\TEST", false, false));
        File.WriteAllText(journal, content);
        Assert.Throws<IOException>(() => HiddenControllerRecovery.Recover(directory, () => new FakeState { IsAvailable = false }));
        Assert.That(File.ReadAllText(journal), Is.EqualTo(content));
    }

    [Test]
    public void FailedReadbackIsNotMisinterpretedAsUnhidden()
    {
        var content = JsonSerializer.Serialize(new HideLease(@"HID\TEST", false, false));
        File.WriteAllText(journal, content);
        var driver = new FakeState { ThrowOnRead = true };
        Assert.Throws<IOException>(() => HiddenControllerRecovery.Recover(directory, () => driver));
        Assert.That(File.ReadAllText(journal), Is.EqualTo(content));
    }

    [Test]
    public void IgnoredMutationDoesNotDeleteJournal()
    {
        File.WriteAllText(journal, JsonSerializer.Serialize(new HideLease(@"HID\TEST", false, false)));
        var driver = new FakeState { Hidden = true, IgnoreMutation = true };
        Assert.Throws<IOException>(() => HiddenControllerRecovery.Recover(directory, () => driver));
        Assert.That(File.Exists(journal), Is.True);
    }

    [TestCase("null")]
    [TestCase("{\"Id\":\"USB\\\\BAD\",\"WasHidden\":false,\"WasActive\":false}")]
    public void InvalidJournalIsPreservedWithoutTouchingDriver(string content)
    {
        File.WriteAllText(journal, content);
        Assert.Throws<IOException>(() => HiddenControllerRecovery.Recover(directory, () => throw new AssertionException("Must not access driver.")));
        Assert.That(File.ReadAllText(journal), Is.EqualTo(content));
    }

    private sealed class FakeState : IHidHideRecoveryState
    {
        public bool IsAvailable { get; init; } = true;
        public bool Active { get; set; }
        public bool Hidden { get; set; }
        public bool IgnoreMutation { get; init; }
        public bool ThrowOnRead { get; init; }
        public bool IsHidden(string instanceId) => ThrowOnRead ? throw new IOException("Driver read failed.") : Hidden;
        public void SetHidden(string instanceId, bool hidden) { if (!IgnoreMutation) Hidden = hidden; }
    }
}
