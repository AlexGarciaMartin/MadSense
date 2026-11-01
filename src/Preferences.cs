using System.Text.Json;
using System.Text.Json.Serialization;
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
    public static string Label(string level) => level switch { "Alto" => "alto", "Bajo" => "bajo", _ => "apagado" };
}

static class Preferences
{
    const string RunKey = @"Software\Microsoft\Windows\CurrentVersion\Run";
    const string RunValue = "MadSense";
    static readonly object gate = new();

    sealed class Stored
    {
        [JsonPropertyName("mode")] public string Mode { get; set; } = "Auto";
        [JsonPropertyName("speaker")] public string Speaker { get; set; } = "Alto";
    }

    /// <summary>Reads the file; a 0.4 file holding only the mode string is still accepted.</summary>
    static Stored Load()
    {
        try
        {
            var text = File.ReadAllText("preferences.json");
            using var doc = JsonDocument.Parse(text);
            if (doc.RootElement.ValueKind == JsonValueKind.String) return new Stored { Mode = doc.RootElement.GetString() ?? "Auto" };
            return JsonSerializer.Deserialize<Stored>(text) ?? new Stored();
        }
        catch { return new Stored(); }
    }
    static void Save(Stored stored)
    {
        lock (gate)
        {
            File.WriteAllText("preferences.json.tmp", JsonSerializer.Serialize(stored));
            File.Move("preferences.json.tmp", "preferences.json", true);
        }
    }

    public static string LoadMode()
    {
        var mode = Load().Mode;
        return mode is "Auto" or "Native" or "Xbox" ? mode : "Auto";
    }
    public static void SaveMode(string mode) { var s = Load(); s.Mode = mode; Save(s); }

    public static string LoadSpeaker()
    {
        var level = Load().Speaker;
        return SpeakerLevels.IsValid(level) ? level : "Alto";
    }
    public static void SaveSpeaker(string level) { var s = Load(); s.Speaker = level; Save(s); }

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
