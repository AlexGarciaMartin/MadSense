using System.IO.Compression;
using System.Net.Http;
using System.Security.Cryptography;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace MadSense.Games;

/// <summary>One installation MadSense made, with every file it wrote so removal touches nothing else.</summary>
sealed class InstalledMod
{
    [JsonPropertyName("id")] public string Id { get; set; } = "";
    [JsonPropertyName("gameKey")] public string GameKey { get; set; } = "";
    [JsonPropertyName("version")] public string Version { get; set; } = "";
    [JsonPropertyName("sha256")] public string Sha256 { get; set; } = "";
    [JsonPropertyName("installed")] public DateTimeOffset Installed { get; set; }
    [JsonPropertyName("root")] public string Root { get; set; } = "";
    [JsonPropertyName("files")] public List<string> Files { get; set; } = [];
    [JsonPropertyName("exe")] public string? Exe { get; set; }
    /// <summary>The game folder at install time, so the runner can follow the game without a library scan.</summary>
    [JsonPropertyName("gameDir")] public string? GameDir { get; set; }
}

sealed record InstallProgress(string Stage, long Done, long Total);

/// <summary>
/// Installs catalog mods from the author's GitHub release: download to a temporary file, verify
/// size and SHA-256 (the pinned one, or the digest computed now, recorded for later), extract safely
/// (no path may leave the destination), and record every file written. Helpers live under
/// MadSense's own mods folder; game-folder mods go next to the game and never overwrite a file the
/// game already has; BeamNG mods are dropped unextracted into the player's mods folder. Removal
/// deletes only recorded files whose content is still what MadSense wrote.
/// </summary>
sealed class ModInstaller
{
    const long MaximumPackageBytes = 256L * 1024 * 1024;
    static readonly JsonSerializerOptions Json = new() { WriteIndented = true };
    readonly string modsRoot;
    readonly string registryPath;
    HttpClient? http;
    readonly object gate = new();
    List<InstalledMod> installed = [];

