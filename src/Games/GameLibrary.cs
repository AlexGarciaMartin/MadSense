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
/// Battle.net, Riot, Bethesda), plus the plain games folders no launcher wrote (see Folders). Each source is read in isolation: one unreadable launcher never
/// hides the others. The approach follows the open-source Zerk Sense 5 library scanner (MIT).
/// </summary>
static class GameLibrary
{
    static readonly string[] SteamUtilities = ["228980", "1812620", "250820", "1070560", "431960", "993090"];
    static readonly string[] Publishers = ["Electronic Arts", "Rockstar Games", "Blizzard Entertainment", "Riot Games", "Bethesda", "Ubisoft", "CD PROJEKT", "Square Enix"];

    public static IReadOnlyList<LibraryGame> Scan(CancellationToken cancellationToken = default, IReadOnlyList<string>? folders = null)
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
        Try("Folders", () => Folders(FolderRoots(games, folders ?? []), games));
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

    // ── plain folders: games that no launcher recorded (offline installers, itch, repacks) ──────
    static readonly string[] FolderNames = ["Games", "Juegos", "Jogos", "Spiele", "Jeux", "Giochi", "Gry", "GOG Games"];
    static readonly string[] Containers = ["Epic Games", "GOG Games", "Ubisoft", "Ubisoft Game Launcher", "Rockstar Games", "EA Games", "Origin Games", "Battle.net", "Riot Games", "Amazon Games", "itch"];
    static readonly string[] SkipFolders = ["Steam", "SteamLibrary", "steamapps", "XboxGames", "WindowsApps", "Launcher", "_CommonRedist", "_Redist", "Redist", "Engine", "DirectX", "Support", "dotnet", "vcredist", "Tools", "Saves"];
    static readonly string[] UtilityExes = ["unins", "setup", "install", "redist", "vc_", "dxsetup", "dxweb", "crash", "report", "sfv", "unrar", "7z", "dotnet", "python", "java", "cef", "subprocess", "eac", "anticheat", "battleye", "uploader", "handler", "updater", "helper"];

    static readonly string[] SystemFolders = ["Windows", "Program Files", "Program Files (x86)", "ProgramData", "AppData", "$Recycle.Bin", "System Volume Information", "Recovery", "PerfLogs", "Intermediate", "Saved"];
    static readonly EnumerationOptions Listing = new() { IgnoreInaccessible = true, AttributesToSkip = FileAttributes.Hidden | FileAttributes.System | FileAttributes.ReparsePoint };

    /// <summary>
    /// Folders worth a look, cheapest first: the ones the user added; a games folder at the root
    /// of each fixed drive and one level below it (D:\Alex\Juegos); the profile's Games; and, since
    /// people keep games next to the ones a launcher already put there, the parent and grandparent
    /// of every known non-Steam game, never a drive root or a system folder.
    /// </summary>
    static IEnumerable<string> FolderRoots(IReadOnlyList<LibraryGame> known, IEnumerable<string> extra) =>
        FolderRoots(known, extra, DriveInfo.GetDrives().Where(d => d.DriveType == DriveType.Fixed && d.IsReady).Select(d => d.RootDirectory.FullName));

