using System.Text.Json;
using System.Xml.Linq;
using Microsoft.Win32;

namespace MadSense.Games;

/// <summary>An installed game as a launcher recorded it. Discovery only: nothing here starts, probes or edits a game.</summary>
sealed record LibraryGame(string Key, string Name, string Source, string? SteamAppId, string InstallDir, string? Exe)
{
    public bool Contains(string path) => path.StartsWith(InstallDir.TrimEnd('\\') + "\\", StringComparison.OrdinalIgnoreCase);
}

/// <summary>
/// Reads the installed games every launcher on this PC keeps a record of: Steam (manifests), Epic
/// (item manifests), GOG Galaxy, Ubisoft Connect (registry), Xbox / Microsoft Store (XboxGames
/// folders) and the Windows uninstall list for the launchers that write nothing else (EA, Rockstar,
/// Battle.net, Riot, Bethesda). Each source is read in isolation: one unreadable launcher never
/// hides the others. The approach follows the open-source Zerk Sense 5 library scanner (MIT).
/// </summary>
static class GameLibrary
{
    static readonly string[] SteamUtilities = ["228980", "1812620", "250820", "1070560", "431960", "993090"];
    static readonly string[] Publishers = ["Electronic Arts", "Rockstar Games", "Blizzard Entertainment", "Riot Games", "Bethesda", "Ubisoft", "CD PROJEKT", "Square Enix"];

    public static IReadOnlyList<LibraryGame> Scan(CancellationToken cancellationToken = default)
    {
        var games = new List<LibraryGame>();
        var warnings = new List<string>();
        void Try(string source, Func<IEnumerable<LibraryGame>> read)
        {
            cancellationToken.ThrowIfCancellationRequested();
            try { games.AddRange(read().ToList()); }
            catch (Exception e) when (e is IOException or UnauthorizedAccessException or System.Security.SecurityException or JsonException or System.Xml.XmlException or FormatException)
            { warnings.Add($"{source}: {e.Message}"); }
        }
        Try("Steam", () => Steam(cancellationToken));
        Try("Epic", Epic);
        Try("GOG", Gog);
        Try("Ubisoft", Ubisoft);
        Try("Xbox", Xbox);
        Try("Uninstall", () => Uninstall(games));
        Warnings = warnings;
        return games
            .Where(g => Directory.Exists(g.InstallDir))
            .GroupBy(g => g.SteamAppId is { } id ? "steam:" + id : g.InstallDir.TrimEnd('\\').ToLowerInvariant())
            .Select(g => g.First())
            .OrderBy(g => g.Name, StringComparer.CurrentCultureIgnoreCase)
            .ToList();
    }

    /// <summary>Launchers that could not be read during the last <see cref="Scan"/>.</summary>
    public static IReadOnlyList<string> Warnings { get; private set; } = [];

    static IEnumerable<LibraryGame> Steam(CancellationToken cancellationToken)
    {
        var result = new SteamLibraryScanner().Scan(cancellationToken);
        foreach (var game in result.Games)
            if (!SteamUtilities.Contains(game.AppId))
                yield return new LibraryGame("steam:" + game.AppId, game.Name, "Steam", game.AppId, game.InstallDirectory, null);
    }

