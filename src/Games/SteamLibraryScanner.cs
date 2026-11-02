using System.Globalization;
using System.Security;
using System.Text;
using Microsoft.Win32;

namespace MadSense.Games;

internal sealed record InstalledSteamGame(string AppId, string Name, string InstallDirectory, string ManifestPath);
internal sealed record SteamScanResult(IReadOnlyList<InstalledSteamGame> Games, IReadOnlyList<string> Warnings);

/// <summary>
/// Reads Steam's own installation manifests. This is discovery only: it never writes Steam
/// configuration, probes game executables, starts Steam or infers controller support from a name.
/// </summary>
internal sealed class SteamLibraryScanner
{
    const int MaximumFileBytes = 1024 * 1024;
    const int MaximumLibraries = 256;
    const int MaximumManifestsPerLibrary = 20000;

    public SteamScanResult Scan(CancellationToken cancellationToken = default) => Scan(FindSteamRoots(), cancellationToken);

    public SteamScanResult Scan(IEnumerable<string> steamRoots, CancellationToken cancellationToken = default)
    {
        ArgumentNullException.ThrowIfNull(steamRoots);
        cancellationToken.ThrowIfCancellationRequested();
        var warnings = new List<string>();
        var libraries = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var games = new Dictionary<string, InstalledSteamGame>(StringComparer.Ordinal);

        foreach (var root in steamRoots)
        {
            cancellationToken.ThrowIfCancellationRequested();
            if (!TryRoot(root, out var fullRoot)) continue;
            AddLibrary(fullRoot);
            var folderFile = Path.Combine(fullRoot, "steamapps", "libraryfolders.vdf");
            if (!File.Exists(folderFile)) continue;
            try
            {
                var folders = ReadVdf(folderFile, cancellationToken).Object("libraryfolders");
                if (folders is null) throw new FormatException("Missing libraryfolders object.");
                foreach (var item in folders.Entries)
                {
                    cancellationToken.ThrowIfCancellationRequested();
                    if (!uint.TryParse(item.Key, NumberStyles.None, CultureInfo.InvariantCulture, out _)) continue;
                    // Older Steam versions used "1" "D:\\SteamLibrary"; newer ones nest path/apps.
                    var library = item.Value.Text ?? item.Value.Object?.Text("path");
                    if (TryRoot(library, out var candidate)) AddLibrary(candidate);
                }
            }
            catch (Exception error) when (IsReadFailure(error))
            {
                warnings.Add($"Could not read Steam libraries at {folderFile}: {error.Message}");
            }
        }

        foreach (var library in libraries)
        {
            cancellationToken.ThrowIfCancellationRequested();
            var steamApps = Path.Combine(library, "steamapps");
            if (!Directory.Exists(steamApps))
            {
                warnings.Add($"Steam library is unavailable: {library}");
                continue;
            }
            try
            {
                var count = 0;
                foreach (var file in Directory.EnumerateFiles(steamApps, "appmanifest_*.acf", SearchOption.TopDirectoryOnly))
                {
                    cancellationToken.ThrowIfCancellationRequested();
                    if (++count > MaximumManifestsPerLibrary)
                    {
                        warnings.Add($"Stopped reading an unusually large Steam library: {library}");
                        break;
                    }
                    try
                    {
                        var game = ReadGame(file, steamApps, cancellationToken);
                        if (game is not null) games.TryAdd(game.AppId, game);
                    }
                    catch (Exception error) when (IsReadFailure(error))
                    {
                        warnings.Add($"Could not read Steam manifest {Path.GetFileName(file)}: {error.Message}");
                    }
                }
            }
            catch (Exception error) when (IsReadFailure(error))
            {
                warnings.Add($"Could not scan Steam library {library}: {error.Message}");
            }
        }

        return new(games.Values.OrderBy(game => game.Name, StringComparer.CurrentCultureIgnoreCase).ToArray(), warnings.ToArray());

        void AddLibrary(string path)
        {
            if (libraries.Count < MaximumLibraries) libraries.Add(path);
            else if (!libraries.Contains(path) && !warnings.Contains("Steam library limit reached."))
                warnings.Add("Steam library limit reached.");
        }
    }

