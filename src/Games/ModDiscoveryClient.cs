using System.Net;
using System.Net.Http;
using System.Text;
using System.Text.RegularExpressions;

namespace MadSense.Games;

internal sealed record DiscoveredMod(string Name, Uri Page, string SourceName);

/// <summary>
/// Imports links from two maintained, public mod lists. An entry is a discovery lead,
/// not a verified MadSense integration or permission to install anything.
/// </summary>
internal sealed class ModDiscoveryClient : IDisposable
{
    internal const int MaximumResponseBytes = 512 * 1024;
    const int MaximumEntriesPerSource = 250;
    static readonly Uri PaliverseReadme = new("https://raw.githubusercontent.com/Paliverse/DualSenseX/main/README.md");
    static readonly Uri DszReadme = new("https://raw.githubusercontent.com/MaximumFreak/dsz/main/README.md");
    static readonly HashSet<string> PageHosts = new(StringComparer.OrdinalIgnoreCase)
    {
        "github.com", "www.nexusmods.com", "nexusmods.com", "steamcommunity.com",
        "www.racedepartment.com", "racedepartment.com",
    };
    static readonly Regex Heading = new(@"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$", RegexOptions.CultureInvariant);
    static readonly Regex Link = new(@"^\s*(?:[-*]\s+)?\[(?<name>[^\]\r\n]{1,180})\]\((?<url>[^)\s]+)\)\s*$", RegexOptions.CultureInvariant);
    readonly HttpClient http;
    readonly bool ownsHttp;

    public ModDiscoveryClient() : this(new HttpClient(new SocketsHttpHandler
    {
        AllowAutoRedirect = false,
        AutomaticDecompression = DecompressionMethods.GZip | DecompressionMethods.Deflate | DecompressionMethods.Brotli,
    }), true) { }

    internal ModDiscoveryClient(HttpClient http) : this(http, false) { }

    ModDiscoveryClient(HttpClient http, bool ownsHttp)
    {
        this.http = http ?? throw new ArgumentNullException(nameof(http));
        this.ownsHttp = ownsHttp;
    }

    // Called by the optional mods panel on opening or manual refresh, never by a timer.
    // null means neither list was readable; an empty list means a valid, empty section.
    public async Task<IReadOnlyList<DiscoveredMod>?> GetAsync(CancellationToken cancellationToken = default)
    {
        cancellationToken.ThrowIfCancellationRequested();
        var sources = await Task.WhenAll(
            ReadAsync(PaliverseReadme, ParsePaliverse, cancellationToken),
            ReadAsync(DszReadme, ParseDsz, cancellationToken)).ConfigureAwait(false);
        if (sources.All(source => source is null)) return null;
        var unique = new Dictionary<string, DiscoveredMod>(StringComparer.OrdinalIgnoreCase);
        foreach (var source in sources)
            if (source is not null)
                foreach (var mod in source)
                    unique.TryAdd(mod.Page.AbsoluteUri, mod);
        return unique.Values.ToArray();
    }