    static IEnumerable<LibraryGame> Epic()
    {
        var folder = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData), "Epic", "EpicGamesLauncher", "Data", "Manifests");
        if (!Directory.Exists(folder)) yield break;
        foreach (var item in Directory.EnumerateFiles(folder, "*.item"))
        {
            if (new FileInfo(item).Length > 1024 * 1024) continue;
            using var document = JsonDocument.Parse(File.ReadAllText(item));
            var root = document.RootElement;
            if (root.ValueKind != JsonValueKind.Object) continue;
            bool isGame = root.TryGetProperty("AppCategories", out var categories) && categories.ValueKind == JsonValueKind.Array
                          && categories.EnumerateArray().Any(c => c.ValueKind == JsonValueKind.String && c.GetString() == "games");
            string? location = Text(root, "InstallLocation"), name = Text(root, "DisplayName"), app = Text(root, "AppName"), exe = Text(root, "LaunchExecutable");
            if (!isGame || string.IsNullOrWhiteSpace(location) || string.IsNullOrWhiteSpace(name) || string.IsNullOrWhiteSpace(app)) continue;
            location = location.Replace('/', '\\');
            yield return new LibraryGame("epic:" + app, name, "Epic Games", null, location, string.IsNullOrWhiteSpace(exe) ? null : Path.Combine(location, exe.Replace('/', '\\')));
        }
    }

    static IEnumerable<LibraryGame> Gog()
    {
        using var games = Registry.LocalMachine.OpenSubKey(@"SOFTWARE\WOW6432Node\GOG.com\Games");
        if (games == null) yield break;
        foreach (var id in games.GetSubKeyNames())
        {
            using var key = games.OpenSubKey(id);
            if (key?.GetValue("path") is not string path || path.Length == 0) continue;
            var name = key.GetValue("gameName") as string ?? Path.GetFileName(path.TrimEnd('\\'));
            if (key.GetValue("dependsOn") is string depends && depends.Length > 0) continue; // DLC rows point at their base game
            yield return new LibraryGame("gog:" + id, name, "GOG", null, path, key.GetValue("exe") as string);
        }
    }

    static IEnumerable<LibraryGame> Ubisoft()
    {
        using var installs = Registry.LocalMachine.OpenSubKey(@"SOFTWARE\WOW6432Node\Ubisoft\Launcher\Installs");
        if (installs == null) yield break;
        foreach (var id in installs.GetSubKeyNames())
        {
            using var key = installs.OpenSubKey(id);
            if (key?.GetValue("InstallDir") is not string dir || dir.Length == 0) continue;
            dir = dir.Replace('/', '\\');
            yield return new LibraryGame("ubisoft:" + id, Path.GetFileName(dir.TrimEnd('\\')), "Ubisoft Connect", null, dir, null);
        }
    }

    static IEnumerable<LibraryGame> Xbox()
    {
        foreach (var drive in DriveInfo.GetDrives().Where(d => d.DriveType == DriveType.Fixed && d.IsReady))
        {
            var root = Path.Combine(drive.RootDirectory.FullName, "XboxGames");
            if (!Directory.Exists(root)) continue;
            foreach (var folder in Directory.EnumerateDirectories(root))
            {
                var config = Path.Combine(folder, "Content", "MicrosoftGame.config");
                if (!File.Exists(config)) continue;
                var xml = XDocument.Load(config);
                var name = xml.Descendants("ShellVisuals").FirstOrDefault()?.Attribute("DefaultDisplayName")?.Value;
                if (string.IsNullOrWhiteSpace(name) || name.StartsWith("ms-resource:", StringComparison.OrdinalIgnoreCase)) name = Path.GetFileName(folder);
                var exe = xml.Descendants("Executable").FirstOrDefault()?.Attribute("Name")?.Value;
                var content = Path.Combine(folder, "Content");
                yield return new LibraryGame("xbox:" + Path.GetFileName(folder).ToLowerInvariant(), name, "Xbox", null, content, exe == null ? null : Path.Combine(content, exe));
            }
        }
    }

    static IEnumerable<LibraryGame> Uninstall(List<LibraryGame> known)
    {
        foreach (var path in new[] { @"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall", @"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall" })
        {
            using var root = Registry.LocalMachine.OpenSubKey(path);
            if (root == null) continue;
            foreach (var sub in root.GetSubKeyNames())
            {
                using var key = root.OpenSubKey(sub);
                if (key?.GetValue("Publisher") is not string publisher || !Publishers.Any(p => publisher.Contains(p, StringComparison.OrdinalIgnoreCase))) continue;
                if (key.GetValue("InstallLocation") is not string dir || dir.Length < 4 || key.GetValue("DisplayName") is not string name) continue;
                if (name.Contains("Launcher", StringComparison.OrdinalIgnoreCase) || name.Contains("Battle.net", StringComparison.OrdinalIgnoreCase)
                    || name.Contains("EA app", StringComparison.OrdinalIgnoreCase) || name.Contains("Connect", StringComparison.OrdinalIgnoreCase)
                    || name.Contains("Redistributable", StringComparison.OrdinalIgnoreCase) || name.Contains("Galaxy", StringComparison.OrdinalIgnoreCase)) continue;
                dir = dir.Trim('"');
                if (known.Any(g => string.Equals(g.InstallDir.TrimEnd('\\'), dir.TrimEnd('\\'), StringComparison.OrdinalIgnoreCase) || g.Contains(dir + "\\"))) continue;
                yield return new LibraryGame("dir:" + dir.TrimEnd('\\').ToLowerInvariant(), name, publisher, null, dir, null);
            }
        }
    }

    static string? Text(JsonElement element, string name) => element.TryGetProperty(name, out var value) && value.ValueKind == JsonValueKind.String ? value.GetString() : null;
}