    static InstalledSteamGame? ReadGame(string file, string steamApps, CancellationToken cancellationToken)
    {
        var state = ReadVdf(file, cancellationToken).Object("AppState")
            ?? throw new FormatException("Missing AppState object.");
        var appId = state.Text("appid");
        if (!uint.TryParse(appId, NumberStyles.None, CultureInfo.InvariantCulture, out var number) || number == 0
            || appId != number.ToString(CultureInfo.InvariantCulture)
            || !string.Equals(Path.GetFileName(file), $"appmanifest_{appId}.acf", StringComparison.OrdinalIgnoreCase))
            throw new FormatException("App ID does not match the manifest filename.");
        // FullyInstalled may coexist with UpdateRequired. A manifest for an incomplete download
        // does not mean the game is playable, even if Steam has already created its directory.
        if (!uint.TryParse(state.Text("StateFlags"), NumberStyles.None, CultureInfo.InvariantCulture, out var flags)
            || (flags & 4) == 0) return null;
        var name = state.Text("name");
        var directory = state.Text("installdir");
        if (string.IsNullOrWhiteSpace(name) || name.Length > 512 || name.Any(char.IsControl)
            || string.IsNullOrWhiteSpace(directory) || Path.IsPathRooted(directory))
            throw new FormatException("Invalid game name or installation directory.");
        var common = Path.GetFullPath(Path.Combine(steamApps, "common"));
        var installation = Path.GetFullPath(Path.Combine(common, directory));
        if (!installation.StartsWith(common + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
            throw new FormatException("Installation directory escapes the Steam library.");
        cancellationToken.ThrowIfCancellationRequested();
        if (!Directory.Exists(installation)) return null;
        return new(appId!, name, installation, Path.GetFullPath(file));
    }

    static bool TryRoot(string? root, out string fullPath)
    {
        fullPath = "";
        if (string.IsNullOrWhiteSpace(root) || !Path.IsPathFullyQualified(root)) return false;
        try
        {
            fullPath = Path.TrimEndingDirectorySeparator(Path.GetFullPath(root));
            return true;
        }
        catch (Exception error) when (error is ArgumentException or NotSupportedException or PathTooLongException)
        {
            return false;
        }
    }

    static IEnumerable<string> FindSteamRoots()
    {
        var roots = new List<string>();
        AddRegistry(RegistryHive.CurrentUser, RegistryView.Default, @"Software\Valve\Steam", "SteamPath");
        AddRegistry(RegistryHive.LocalMachine, RegistryView.Registry32, @"Software\Valve\Steam", "InstallPath");
        AddRegistry(RegistryHive.LocalMachine, RegistryView.Registry64, @"Software\Valve\Steam", "InstallPath");
        var fallback = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86), "Steam");
        if (Directory.Exists(fallback)) roots.Add(fallback);
        return roots.Distinct(StringComparer.OrdinalIgnoreCase);

        void AddRegistry(RegistryHive hive, RegistryView view, string path, string valueName)
        {
            try
            {
                using var baseKey = RegistryKey.OpenBaseKey(hive, view);
                using var key = baseKey.OpenSubKey(path, writable: false);
                if (key?.GetValue(valueName) is string value && !string.IsNullOrWhiteSpace(value)) roots.Add(value);
            }
            catch (Exception error) when (error is SecurityException or UnauthorizedAccessException or IOException) { }
        }
    }

    static bool IsReadFailure(Exception error) => error is IOException or UnauthorizedAccessException
        or SecurityException or FormatException or ArgumentException or NotSupportedException;

