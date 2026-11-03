using System.Collections.Concurrent;
using System.Text.Json;
using DualSenseClient.Controllers.DualSense.Audio;
using DualSenseClient.Controllers.DualSense.Output;
using DualSenseClient.Controllers.Emulation;
using DualSenseClient.Hid;

namespace MadSense.Mac.Tests;

public class SpeakerPreferencesTests
{
    private string directory = null!;
    private string path = null!;

    [SetUp]
    public void SetUp()
    {
        directory = Path.Combine(Path.GetTempPath(), "MadSense-speaker-tests-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(directory);
        path = Path.Combine(directory, "preferences.json");
    }

    [TearDown]
    public void TearDown() => Directory.Delete(directory, true);

    [TestCase(null)]
    [TestCase("{")]
    [TestCase("null")]
    [TestCase("[]")]
    [TestCase("{\"language\":\"es\"}")]
    [TestCase("{\"speaker\":[]}")]
    [TestCase("{\"speaker\":\"medium\"}")]
    [TestCase("{\"speaker\":\"off\",\"speaker\":\"low\"}")]
    public void MissingOlderOrMalformedPreferencesDefaultToHigh(string? document)
    {
        if (document != null) File.WriteAllText(path, document);
        Assert.That(new SpeakerPreferences(path).Speaker, Is.EqualTo("high"));
    }

    [Test]
    public void SelectionSurvivesRestartWithoutAudioAndPreservesUnknownSettings()
    {
        File.WriteAllText(path, "{\"language\":\"es\",\"future\":{\"enabled\":true},\"other\":null}");
        var preferences = new SpeakerPreferences(path);
        foreach (string level in new[] { "low", "off", "high" })
        {
            Assert.That(preferences.TryApplyCommand("speaker " + level), Is.True);
            Assert.That(new SpeakerPreferences(path).Speaker, Is.EqualTo(level));
            using var document = JsonDocument.Parse(File.ReadAllText(path));
            Assert.That(document.RootElement.GetProperty("language").GetString(), Is.EqualTo("es"));
            Assert.That(document.RootElement.GetProperty("future").GetProperty("enabled").GetBoolean(), Is.True);
            Assert.That(document.RootElement.GetProperty("other").ValueKind, Is.EqualTo(JsonValueKind.Null));
            Assert.That(Directory.GetFiles(directory, "*.tmp"), Is.Empty);
        }
    }

    [TestCase("speaker medium")]
    [TestCase("speaker HIGH")]
    [TestCase("speaker off extra")]
    [TestCase("speaker  off")]
    [TestCase("speaker")]
    [TestCase("pause")]
    public void InvalidCommandCannotChangeLiveOrPersistedChoice(string command)
    {
        const string document = "{\"speaker\":\"low\",\"future\":123}";
        File.WriteAllText(path, document);
        var preferences = new SpeakerPreferences(path);
        int callbacks = 0;
        Assert.That(preferences.TryApplyCommand(command, _ => callbacks++), Is.False);
        Assert.That(preferences.Speaker, Is.EqualTo("low"));
        Assert.That(callbacks, Is.Zero);
        Assert.That(File.ReadAllText(path), Is.EqualTo(document));
    }

    [Test]
    public void FailedAtomicReplacementKeepsLiveChoiceAndCleansTemporaryFile()
    {
        Directory.CreateDirectory(path); // A directory cannot be replaced by the JSON document.
        File.WriteAllText(Path.Combine(path, "sentinel"), "untouched");
        var warnings = new List<string>();
        var preferences = new SpeakerPreferences(path, warnings.Add);
        string? live = null;
        Assert.That(preferences.TryApplyCommand("speaker off", value => live = value), Is.True);
        Assert.That(preferences.Speaker, Is.EqualTo("off"));
        Assert.That(live, Is.EqualTo("off"));
        Assert.That(warnings, Has.Count.EqualTo(1));
        Assert.That(File.ReadAllText(Path.Combine(path, "sentinel")), Is.EqualTo("untouched"));
        Assert.That(Directory.GetFiles(directory, "*.tmp"), Is.Empty);
    }

    [Test]
    public void LiveSpeakerChangesReachBluetoothReportsWithoutMutingHaptics()
    {
        var output = new RecordingAudioOutputs();
        using var forwarder = new DualSenseAudioForwarder(output, null) { StrictNativeHaptics = true };
        Assert.That(forwarder.Start(), Is.True);
        var preferences = new SpeakerPreferences(path);
        foreach (var (level, volume, gain) in new[] { ("high", 100, 7), ("low", 100, 2), ("off", 0, 0), ("high", 100, 7) })
        {
            output.Reports.Clear();
            Assert.That(preferences.TryApplyCommand("speaker " + level, value => SpeakerLevels.Apply(forwarder, value)), Is.True);
            byte[] rearPcm = new byte[512 * 4];
            for (int sample = 0; sample < rearPcm.Length / 2; sample++) rearPcm[sample * 2 + 1] = 24;
            forwarder.UpdateGameHaptics(rearPcm);
            forwarder.FeedPcm(new float[1024]);
            Assert.That(SpinWait.SpinUntil(() => output.Reports.Any(r =>
                r.Volume == volume && r.Gain == gain && r.Haptics), TimeSpan.FromSeconds(5)), Is.True,
                $"{level} must set its hardware pair while rear-channel PCM still reaches the actuators");
        }
        Assert.That(forwarder.HasFailed, Is.False);
    }

    private sealed class RecordingAudioOutputs : IDualSenseAudioOutputs
    {
        public ConcurrentQueue<(byte Volume, byte Gain, bool Haptics)> Reports { get; } = new();
        public ConnectionType ConnectionType => ConnectionType.Bluetooth;
        public bool NeedsOutputStateBlock => false;
        public void SetVibration(byte left, byte right) { }
        public void SendOutputState(SetStateData payload) { }
        public void ResetBluetoothAudioStream() { }
        public void SendBluetoothAudioPrime(SetStateData state) { }
        public void SendBluetoothAudioAndHaptics(SetStateData state, ReadOnlySpan<byte> opusFrame,
            ReadOnlySpan<byte> hapticsPcm, BluetoothAudioRoute route) =>
            Reports.Enqueue((state.SpeakerVolume, state.AudioControl2, hapticsPcm.ContainsAnyExcept((byte)0)));
        public void SendBluetoothAudio(ReadOnlySpan<byte> opusFrame, BluetoothAudioRoute route) { }
        public void SetAudioOutput(AudioControl outputControl, byte speakerVolume, byte headphoneVolume, byte preampGain = 0x02) { }
    }
}
