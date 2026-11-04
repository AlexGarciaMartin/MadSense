namespace MadSense.Games;

/// <summary>
/// The games feature behind the window: the installed library, PCGamingWiki answers (cached on
/// disk), the mod installer and the runner that starts helpers with their games. One instance per
/// app, created when the games screen opens, or at start-up only when a helper is installed so it
/// follows its game. The wiki client, its cache and the HTTP clients exist only once something
/// asks for them, so the service costs one small file read until the screen is used.
/// </summary>
sealed class GamesService : IDisposable
{
    readonly object gate = new();
    readonly Lazy<PcGamingWikiClient> wiki;
    readonly string foldersPath;
    readonly ModPreferences preferences;
    List<string>? folders;
    IReadOnlyList<LibraryGame> library = [];
    Task<IReadOnlyList<LibraryGame>>? scan;

    public GamesService(string? dataRoot = null, bool watchHelpers = true)
    {
        DataRoot = dataRoot ?? DefaultDataRoot;
        wiki = new(() => new PcGamingWikiClient(Path.Combine(DataRoot, "cache", "pcgw.json")));
        foldersPath = Path.Combine(DataRoot, "games-folders.json");
        preferences = new ModPreferences(Path.Combine(DataRoot, "mod-preferences.json"));
        Installer = new ModInstaller(Path.Combine(DataRoot, "mods"));
        Runner = new ModRunner(Installer, () => Library, watch: watchHelpers, autoStartEnabled: (mod, game) =>
            ModCatalog.ById(mod)?.Available == true && preferences.IsSetupComplete(mod, game) && preferences.IsAutoStartEnabled(mod, game));
    }

    public static string DefaultDataRoot => Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "MadSense");

    /// <summary>Whether a helper is installed: the one reason to create the service before the screen opens.</summary>
    public static bool HelpersInstalled(string? dataRoot = null) => ModInstaller.AnyHelperInstalled(Path.Combine(dataRoot ?? DefaultDataRoot, "mods"));

    public string DataRoot { get; }
    public PcGamingWikiClient Wiki => wiki.Value;
    public ModInstaller Installer { get; }
    public ModRunner Runner { get; }
    public IReadOnlyList<LibraryGame> Library { get { lock (gate) return library; } }
    public DateTimeOffset? Scanned { get; private set; }
    public event Action? LibraryChanged;

    public bool IsAutoStartEnabled(string modId, string gameKey) => preferences.IsAutoStartEnabled(modId, gameKey);
    public void SetAutoStartEnabled(string modId, string gameKey, bool enabled)
    {
        preferences.SetAutoStartEnabled(modId, gameKey, enabled);
        Runner.RefreshPreferences();
    }
    public bool IsSetupComplete(string modId, string gameKey) => preferences.IsSetupComplete(modId, gameKey);
    public void SetSetupComplete(string modId, string gameKey, bool complete)
    {
        preferences.SetSetupComplete(modId, gameKey, complete);
        Runner.RefreshPreferences();
    }

    /// <summary>Folders the user added by hand; read with every scan, kept in a small JSON file.</summary>
    public IReadOnlyList<string> CustomFolders { get { lock (gate) return Folders().ToList(); } }

    public bool IsCustomFolder(string path) { lock (gate) return Folders().Any(f => Same(f, path)); }

    /// <summary>Remembers <paramref name="path"/>; false when it was already there.</summary>
    public bool AddFolder(string path)
    {
        var full = Path.GetFullPath(path).TrimEnd('\\');
        lock (gate)
        {
            var list = Folders();
            if (list.Any(f => Same(f, full))) return false;
            list.Add(full); SaveFolders(list);
        }
        return true;
    }

    public bool RemoveFolder(string path)
    {
        lock (gate)
        {
            var list = Folders();
            int removed = list.RemoveAll(f => Same(f, path));
            if (removed > 0) SaveFolders(list);
            return removed > 0;
        }
    }

    static bool Same(string a, string b) => string.Equals(a.TrimEnd('\\'), b.TrimEnd('\\'), StringComparison.OrdinalIgnoreCase);

    List<string> Folders()
    {
        if (folders is not null) return folders;
        try { folders = File.Exists(foldersPath) ? System.Text.Json.JsonSerializer.Deserialize<List<string>>(File.ReadAllText(foldersPath)) ?? [] : []; }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException or System.Text.Json.JsonException) { folders = []; }
        return folders;
    }

    void SaveFolders(List<string> list)
    {
        try { Directory.CreateDirectory(DataRoot); File.WriteAllText(foldersPath, System.Text.Json.JsonSerializer.Serialize(list)); }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException) { }
    }

    /// <summary>Reads every launcher's records; concurrent callers share one scan.</summary>
    public Task<IReadOnlyList<LibraryGame>> ScanAsync(CancellationToken cancellationToken = default)
    {
        lock (gate)
        {
            if (scan is { IsCompleted: false }) return scan;
            scan = Task.Run(() =>
            {
                var games = GameLibrary.Scan(cancellationToken, CustomFolders);
                lock (gate) { library = games; Scanned = DateTimeOffset.Now; }
                LibraryChanged?.Invoke();
                return games;
            }, cancellationToken);
            return scan;
        }
    }

    /// <summary>A game's current standing for the list and the detail pane.</summary>
    public GameStanding Standing(LibraryGame game, WikiSupport? wiki)
    {
        var mods = ModCatalog.Mods.Where(m => (m.Available && m.Matches(game)) || Installer.IsInstalled(m.Id, game.Key)).OrderBy(m => m.Kind).ToList();
        var installed = mods.Where(m => Installer.IsInstalled(m.Id, game.Key)).ToList();
        if (installed.Count > 0) return new GameStanding(GameStatus.ModInstalled, wiki, mods, installed);
        // A game that drives the pad itself needs no mod, but one that goes further is still worth listing.
        if (wiki?.NativeDualSense == true) return new GameStanding(GameStatus.Native, wiki, mods, installed);
        if (mods.Any(m => m.Installable)) return new GameStanding(GameStatus.ModAvailable, wiki, mods, installed);
        if (mods.Count > 0) return new GameStanding(GameStatus.ModManual, wiki, mods, installed);
        if (wiki is null) return new GameStanding(GameStatus.Unknown, null, mods, installed);
        if (wiki.Controller is WikiValue.Yes or WikiValue.Limited) return new GameStanding(GameStatus.PlayStationOnly, wiki, mods, installed);
        if (wiki.Controller == WikiValue.No || wiki.AnyController == WikiValue.No) return new GameStanding(GameStatus.None, wiki, mods, installed);
        return new GameStanding(GameStatus.Unknown, wiki, mods, installed);
    }

    public void Dispose()
    {
        Runner.Dispose();
        if (wiki.IsValueCreated) wiki.Value.Dispose();
    }
}

enum GameStatus { Unknown, Native, ModInstalled, ModAvailable, ModManual, PlayStationOnly, None }

sealed record GameStanding(GameStatus Status, WikiSupport? Wiki, IReadOnlyList<ModEntry> Mods, IReadOnlyList<ModEntry> Installed);
