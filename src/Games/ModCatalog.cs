using System.Reflection;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace MadSense.Games;

/// <summary>How MadSense can install a catalog entry.</summary>
enum ModKind
{
    /// <summary>A standalone program run beside the game; MadSense keeps it in its own mods folder and starts it with the game.</summary>
    Helper,
    /// <summary>Files copied into the game's install folder (recorded, so they can be removed).</summary>
    GameFolder,
    /// <summary>A BeamNG.drive mod zip dropped into the player's BeamNG mods folder.</summary>
    BeamNG,
    /// <summary>Hosted where MadSense cannot download (a login) or needing a loader MadSense does not manage: page and steps only.</summary>
    Manual,
}

sealed class ModGame
{
    [JsonPropertyName("name")] public string Name { get; set; } = "";
    [JsonPropertyName("steam")] public string[] Steam { get; set; } = [];
    [JsonPropertyName("match")] public string[] Match { get; set; } = [];
    [JsonPropertyName("exclude")] public string[] Exclude { get; set; } = [];
    [JsonPropertyName("setup")] public Dictionary<string, string> Setup { get; set; } = new();
}

sealed class ModPackage
{
    [JsonPropertyName("version")] public string Version { get; set; } = "";
    [JsonPropertyName("url")] public string Url { get; set; } = "";
    [JsonPropertyName("size")] public long Size { get; set; }
    [JsonPropertyName("sha256")] public string? Sha256 { get; set; }
    [JsonPropertyName("type")] public string Type { get; set; } = "zip";
}

sealed class ModRun
{
    [JsonPropertyName("exe")] public string Exe { get; set; } = "";
    [JsonPropertyName("args")] public string? Args { get; set; }
    [JsonPropertyName("window")] public string Window { get; set; } = "minimized";
}

sealed class ModEntry
{
    [JsonPropertyName("id")] public string Id { get; set; } = "";
    [JsonPropertyName("name")] public string Name { get; set; } = "";
    [JsonPropertyName("author")] public string Author { get; set; } = "";
    [JsonPropertyName("license")] public string? License { get; set; }
    [JsonPropertyName("page")] public string Page { get; set; } = "";
    [JsonPropertyName("games")] public ModGame[] Games { get; set; } = [];
    [JsonPropertyName("features")] public string[] Features { get; set; } = [];
    [JsonPropertyName("kind")] public string KindName { get; set; } = "manual";
    [JsonPropertyName("package")] public ModPackage? Package { get; set; }
    [JsonPropertyName("run")] public ModRun? Run { get; set; }
    [JsonPropertyName("requires")] public string[] Requires { get; set; } = [];
    [JsonPropertyName("notes")] public Dictionary<string, string> Notes { get; set; } = new();
    [JsonPropertyName("setup")] public Dictionary<string, string> Setup { get; set; } = new();
    [JsonPropertyName("beforeInstall")] public Dictionary<string, string> BeforeInstall { get; set; } = new();
    [JsonPropertyName("available")] public bool Available { get; set; } = true;

    static string Localized(Dictionary<string, string> values, string language) => values.GetValueOrDefault(language) ?? values.GetValueOrDefault("en") ?? values.Values.FirstOrDefault() ?? "";
    public string Preparation(LibraryGame game, string language)
    {
        var match = Games.Where(g => game.SteamAppId is { } id ? g.Steam.Contains(id) : g.Match.Any(m => PcGamingWikiClient.Normalize(game.Name).Contains(PcGamingWikiClient.Normalize(m))))
            .OrderByDescending(g => g.Name.Length).FirstOrDefault();
        return Localized(match?.Setup.Count > 0 ? match.Setup : Setup, language);
    }
    public string InstallNotice(string language) => Localized(BeforeInstall, language);

