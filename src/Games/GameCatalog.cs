namespace MadSense.Games;

// Editorial metadata for the preview. Discovery and PCGamingWiki may add information at runtime;
// none of these entries certifies compatibility with MadSense or contains an installation recipe.
internal sealed record ModReference(string Name, string Games, string[] SteamIds, string Author, Uri Page, string DescriptionKey);
internal sealed record NativeReference(string AppId, Uri Source);

internal static class GameCatalog
{
    // ACF manifests also describe software. Keep known utilities out of the game view;
    // comprehensive classification requires Steam app metadata in a later iteration.
    public static readonly HashSet<string> KnownUtilities = ["228980", "1812620"];
    public static readonly ModReference[] Mods =
    [
        new("WF2_DSX", "Wreckfest 2", ["1203190"], "SYSTEMATI0N", new("https://github.com/SYSTEMATI0N/WF2_DSX"), "Games.WreckfestDescription"),
        new("RacingDSX", "Forza Horizon 5 / DiRT Rally 2.0", ["1551360", "690790"], "cosmii02", new("https://github.com/cosmii02/RacingDSX"), "Games.RacingDescription"),
        new("DualSense4Rockstar", "GTA V Legacy / Red Dead Redemption 2", ["271590", "1174180"], "tommargar", new("https://github.com/tommargar/DualSense4Rockstar"), "Games.RockstarDescription")
    ];

    // Publisher sources confirm haptics and adaptive triggers only; no inference about other features.
    public static readonly NativeReference[] Native =
    [
        new("3489700", new("https://blog.playstation.com/2025/05/15/stellar-blade-launches-june-11-on-pc-specs-and-features-revealed/")),
        new("1649240", new("https://www.playstation.com/en-us/games/returnal/pc/")),
        new("1895880", new("https://www.nixxes.com/project/ratchet-clank-rift-apart/"))
    ];

    public static bool SamePage(Uri a, Uri b) => string.Equals(a.GetLeftPart(UriPartial.Path).TrimEnd('/'), b.GetLeftPart(UriPartial.Path).TrimEnd('/'), StringComparison.OrdinalIgnoreCase);
}
