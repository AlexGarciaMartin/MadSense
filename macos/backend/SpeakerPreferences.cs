using System.Text.Json;
using System.Text.Json.Nodes;
using DualSenseClient.Controllers.DualSense.Audio;

namespace MadSense.Mac;

internal static class SpeakerLevels
{
    public static bool IsValid(string? value) => value is "high" or "low" or "off";

    // Match the volume/pre-gain pairs offered by the Windows application.
    public static (byte Volume, byte PreGain) Resolve(string value) => value switch
    {
        "high" => (0x64, 7),
        "low" => (0x64, 2),
        "off" => (0, 0),
        _ => throw new ArgumentOutOfRangeException(nameof(value), "Invalid speaker level.")
    };

    public static void Apply(DualSenseAudioForwarder forwarder, string value)
    {
        var (volume, gain) = Resolve(value);
        forwarder.SpeakerVolume = volume;
        forwarder.SpeakerPreGain = gain;
        // Muting the speaker must not disable rear-channel haptics or HID effects.
    }
}

/// <summary>User-owned preferences; never stored in the signed application bundle.</summary>
internal sealed class SpeakerPreferences
{
    private readonly string path;
    private readonly Action<string> warn;
    public string Speaker { get; private set; }

    public SpeakerPreferences(string path, Action<string>? warn = null)
    {
        this.path = Path.GetFullPath(path);
        this.warn = warn ?? (_ => { });
        var data = Read();
        string? value = data["speaker"] is JsonValue node && node.TryGetValue<string>(out var text) ? text : null;
        Speaker = SpeakerLevels.IsValid(value) ? value! : "high";
    }

    public bool TryApplyCommand(string command, Action<string>? apply = null)
    {
        const string prefix = "speaker ";
        if (!command.StartsWith(prefix, StringComparison.Ordinal)) return false;
        string value = command[prefix.Length..];
        if (!SpeakerLevels.IsValid(value)) return false;
        Speaker = value;
        // A full disk must not prevent the requested live setting or reconnects
        // in this process. Surface the persistence failure without stopping HID.
        try { Save(value); }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException)
        { warn($"Could not save the speaker preference: {e.Message}"); }
        apply?.Invoke(value);
        return true;
    }

    private JsonObject Read()
    {
        try
        {
            var data = JsonNode.Parse(File.ReadAllText(path)) as JsonObject ?? new JsonObject();
            _ = data.Count; // Materialize here so duplicate-key errors also fall back safely.
            return data;
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException or JsonException or ArgumentException)
        { return new JsonObject(); }
    }

    private void Save(string value)
    {
        var data = Read();
        data["speaker"] = value;
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        string temporary = path + "." + Guid.NewGuid().ToString("N") + ".tmp";
        try
        {
            // Same-directory rename atomically replaces the previous complete
            // document. A failed write cannot leave preferences.json truncated.
            using (var stream = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None))
            {
                using (var writer = new Utf8JsonWriter(stream)) { data.WriteTo(writer); writer.Flush(); }
                stream.Flush(flushToDisk: true);
            }
            File.Move(temporary, path, overwrite: true);
        }
        finally
        {
            try { File.Delete(temporary); }
            catch (Exception e) when (e is IOException or UnauthorizedAccessException) { }
        }
    }
}
