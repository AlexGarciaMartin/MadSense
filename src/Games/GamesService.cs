namespace MadSense.Games;

/// <summary>
/// The games feature behind the window: the installed library, PCGamingWiki answers (cached on
/// disk), the mod installer and the runner that starts helpers with their games. One instance per
/// app; the runner keeps working while the window is closed so installed helpers follow the game.
/// </summary>
sealed class GamesService : IDisposable
{
    readonly object gate = new();
    IReadOnlyList<LibraryGame> library = [];
    Task<IReadOnlyList<LibraryGame>>? scan;

    public GamesService(string? dataRoot = null)
    {
        DataRoot = dataRoot ?? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "MadSense");
        Wiki = new PcGamingWikiClient(Path.Combine(DataRoot, "cache", "pcgw.json"));
        Installer = new ModInstaller(Path.Combine(DataRoot, "mods"));
        Runner = new ModRunner(Installer, () => Library);
    }

    public string DataRoot { get; }
    public PcGamingWikiClient Wiki { get; }
    public ModInstaller Installer { get; }
    public ModRunner Runner { get; }
    public IReadOnlyList<LibraryGame> Library { get { lock (gate) return library; } }
    public DateTimeOffset? Scanned { get; private set; }
    public event Action? LibraryChanged;

    /// <summary>Reads every launcher's records; concurrent callers share one scan.</summary>
    public Task<IReadOnlyList<LibraryGame>> ScanAsync(CancellationToken cancellationToken = default)
    {
        lock (gate)
        {
            if (scan is { IsCompleted: false }) return scan;
            scan = Task.Run(() =>
            {
                var games = GameLibrary.Scan(cancellationToken);
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
        var mods = ModCatalog.ForGame(game);
        var installed = mods.Where(m => Installer.IsInstalled(m.Id, game.Key)).ToList();
        if (installed.Count > 0) return new GameStanding(GameStatus.ModInstalled, wiki, mods, installed);
        // A game that drives the pad itself needs no mod: only one MadSense can install is still worth listing.
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
        Wiki.Dispose();
    }
}

enum GameStatus { Unknown, Native, ModInstalled, ModAvailable, ModManual, PlayStationOnly, None }

sealed record GameStanding(GameStatus Status, WikiSupport? Wiki, IReadOnlyList<ModEntry> Mods, IReadOnlyList<ModEntry> Installed);