    public ModInstaller(string? modsRoot = null, HttpClient? http = null)
    {
        this.modsRoot = modsRoot ?? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "MadSense", "mods");
        registryPath = Path.Combine(this.modsRoot, "installed.json");
        this.http = http;
        Load();
    }

    public string ModsRoot => modsRoot;

    /// <summary>Raised after the registry changes (install, removal).</summary>
    public event Action? Changed;

    /// <summary>One small file read: whether a helper is recorded under <paramref name="modsRoot"/>.</summary>
    public static bool AnyHelperInstalled(string modsRoot)
    {
        var path = Path.Combine(modsRoot, "installed.json");
        try { return File.Exists(path) && (JsonSerializer.Deserialize<List<InstalledMod>>(File.ReadAllText(path), Json)?.Any(m => m.Exe != null) ?? false); }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException or JsonException) { return false; }
    }

    /// <summary>Created on the first download, with the install user agent; tests hand in their own.</summary>
    HttpClient Http
    {
        get
        {
            if (http is null)
            {
                http = new HttpClient(new SocketsHttpHandler { AllowAutoRedirect = true, MaxAutomaticRedirections = 5 }) { Timeout = TimeSpan.FromMinutes(10) };
                http.DefaultRequestHeaders.UserAgent.ParseAdd("MadSense/0.5 (+https://madsense.pro; mod-install)");
            }
            return http;
        }
    }

    public IReadOnlyList<InstalledMod> Installed { get { lock (gate) return installed.ToList(); } }

    public InstalledMod? Find(string modId, string gameKey)
    {
        lock (gate) return installed.FirstOrDefault(m => m.Id == modId && m.GameKey == gameKey);
    }

    public bool IsInstalled(string modId, string gameKey) => Find(modId, gameKey) is not null;

    /// <summary>Downloads, verifies and installs <paramref name="mod"/> for <paramref name="game"/>; throws with a readable message on any failure.</summary>
    public async Task<InstalledMod> InstallAsync(ModEntry mod, LibraryGame game, IProgress<InstallProgress>? progress, CancellationToken cancellationToken)
    {
        if (!mod.Installable || mod.Package is null) throw new InvalidOperationException(L.Text("Games.NotInstallable"));
        if (mod.Package.Size > MaximumPackageBytes) throw new InvalidOperationException(L.Text("Games.PackageTooLarge"));
        Directory.CreateDirectory(modsRoot);
        var temp = Path.Combine(modsRoot, $"{mod.Id}.download");
        try
        {
            var sha256 = await DownloadAsync(mod.Package, temp, progress, cancellationToken).ConfigureAwait(false);
            progress?.Report(new InstallProgress("install", 0, 1));
            var record = mod.Kind switch
            {
                ModKind.Helper => InstallHelper(mod, game, temp, sha256),
                ModKind.GameFolder => InstallIntoGameFolder(mod, game, temp, sha256),
                ModKind.BeamNG => InstallBeamNg(mod, game, temp, sha256),
                _ => throw new InvalidOperationException(L.Text("Games.NotInstallable")),
            };
            lock (gate)
            {
                installed.RemoveAll(m => m.Id == mod.Id && m.GameKey == game.Key);
                installed.Add(record);
                Save();
            }
            Changed?.Invoke();
            progress?.Report(new InstallProgress("done", 1, 1));
            return record;
        }
        finally
        {
            TryDelete(temp);
        }
    }

    /// <summary>Deletes a file we just wrote; an antivirus scan can hold it for a moment, so retry briefly.</summary>
    internal static void TryDelete(string path)
    {
        for (int attempt = 0; attempt < 10; attempt++)
        {
            try { if (File.Exists(path)) File.Delete(path); return; }
            catch (Exception e) when (e is IOException or UnauthorizedAccessException) { Thread.Sleep(100); }
        }
    }

    /// <summary>Removes what MadSense wrote for this mod and game. Files changed since are left alone.</summary>
    public void Uninstall(string modId, string gameKey)
    {
        InstalledMod? record;
        lock (gate) record = installed.FirstOrDefault(m => m.Id == modId && m.GameKey == gameKey);
        if (record is null) return;
        foreach (var file in record.Files.OrderByDescending(f => f.Length))
        {
            var path = Path.Combine(record.Root, file);
            if (!IsInside(path, record.Root)) continue;
            try
            {
                if (File.Exists(path)) File.Delete(path);
                var directory = Path.GetDirectoryName(path);
                while (directory is not null && IsInside(directory, record.Root) && !string.Equals(Path.GetFullPath(directory), Path.GetFullPath(record.Root), StringComparison.OrdinalIgnoreCase)
                       && Directory.Exists(directory) && !Directory.EnumerateFileSystemEntries(directory).Any())
                {
                    Directory.Delete(directory);
                    directory = Path.GetDirectoryName(directory);
                }
            }
            catch (Exception e) when (e is IOException or UnauthorizedAccessException) { }
        }
        if (record.Root.StartsWith(modsRoot, StringComparison.OrdinalIgnoreCase))
        {
            try { if (Directory.Exists(record.Root)) Directory.Delete(record.Root, true); } catch (Exception e) when (e is IOException or UnauthorizedAccessException) { }
        }
        lock (gate) { installed.Remove(record); Save(); }
        Changed?.Invoke();
    }

    async Task<string> DownloadAsync(ModPackage package, string target, IProgress<InstallProgress>? progress, CancellationToken cancellationToken)
    {
        using var response = await Http.GetAsync(package.Url, HttpCompletionOption.ResponseHeadersRead, cancellationToken).ConfigureAwait(false);
        if (!response.IsSuccessStatusCode) throw new InvalidOperationException(L.Text("Games.DownloadFailed", (int)response.StatusCode));
        var expected = response.Content.Headers.ContentLength ?? package.Size;
        if (expected > MaximumPackageBytes) throw new InvalidOperationException(L.Text("Games.PackageTooLarge"));
        await using var input = await response.Content.ReadAsStreamAsync(cancellationToken).ConfigureAwait(false);
        await using var output = new FileStream(target, FileMode.Create, FileAccess.Write, FileShare.None, 64 * 1024, true);
        using var hash = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
        var buffer = new byte[64 * 1024];
        long total = 0;
        while (true)
        {
            var read = await input.ReadAsync(buffer, cancellationToken).ConfigureAwait(false);
            if (read == 0) break;
            total += read;
            if (total > MaximumPackageBytes) throw new InvalidOperationException(L.Text("Games.PackageTooLarge"));
            await output.WriteAsync(buffer.AsMemory(0, read), cancellationToken).ConfigureAwait(false);
            hash.AppendData(buffer, 0, read);
            progress?.Report(new InstallProgress("download", total, expected));
        }
        var digest = Convert.ToHexString(hash.GetHashAndReset()).ToLowerInvariant();
        if (package.Size > 0 && total != package.Size) throw new InvalidOperationException(L.Text("Games.SizeMismatch"));
        if (!string.IsNullOrEmpty(package.Sha256) && !digest.Equals(package.Sha256, StringComparison.OrdinalIgnoreCase)) throw new InvalidOperationException(L.Text("Games.HashMismatch"));
        return digest;
    }

    InstalledMod InstallHelper(ModEntry mod, LibraryGame game, string package, string sha256)
    {
        var root = Path.Combine(modsRoot, mod.Id);
        if (Directory.Exists(root)) Directory.Delete(root, true);
        Directory.CreateDirectory(root);
        var files = mod.Package!.Type == "zip" ? Extract(package, root, overwrite: true) : [CopyFile(package, root, mod.Run?.Exe ?? Path.GetFileName(mod.Package.Url))];
        var exe = files.FirstOrDefault(f => string.Equals(Path.GetFileName(f), mod.Run?.Exe, StringComparison.OrdinalIgnoreCase))
                  ?? files.Where(f => f.EndsWith(".exe", StringComparison.OrdinalIgnoreCase)).OrderBy(f => f.Count(c => c == '\\')).FirstOrDefault()
                  ?? throw new InvalidOperationException(L.Text("Games.NoExecutable"));
        return Record(mod, game, root, files, sha256, exe);
    }

    InstalledMod InstallIntoGameFolder(ModEntry mod, LibraryGame game, string package, string sha256)
    {
        if (!Directory.Exists(game.InstallDir)) throw new InvalidOperationException(L.Text("Games.GameFolderMissing"));
        // Never replace a file the game ships: a mod that needs to would need a review first.
        var files = Extract(package, game.InstallDir, overwrite: false);
        return Record(mod, game, game.InstallDir, files, sha256, null);
    }

    InstalledMod InstallBeamNg(ModEntry mod, LibraryGame game, string package, string sha256)
    {
        var root = BeamNgModsFolder() ?? throw new InvalidOperationException(L.Text("Games.BeamNgFolderMissing"));
        Directory.CreateDirectory(root);
        var name = Path.GetFileName(new Uri(mod.Package!.Url).AbsolutePath);
        return Record(mod, game, root, [CopyFile(package, root, name)], sha256, null);
    }

    /// <summary>BeamNG keeps mods under its user folder, one subfolder per game version; the newest wins.</summary>
    internal static string? BeamNgModsFolder()
    {
        var user = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "BeamNG.drive");
        if (!Directory.Exists(user)) return null;
        var current = Path.Combine(user, "current", "mods");
        if (Directory.Exists(Path.Combine(user, "current"))) return current;
        var versions = Directory.EnumerateDirectories(user).Where(d => Version.TryParse(Path.GetFileName(d), out _)).OrderByDescending(d => Version.Parse(Path.GetFileName(d))).ToList();
        return versions.Count == 0 ? null : Path.Combine(versions[0], "mods");
    }

    static InstalledMod Record(ModEntry mod, LibraryGame game, string root, List<string> files, string sha256, string? exe) => new()
    {
        Id = mod.Id, GameKey = game.Key, Version = mod.Package!.Version, Sha256 = sha256, Installed = DateTimeOffset.Now,
        Root = root, Files = files, Exe = exe, GameDir = game.InstallDir,
    };

    static string CopyFile(string source, string root, string name)
    {
        var safe = Path.GetFileName(name);
        File.Copy(source, Path.Combine(root, safe), true);
        return safe;
    }

    /// <summary>Extracts the zip into <paramref name="root"/>; returns the relative files written. Entries that would land outside the root, or over an existing file when overwriting is off, are refused.</summary>
    internal static List<string> Extract(string package, string root, bool overwrite)
    {
        var written = new List<string>();
        var fullRoot = Path.GetFullPath(root).TrimEnd('\\') + "\\";
        using var archive = ZipFile.OpenRead(package);
        long totalBytes = 0;
        foreach (var entry in archive.Entries)
        {
            if (entry.FullName.EndsWith('/') || entry.FullName.EndsWith('\\')) continue;
            totalBytes += entry.Length;
            if (totalBytes > MaximumPackageBytes * 4) throw new InvalidOperationException(L.Text("Games.PackageTooLarge"));
            var relative = entry.FullName.Replace('/', '\\').TrimStart('\\');
            var destination = Path.GetFullPath(Path.Combine(fullRoot, relative));
            if (!destination.StartsWith(fullRoot, StringComparison.OrdinalIgnoreCase)) throw new InvalidOperationException(L.Text("Games.UnsafePackage"));
            if (!overwrite && File.Exists(destination)) throw new InvalidOperationException(L.Text("Games.WouldOverwrite", relative));
            Directory.CreateDirectory(Path.GetDirectoryName(destination)!);
            entry.ExtractToFile(destination, overwrite);
            written.Add(destination[fullRoot.Length..]);
        }
        if (written.Count == 0) throw new InvalidOperationException(L.Text("Games.EmptyPackage"));
        return written;
    }

    static bool IsInside(string path, string root)
    {
        var fullRoot = Path.GetFullPath(root).TrimEnd('\\') + "\\";
        return Path.GetFullPath(path).StartsWith(fullRoot, StringComparison.OrdinalIgnoreCase);
    }

    void Load()
    {
        try
        {
            if (File.Exists(registryPath))
                installed = JsonSerializer.Deserialize<List<InstalledMod>>(File.ReadAllText(registryPath), Json) ?? [];
        }
        catch (Exception e) when (e is IOException or JsonException or UnauthorizedAccessException) { installed = []; }
    }

    void Save()
    {
        Directory.CreateDirectory(modsRoot);
        File.WriteAllText(registryPath + ".tmp", JsonSerializer.Serialize(installed, Json));
        File.Move(registryPath + ".tmp", registryPath, true);
    }
}