    [JsonIgnore] public ModKind Kind => KindName.ToLowerInvariant() switch { "helper" => ModKind.Helper, "gamefolder" => ModKind.GameFolder, "beamng" => ModKind.BeamNG, _ => ModKind.Manual };
    [JsonIgnore] public bool Installable => Kind != ModKind.Manual && Package is { Url.Length: > 0 } && Uri.TryCreate(Package.Url, UriKind.Absolute, out var uri) && uri.Scheme == Uri.UriSchemeHttps && uri.Host.Equals("github.com", StringComparison.OrdinalIgnoreCase);
    [JsonIgnore] public Uri PageUri => new(Page);

    /// <summary>The note in the UI language, English otherwise, Spanish as the last resort.</summary>
    public string Note(string language) => Notes.TryGetValue(language, out var text) ? text : Notes.TryGetValue("en", out var english) ? english : Notes.Values.FirstOrDefault() ?? "";

    public bool Matches(LibraryGame game)
    {
        var normalized = PcGamingWikiClient.Normalize(game.Name);
        foreach (var entry in Games)
        {
            if (entry.Exclude.Any(x => normalized.Contains(PcGamingWikiClient.Normalize(x)))) continue;
            if (game.SteamAppId is { } id && entry.Steam.Contains(id)) return true;
            if (game.SteamAppId is null && entry.Match.Any(m => normalized.Contains(PcGamingWikiClient.Normalize(m)))) return true;
        }
        return false;
    }
}

sealed class ModCatalogDocument
{
    [JsonPropertyName("schemaVersion")] public int SchemaVersion { get; set; }
    [JsonPropertyName("catalogVersion")] public string CatalogVersion { get; set; } = "";
    [JsonPropertyName("notice")] public string Notice { get; set; } = "";
    [JsonPropertyName("mods")] public ModEntry[] Mods { get; set; } = [];
}

/// <summary>
/// The MadSense catalog of community DualSense mods: which games have one, who made it, where it
/// lives and whether MadSense can install it. Shipped inside the app (so the list works offline)
/// and read once; a mod listed here is a documented community mod, not a MadSense certification.
/// </summary>
static class ModCatalog
{
    static readonly Lazy<ModCatalogDocument> document = new(Load);

    public static ModCatalogDocument Document => document.Value;
    public static IReadOnlyList<ModEntry> Mods => document.Value.Mods;
    public static string Version => document.Value.CatalogVersion;

    public static IReadOnlyList<ModEntry> ForGame(LibraryGame game) => Mods.Where(m => m.Available && m.Matches(game)).OrderBy(m => m.Kind).ToList();

    public static ModEntry? ById(string id) => Mods.FirstOrDefault(m => string.Equals(m.Id, id, StringComparison.OrdinalIgnoreCase));

    static ModCatalogDocument Load()
    {
        var assembly = typeof(ModCatalog).Assembly;
        using var stream = assembly.GetManifestResourceStream("MadSense.Games.catalog.json") ?? throw new InvalidOperationException("The mod catalog is missing from the build.");
        return Parse(stream);
    }

    internal static ModCatalogDocument Parse(Stream stream)
    {
        var parsed = JsonSerializer.Deserialize<ModCatalogDocument>(stream) ?? throw new FormatException("Empty mod catalog.");
        if (parsed.SchemaVersion != 1) throw new FormatException($"Unsupported catalog schema {parsed.SchemaVersion}.");
        foreach (var mod in parsed.Mods)
        {
            if (mod.Id.Length == 0 || mod.Name.Length == 0 || mod.Games.Length == 0 || !Uri.TryCreate(mod.Page, UriKind.Absolute, out var page) || page.Scheme != Uri.UriSchemeHttps)
                throw new FormatException($"Invalid catalog entry '{mod.Id}'.");
            if (mod.Kind != ModKind.Manual && (mod.Package is null || !mod.Installable)) throw new FormatException($"Catalog entry '{mod.Id}' is installable but has no GitHub package.");
            if (mod.Kind == ModKind.Helper && string.IsNullOrWhiteSpace(mod.Run?.Exe)) throw new FormatException($"Helper '{mod.Id}' names no executable.");
        }
        return parsed;
    }
}
