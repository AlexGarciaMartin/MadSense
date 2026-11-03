using System.Text.Json.Nodes;

namespace MadSense;

static class AppPaths
{
    public static string Config => DirectoryFor("XDG_CONFIG_HOME", ".config");
    public static string State => DirectoryFor("XDG_STATE_HOME", ".local/state");
    static string DirectoryFor(string variable, string fallback)
    {
        var configured = Environment.GetEnvironmentVariable(variable);
        var root = !string.IsNullOrEmpty(configured) && Path.IsPathFullyQualified(configured)
            ? configured : Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), fallback);
        return Path.Combine(root, "madsense");
    }
}

sealed class LinuxPreferences(bool preview)
{
    readonly string path = Path.Combine(AppPaths.Config, "preferences.json");
    JsonObject data = new();
    public void Load()
    {
        if (preview) return;
        try { data = JsonNode.Parse(File.ReadAllText(path)) as JsonObject ?? new(); }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException or System.Text.Json.JsonException) { }
    }
    string Read(string key, string fallback) => data[key] is JsonValue v && v.TryGetValue<string>(out var s) ? s : fallback;
    public string Mode => Read("mode", "Auto") is "Xbox" ? "Xbox" : "Auto";
    public string Speaker => Read("speaker", "Alto") is var s && s is "Alto" or "Bajo" or "Off" ? s : "Alto";
    public string Language => L.IsSupportedPreference(Read("language", "auto")) ? Read("language", "auto") : "auto";
    public void Save(string key, string value)
    {
        data[key] = value;
        if (preview) return;
        Directory.CreateDirectory(AppPaths.Config);
        var temporary = path + "." + Guid.NewGuid().ToString("N") + ".tmp";
        try { File.WriteAllText(temporary, data.ToJsonString()); File.Move(temporary, path, true); }
        finally { if (File.Exists(temporary)) File.Delete(temporary); }
    }
}
