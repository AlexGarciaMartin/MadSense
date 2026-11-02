using System.Collections.Concurrent;
using System.Text.Json.Nodes;
using Microsoft.Win32;

namespace MadSense;

/// <summary>
/// The pad's speaker levels offered in the window and the tray. Each one is a pair the user heard on
/// hardware (volume byte, audio-control-2 pre-gain). The pre-gain feeds the pad's speaker compressor,
/// so its steps are far from even (2 → 4 is a big jump, 4 → 7 barely audible): three levels with a
/// real gap, not a slider and no "medium". The volume byte only matters between 0x3D and 0x64;
/// 0x00 is silence.
/// </summary>
static class SpeakerLevels
{
    public static readonly string[] All = ["Alto", "Bajo", "Off"];
    public static bool IsValid(string level) => Array.IndexOf(All, level) >= 0;
    public static (byte Volume, byte PreGain) Resolve(string level) => level switch
    {
        "Alto" => (0x64, 7),
        "Bajo" => (0x64, 2),
        _ => (0x00, 0)
    };
    public static string Label(string level) => L.Text(level switch { "Alto" => "Ui.SpeakerHigh", "Bajo" => "Ui.SpeakerLow", _ => "Ui.SpeakerOff" });
}

/// <summary>Atomic read-modify-write preserves old IDs and settings owned by future versions.</summary>
internal sealed class PreferenceStore
{
    static readonly ConcurrentDictionary<string, object> gates = new(StringComparer.OrdinalIgnoreCase);
    readonly string path;
    readonly string? installerLanguagePath;
    readonly object gate;
    public PreferenceStore(string path, string? installerLanguagePath = null)
    {
        this.path = Path.GetFullPath(path);
        this.installerLanguagePath = installerLanguagePath is null ? null : Path.GetFullPath(installerLanguagePath);
        gate = gates.GetOrAdd(this.path, _ => new object());
    }

    JsonObject Read()
    {
        try
        {
            var node = JsonNode.Parse(File.ReadAllText(path));
            if (node is JsonValue value && value.TryGetValue<string>(out var legacyMode)) return new JsonObject { ["mode"] = legacyMode };
            return node as JsonObject ?? new JsonObject();
        }
        catch { return new JsonObject(); }
    }
    static string? StringValue(JsonObject data, string key) => data[key] is JsonValue value && value.TryGetValue<string>(out var text) ? text : null;
    void Save(string key, string value)
    {
        lock (gate)
        {
            var data = Read();
            data[key] = value;
            Directory.CreateDirectory(Path.GetDirectoryName(path)!);
            File.WriteAllText(path + ".tmp", data.ToJsonString());
            File.Move(path + ".tmp", path, true);
        }
    }
    public string LoadMode()
    {
        lock (gate)
        {
            var mode = StringValue(Read(), "mode");
            return mode is "Auto" or "Native" or "Xbox" ? mode : "Auto";
        }
    }
    public void SaveMode(string mode)
    {
        if (mode is not ("Auto" or "Native" or "Xbox")) throw new ArgumentOutOfRangeException(nameof(mode));
        Save("mode", mode);
    }
    public string LoadSpeaker()
    {
        lock (gate)
        {
            var level = StringValue(Read(), "speaker");
            return level is not null && SpeakerLevels.IsValid(level) ? level : "Alto";
        }
    }
    public void SaveSpeaker(string level)
    {
        if (!SpeakerLevels.IsValid(level)) throw new ArgumentOutOfRangeException(nameof(level));
        Save("speaker", level);
    }
    public string LoadLanguage()
    {
        lock (gate)
        {
            var data = Read();
            if (data.ContainsKey("language"))
            {
                var preference = StringValue(data, "language");
                return L.IsSupportedPreference(preference) ? preference! : "auto";
            }
            try
            {
                var seed = installerLanguagePath is null ? null : File.ReadAllText(installerLanguagePath).Trim();
                if (seed is not null && L.Languages.Any(l => l.Code == seed)) return seed;
            }
            catch { /* Portable and earlier installations have no language seed. */ }
            return "auto";
        }
    }
    public void SaveLanguage(string language)
    {
        if (!L.IsSupportedPreference(language)) throw new ArgumentOutOfRangeException(nameof(language));
        Save("language", language);
    }
}

static class Preferences
{
    const string RunKey = @"Software\Microsoft\Windows\CurrentVersion\Run";
    const string RunValue = "MadSense";
    static readonly PreferenceStore store = new("preferences.json", "installer-language.txt");
    // Avoid beforefieldinit: Main (or the isolated preview) establishes CWD first.
    static Preferences() { }
    public static string LoadMode() => store.LoadMode();
    public static void SaveMode(string mode) => store.SaveMode(mode);
    public static string LoadSpeaker() => store.LoadSpeaker();
    public static void SaveSpeaker(string level) => store.SaveSpeaker(level);
    public static string LoadLanguage() => store.LoadLanguage();
    public static void SaveLanguage(string language) => store.SaveLanguage(language);

    /// <summary>Whether this executable is registered to start with the user's session (tray only).</summary>
    public static bool StartsWithWindows
    {
        get
        {
            try
            {
                using var key = Registry.CurrentUser.OpenSubKey(RunKey);
                return key?.GetValue(RunValue) is string command && command.Contains(Environment.ProcessPath ?? "\0", StringComparison.OrdinalIgnoreCase);
            }
            catch { return false; }
        }
        set
        {
            using var key = Registry.CurrentUser.CreateSubKey(RunKey);
            if (value) key.SetValue(RunValue, $"\"{Environment.ProcessPath}\" --tray");
            else key.DeleteValue(RunValue, false);
        }
    }
}

static class LogFiles
{
    static readonly object gate = new();
    public static void Append(string file, string line)
    {
        try
        {
            lock (gate)
            {
                if (File.Exists(file) && new FileInfo(file).Length >= 2 * 1024 * 1024)
                    File.Move(file, file + ".previous", true);
                File.AppendAllText(file, line);
            }
        }
        catch { /* A full disk must not stop the input bridge. */ }
    }
}
