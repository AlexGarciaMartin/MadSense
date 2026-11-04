using System.Text.Json;

namespace MadSense.Games;

/// <summary>Small, lazy, local preferences. Reading defaults never creates files or starts work.</summary>
sealed class ModPreferences(string path)
{
    sealed record Entry(string ModId, string GameKey, bool AutoStart = true, bool SetupComplete = false);
    readonly object gate = new();
    List<Entry>? entries;

    List<Entry> Load()
    {
        if (entries is not null) return entries;
        try { entries = File.Exists(path) ? JsonSerializer.Deserialize<List<Entry>>(File.ReadAllText(path)) ?? [] : []; }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException or JsonException) { entries = []; }
        return entries;
    }

    Entry Get(string modId, string gameKey) => Load().FirstOrDefault(e => e.ModId == modId && e.GameKey == gameKey) ?? new(modId, gameKey);
    public bool IsAutoStartEnabled(string modId, string gameKey) { lock (gate) return Get(modId, gameKey).AutoStart; }
    public bool IsSetupComplete(string modId, string gameKey) { lock (gate) return Get(modId, gameKey).SetupComplete; }
    public void SetAutoStartEnabled(string modId, string gameKey, bool enabled) => Save(modId, gameKey, e => e with { AutoStart = enabled });
    public void SetSetupComplete(string modId, string gameKey, bool complete) => Save(modId, gameKey, e => e with { SetupComplete = complete });

    void Save(string modId, string gameKey, Func<Entry, Entry> update)
    {
        lock (gate)
        {
            var previous = Get(modId, gameKey);
            var next = update(previous);
            if (next == previous) return;
            var saved = Load().Where(e => e.ModId != modId || e.GameKey != gameKey).ToList();
            saved.Add(next);
            Directory.CreateDirectory(Path.GetDirectoryName(path)!);
            // Replace the file only after a full write; failed saves leave both old states intact.
            var temp = path + ".tmp";
            try { File.WriteAllText(temp, JsonSerializer.Serialize(saved)); File.Move(temp, path, true); }
            finally { try { if (File.Exists(temp)) File.Delete(temp); } catch (IOException) { } catch (UnauthorizedAccessException) { } }
            entries = saved;
        }
    }
}

/// <summary>A stop applies to the complete current game session, including child processes.</summary>
sealed class ModSessionPolicy
{
    readonly HashSet<(string Mod, string Game)> stopped = [];
    public bool IsStopped(string modId, string gameKey) => stopped.Contains((modId, gameKey));
    public void Stop(string modId, string gameKey) => stopped.Add((modId, gameKey));
    public void Resume(string modId, string gameKey) => stopped.Remove((modId, gameKey));
    public void ObserveGames(IReadOnlySet<string> activeGames) => stopped.RemoveWhere(key => !activeGames.Contains(key.Game));
}