    static VdfObject ReadVdf(string file, CancellationToken cancellationToken)
    {
        using var stream = new FileStream(file, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
        var initialLength = stream.Length;
        if (initialLength > MaximumFileBytes) throw new FormatException("Steam metadata exceeds the size limit.");
        // Most manifests are only a few KiB. Reserve one extra byte to detect concurrent growth
        // instead of allocating a megabyte for every game or trusting a changing file length.
        var bytes = new byte[checked((int)initialLength + 1)];
        var length = 0;
        while (length < bytes.Length)
        {
            cancellationToken.ThrowIfCancellationRequested();
            var read = stream.Read(bytes, length, bytes.Length - length);
            if (read == 0) break;
            length += read;
        }
        if (length == bytes.Length) throw new IOException("Steam metadata changed during discovery; scan again.");
        var text = new UTF8Encoding(false, true).GetString(bytes, 0, length).TrimStart('\uFEFF');
        return new VdfParser(text, cancellationToken).Parse();
    }

    sealed record VdfValue(string? Text = null, VdfObject? Object = null);
    sealed class VdfObject
    {
        public Dictionary<string, VdfValue> Entries { get; } = new(StringComparer.OrdinalIgnoreCase);
        public string? Text(string key) => Entries.GetValueOrDefault(key)?.Text;
        public VdfObject? Object(string key) => Entries.GetValueOrDefault(key)?.Object;
    }

    /// <summary>Small bounded KeyValues parser, including comments, quoted escapes and bare tokens.</summary>
    sealed class VdfParser(string source, CancellationToken cancellationToken)
    {
        int offset, entries;
        public VdfObject Parse() => ParseObject(0, expectClose: false);

        VdfObject ParseObject(int depth, bool expectClose)
        {
            if (depth > 32) throw new FormatException("Steam metadata is nested too deeply.");
            var result = new VdfObject();
            while (true)
            {
                cancellationToken.ThrowIfCancellationRequested();
                SkipWhitespace();
                if (offset == source.Length)
                {
                    if (expectClose) throw new FormatException("Unclosed Steam metadata object.");
                    return result;
                }
                if (source[offset] == '}')
                {
                    if (!expectClose) throw new FormatException("Unexpected closing brace.");
                    offset++;
                    return result;
                }
                var key = Token();
                SkipWhitespace();
                VdfValue value;
                if (offset < source.Length && source[offset] == '{')
                {
                    offset++;
                    value = new(Object: ParseObject(depth + 1, expectClose: true));
                }
                else value = new(Text: Token());
                if (++entries > 20000) throw new FormatException("Too many Steam metadata entries.");
                if (!result.Entries.TryAdd(key, value)) throw new FormatException($"Duplicate Steam metadata key: {key}");
            }
        }

        string Token()
        {
            SkipWhitespace();
            if (offset == source.Length || source[offset] is '{' or '}') throw new FormatException("Expected a Steam metadata value.");
            if (source[offset] != '"')
            {
                var start = offset;
                while (offset < source.Length && !char.IsWhiteSpace(source[offset]) && source[offset] is not ('{' or '}')) offset++;
                return source[start..offset];
            }
            offset++;
            var result = new StringBuilder();
            while (offset < source.Length)
            {
                var character = source[offset++];
                if (character == '"') return result.ToString();
                if (character == '\\' && offset < source.Length)
                {
                    var next = source[offset];
                    // Preserve unknown escapes: Windows paths can contain single backslashes.
                    if (next is '\\' or '"') { result.Append(next); offset++; continue; }
                }
                result.Append(character);
            }
            throw new FormatException("Unclosed quoted Steam metadata value.");
        }

        void SkipWhitespace()
        {
            while (offset < source.Length)
            {
                if (char.IsWhiteSpace(source[offset])) { offset++; continue; }
                if (source[offset] == '/' && offset + 1 < source.Length && source[offset + 1] == '/')
                {
                    offset += 2;
                    while (offset < source.Length && source[offset] is not ('\r' or '\n')) offset++;
                    continue;
                }
                break;
            }
        }
    }
}