    internal static IEnumerable<string> FolderRoots(IReadOnlyList<LibraryGame> known, IEnumerable<string> extra, IEnumerable<string> driveRoots)
    {
        foreach (var folder in extra) yield return folder;
        foreach (var drive in driveRoots)
        {
            foreach (var name in FolderNames) yield return Path.Combine(drive, name);
            List<string> tops;
            try { tops = Directory.EnumerateDirectories(drive, "*", Listing).ToList(); }
            catch (Exception e) when (e is IOException or UnauthorizedAccessException) { continue; }
            foreach (var top in tops)
                if (!SystemFolders.Contains(Path.GetFileName(top), StringComparer.OrdinalIgnoreCase))
                    foreach (var name in FolderNames) yield return Path.Combine(top, name);
        }
        yield return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "Games");
        foreach (var game in known)
        {
            if (game.Source is "Steam" or "Xbox") continue;
            var parent = Path.GetDirectoryName(game.InstallDir.TrimEnd('\\'));
            for (int level = 0; level < 2 && parent is not null && !IsDriveRoot(parent) && !IsSystemPath(parent); level++)
            {
                yield return parent;
                parent = Path.GetDirectoryName(parent);
            }
        }
    }

    static bool IsDriveRoot(string path) => string.Equals(Path.GetPathRoot(path)?.TrimEnd('\\'), path.TrimEnd('\\'), StringComparison.OrdinalIgnoreCase);

    static bool IsSystemPath(string path) => path.Split('\\', StringSplitOptions.RemoveEmptyEntries).Any(s => SystemFolders.Contains(s, StringComparer.OrdinalIgnoreCase));


    /// <summary>
    /// Every direct subfolder of a games folder that holds a real executable within four levels is
    /// a game; a launcher's own folder (Epic Games, GOG Games…) or one that already contains a known
    /// game is looked into one level deeper instead. Only directory listings are read, bounded per
    /// candidate, so a drive full of games costs a few milliseconds and no file is opened.
    /// </summary>
    internal static IEnumerable<LibraryGame> Folders(IEnumerable<string> roots, IReadOnlyList<LibraryGame> known)
    {
        var options = Listing;
        foreach (var root in roots.Distinct(StringComparer.OrdinalIgnoreCase))
        {
            if (!Directory.Exists(root)) continue;
            foreach (var folder in Directory.EnumerateDirectories(root, "*", options))
            {
                if (SkipFolders.Contains(Path.GetFileName(folder), StringComparer.OrdinalIgnoreCase)) continue;
                bool container = Containers.Contains(Path.GetFileName(folder), StringComparer.OrdinalIgnoreCase) || known.Any(g => Covers(folder, g.InstallDir) && !Covers(g.InstallDir, folder));
                IEnumerable<string> candidates = container ? Directory.EnumerateDirectories(folder, "*", options) : [folder];
                foreach (var candidate in candidates)
                {
                    if (known.Any(g => Covers(g.InstallDir, candidate) || Covers(candidate, g.InstallDir))) continue;
                    var exe = BestExecutable(candidate, options);
                    if (exe is null) continue;
                    yield return new LibraryGame("dir:" + candidate.TrimEnd('\\').ToLowerInvariant(), CleanName(Path.GetFileName(candidate)), root, null, candidate, exe);
                }
            }
        }
    }

    static bool Covers(string parent, string path)
    {
        var top = parent.TrimEnd('\\');
        return path.TrimEnd('\\').Equals(top, StringComparison.OrdinalIgnoreCase) || path.StartsWith(top + "\\", StringComparison.OrdinalIgnoreCase);
    }

    /// <summary>Breadth-first, four levels, at most 160 folders: the largest executable that is not a tool.</summary>
    static string? BestExecutable(string folder, EnumerationOptions options)
    {
        var queue = new Queue<(string Dir, int Depth)>();
        queue.Enqueue((folder, 0));
        string? best = null; long bestSize = -1; int visited = 0;
        while (queue.Count > 0 && visited < 160)
        {
            var (dir, depth) = queue.Dequeue();
            visited++;
            try
            {
                foreach (var file in Directory.EnumerateFiles(dir, "*.exe", options))
                {
                    var stem = Path.GetFileNameWithoutExtension(file).ToLowerInvariant();
                    if (UtilityExes.Any(stem.Contains)) continue;
                    long size = new FileInfo(file).Length;
                    if (size > bestSize) { bestSize = size; best = file; }
                }
                if (depth >= 4) continue;
                foreach (var sub in Directory.EnumerateDirectories(dir, "*", options))
                    if (!SkipFolders.Contains(Path.GetFileName(sub), StringComparer.OrdinalIgnoreCase)) queue.Enqueue((sub, depth + 1));
            }
            catch (Exception e) when (e is IOException or UnauthorizedAccessException) { }
        }
        return best;
    }

    /// <summary>The folder name as a title: no bracketed tags, version suffix or underscores.</summary>
    internal static string CleanName(string folder)
    {
        var name = folder.Replace('_', ' ');
        name = System.Text.RegularExpressions.Regex.Replace(name, @"\s*[\[\(][^\]\)]*[\]\)]", "");
        name = System.Text.RegularExpressions.Regex.Replace(name, @"[\s.]+v?\d+(\.\d+)+[a-z]?$", "", System.Text.RegularExpressions.RegexOptions.IgnoreCase);
        name = System.Text.RegularExpressions.Regex.Replace(name, @"\s{2,}", " ").Trim(' ', '-', '.');
        return name.Length == 0 ? folder : name;
    }

    static string? Text(JsonElement element, string name) => element.TryGetProperty(name, out var value) && value.ValueKind == JsonValueKind.String ? value.GetString() : null;
}