    async Task<IReadOnlyList<DiscoveredMod>?> ReadAsync(Uri uri,
        Func<string, IReadOnlyList<DiscoveredMod>?> parse, CancellationToken cancellationToken)
    {
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        timeout.CancelAfter(TimeSpan.FromSeconds(10));
        try
        {
            using var request = new HttpRequestMessage(HttpMethod.Get, uri);
            request.Headers.UserAgent.ParseAdd("MadSense/0.4.6 (+https://github.com/AlexGarciaMartin/MadSense; mod-catalog-discovery)");
            using var response = await http.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, timeout.Token).ConfigureAwait(false);
            if (!response.IsSuccessStatusCode || response.Content.Headers.ContentLength > MaximumResponseBytes) return null;
            // Reject an HTML error/login document even if its HTTP status is 200.
            if (response.Content.Headers.ContentType?.MediaType?.Equals("text/html", StringComparison.OrdinalIgnoreCase) == true) return null;
            await using var stream = await response.Content.ReadAsStreamAsync(timeout.Token).ConfigureAwait(false);
            using var body = new MemoryStream();
            var buffer = new byte[8192];
            while (true)
            {
                int read = await stream.ReadAsync(buffer, timeout.Token).ConfigureAwait(false);
                if (read == 0) break;
                if (body.Length + read > MaximumResponseBytes) return null;
                body.Write(buffer, 0, read);
            }
            return parse(new UTF8Encoding(false, true).GetString(body.GetBuffer(), 0, (int)body.Length));
        }
        catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested) { return null; }
        catch (Exception error) when (error is HttpRequestException or IOException or DecoderFallbackException) { return null; }
    }

    internal static IReadOnlyList<DiscoveredMod>? ParsePaliverse(string markdown)
    {
        var lines = Section(markdown, "Game Mods with DualSenseX");
        if (lines is null) return null;
        var result = new Dictionary<string, DiscoveredMod>(StringComparer.OrdinalIgnoreCase);
        foreach (var line in lines)
        {
            if (TryModLink(line, "Paliverse", out var mod)) result.TryAdd(mod.Page.AbsoluteUri, mod);
            if (result.Count == MaximumEntriesPerSource) break;
        }
        return result.Values.ToArray();
    }

    internal static IReadOnlyList<DiscoveredMod>? ParseDsz(string markdown)
    {
        var lines = Section(markdown, "Mod compatibility");
        if (lines is null) return null;
        var result = new Dictionary<string, DiscoveredMod>(StringComparer.OrdinalIgnoreCase);
        bool table = false;
        foreach (var line in lines)
        {
            if (!line.TrimStart().StartsWith('|')) { table = false; continue; }
            var cells = line.Trim().Trim('|').Split('|').Select(cell => cell.Trim()).ToArray();
            if (cells.Length < 3) { table = false; continue; }
            if (cells[0].Equals("Mod", StringComparison.OrdinalIgnoreCase)
                && cells[1].Equals("Game", StringComparison.OrdinalIgnoreCase)
                && cells[2].Equals("License", StringComparison.OrdinalIgnoreCase))
            {
                table = true;
                continue;
            }
            if (!table || cells[1].Length == 0 || cells[1].Contains("library", StringComparison.OrdinalIgnoreCase)
                || cells[1].Contains("engine add-on", StringComparison.OrdinalIgnoreCase)) continue;
            if (TryModLink(cells[0], "DSZ", out var mod)) result.TryAdd(mod.Page.AbsoluteUri, mod);
            if (result.Count == MaximumEntriesPerSource) break;
        }
        return result.Values.ToArray();
    }

    // Only the named section is considered. Code fences and other sections are excluded.
    static List<string>? Section(string markdown, string name)
    {
        List<string>? result = null;
        int level = 0;
        char fence = '\0';
        foreach (var line in markdown.Split('\n'))
        {
            var trimmed = line.Trim();
            if (trimmed.StartsWith("```", StringComparison.Ordinal) || trimmed.StartsWith("~~~", StringComparison.Ordinal))
            {
                if (fence == '\0') fence = trimmed[0];
                else if (fence == trimmed[0]) fence = '\0';
                continue;
            }
            if (fence != '\0') continue;
            var heading = Heading.Match(line);
            if (heading.Success)
            {
                int nextLevel = heading.Groups[1].Length;
                if (result is not null && nextLevel <= level) break;
                var title = heading.Groups[2].Value.Replace("*", "").Replace("_", "").Replace("`", "").Trim();
                if (result is null && title.Equals(name, StringComparison.OrdinalIgnoreCase))
                {
                    result = [];
                    level = nextLevel;
                }
                continue;
            }
            result?.Add(line);
        }
        return result;
    }

    static bool TryModLink(string line, string source, out DiscoveredMod mod)
    {
        mod = null!;
        var match = Link.Match(line);
        if (!match.Success) return false;
        string name = WebUtility.HtmlDecode(match.Groups["name"].Value).Trim();
        string raw = match.Groups["url"].Value;
        if (name.Length == 0 || name.Any(char.IsControl) || raw.Contains('\\')
            || !Uri.TryCreate(raw, UriKind.Absolute, out var uri) || uri.Scheme != Uri.UriSchemeHttps
            || !uri.IsDefaultPort || uri.UserInfo.Length != 0 || !PageHosts.Contains(uri.IdnHost)) return false;
        var builder = new UriBuilder(uri) { Fragment = "" };
        if (uri.Host.Equals("github.com", StringComparison.OrdinalIgnoreCase))
        {
            var parts = uri.AbsolutePath.Trim('/').Split('/');
            if (parts.Length < 2) return false;
            string repository = parts[1].EndsWith(".git", StringComparison.OrdinalIgnoreCase) ? parts[1][..^4] : parts[1];
            if (parts[0].Length == 0 || repository.Length == 0) return false;
            builder.Path = $"/{parts[0]}/{repository}";
            builder.Query = "";
        }
        else
        {
            builder.Path = builder.Path.TrimEnd('/');
            if (uri.Host.EndsWith("nexusmods.com", StringComparison.OrdinalIgnoreCase)) builder.Query = "";
        }
        mod = new DiscoveredMod(name, builder.Uri, source);
        return true;
    }

    public void Dispose()
    {
        if (ownsHttp) http.Dispose();
    }
}
