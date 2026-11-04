using System.Diagnostics;
using System.Globalization;
using System.Net;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Text.RegularExpressions;

namespace MadSense.Games;

internal enum WikiValue { Unknown, Yes, No, Limited, ModRequired, NotApplicable }

/// <summary>What PCGamingWiki records about a game's PlayStation controller support.</summary>
internal sealed class WikiSupport
{
    public string Title { get; set; } = "";
    public string Page { get; set; } = "";
    public DateTimeOffset Fetched { get; set; }
    public Dictionary<string, string> Fields { get; set; } = new(StringComparer.OrdinalIgnoreCase);
    public string? SteamAppId { get; set; }

    [JsonIgnore] public Uri Source => new("https://www.pcgamingwiki.com/wiki/" + Uri.EscapeDataString(Page.Replace(' ', '_')));
    [JsonIgnore] public WikiValue Controller => Parse("playstation controllers");
    [JsonIgnore] public WikiValue Haptics => Parse("dualsense haptics support");
    [JsonIgnore] public WikiValue Triggers => Parse("dualsense adaptive trigger support");
    [JsonIgnore] public WikiValue Speaker => Parse("playstation speaker");
    [JsonIgnore] public WikiValue Motion => Parse("playstation motion sensors");
    [JsonIgnore] public WikiValue Lightbar => Parse("light bar support");
    [JsonIgnore] public WikiValue Prompts => Parse("playstation prompts");
    [JsonIgnore] public WikiValue AnyController => Parse("controller support");
    [JsonIgnore] public string ConnectionModes => Fields.TryGetValue("playstation connection modes", out var v) ? v : "";
    public string? Note(string field) => Fields.TryGetValue(field + " notes", out var v) && v.Length > 0 ? v : null;

    /// <summary>The game's own DualSense features, as the wiki's editors recorded them.</summary>
    [JsonIgnore] public bool NativeDualSense => Haptics == WikiValue.Yes || Triggers == WikiValue.Yes;

    public WikiValue Parse(string field) => Fields.TryGetValue(field, out var raw) ? ParseValue(raw) : WikiValue.Unknown;

    public static WikiValue ParseValue(string? raw) => (raw ?? "").Trim().ToLowerInvariant() switch
    {
        "true" => WikiValue.Yes,
        "false" => WikiValue.No,
        "limited" => WikiValue.Limited,
        "hackable" => WikiValue.ModRequired,
        "n/a" => WikiValue.NotApplicable,
        _ => WikiValue.Unknown,
    };
}

/// <summary>
/// Looks a game up on PCGamingWiki and reads its Input template. The wiki is evidence written by
/// people, not a guarantee about the build on this PC: missing or unrecognised values stay unknown
/// and are shown as such. Pages are found by name through the MediaWiki search API and, for Steam
/// games, accepted only when the page's infobox carries the same Steam AppID. Results are cached on
/// disk for thirty days, requests are spaced by at least 2.1 s and stop for a minute after a 403/429,
/// as the wiki's API terms ask. Content is CC BY-NC-SA: it is quoted with attribution, never
/// redistributed inside MadSense.
/// </summary>
internal sealed class PcGamingWikiClient : IDisposable
{
    const int MaximumBodyBytes = 2 * 1024 * 1024;
    const string Api = "https://www.pcgamingwiki.com/w/api.php";
    const string UserAgent = "MadSense/0.5 (+https://madsense.pro; games-compatibility)";
    static readonly TimeSpan RequestBudget = TimeSpan.FromSeconds(12);
    static readonly TimeSpan AccessBackoff = TimeSpan.FromSeconds(60);
    static readonly TimeSpan CacheAge = TimeSpan.FromDays(30);
    static readonly JsonSerializerOptions Json = new() { WriteIndented = false };
    static readonly Regex EditionSuffix = new(@"\s*[:\-–—]?\s*\b(Complete|Definitive|Deluxe|Ultimate|Game of the Year|GOTY|Gold|Enhanced|Remastered|Special|Legendary|Anniversary|Director'?s Cut|Digital)\b(\s+Edition)?\s*$", RegexOptions.IgnoreCase | RegexOptions.CultureInvariant);
    readonly HttpClient http;
    readonly bool ownsHttp;
    readonly TimeSpan minimumRequestInterval;
    readonly SemaphoreSlim requests = new(1, 1);
    readonly CancellationTokenSource lifetime = new();
    readonly string? cachePath;
    readonly Dictionary<string, WikiSupport?> cache = new(StringComparer.OrdinalIgnoreCase);
    long lastRequestStart;
    bool hasRequested, disposed, cacheDirty;
    DateTimeOffset serverBackoffUntil;

    public PcGamingWikiClient(string? cachePath = null) : this(new HttpClient(new HttpClientHandler { AllowAutoRedirect = false }) { Timeout = Timeout.InfiniteTimeSpan }, TimeSpan.FromSeconds(2.1), ownsHttp: true, cachePath) { }

    internal PcGamingWikiClient(HttpClient http) : this(http, TimeSpan.Zero, ownsHttp: false, null) { }

    PcGamingWikiClient(HttpClient http, TimeSpan minimumRequestInterval, bool ownsHttp, string? cachePath)
    {
        this.http = http ?? throw new ArgumentNullException(nameof(http));
        this.minimumRequestInterval = minimumRequestInterval;
        this.ownsHttp = ownsHttp;
        this.cachePath = cachePath;
        LoadCache();
    }

    /// <summary>The cached record, if the game was looked up before (any age); no network.</summary>
    public WikiSupport? Cached(LibraryGame game)
    {
        lock (cache) return cache.TryGetValue(Key(game), out var hit) ? hit : null;
    }

    /// <summary>Whether a lookup is currently pointless because the wiki rate-limited or blocked us.</summary>
    public bool Blocked => DateTimeOffset.UtcNow < serverBackoffUntil;

    public async Task<WikiSupport?> GetAsync(LibraryGame game, CancellationToken cancellationToken = default, bool refresh = false)
    {
        cancellationToken.ThrowIfCancellationRequested();
        ObjectDisposedException.ThrowIf(disposed, this);
        var key = Key(game);
        lock (cache)
        {
            if (!refresh && cache.TryGetValue(key, out var hit) && (hit is null || DateTimeOffset.UtcNow - hit.Fetched < CacheAge)) return hit;
        }
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken, lifetime.Token);
        timeout.CancelAfter(RequestBudget * 3);
        var locked = false;
        try
        {
            await requests.WaitAsync(timeout.Token).ConfigureAwait(false);
            locked = true;
            if (Blocked) return Cached(game);
            var result = await FindAsync(game, timeout.Token).ConfigureAwait(false);
            if (result.Cacheable)
            {
                lock (cache) { cache[key] = result.Support; cacheDirty = true; }
                SaveCache();
            }
            return result.Support ?? Cached(game);
        }
        catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested) { return Cached(game); }
        catch (Exception error) when (error is HttpRequestException or IOException or JsonException or FormatException) { return Cached(game); }
        finally { if (locked) requests.Release(); }
    }

    static string Key(LibraryGame game) => game.SteamAppId is { } id ? "steam:" + id : "name:" + Normalize(game.Name);

    async Task<(bool Cacheable, WikiSupport? Support)> FindAsync(LibraryGame game, CancellationToken cancellationToken)
    {
        var candidates = new List<string>();
        foreach (var query in Queries(game.Name))
        {
            foreach (var title in await SearchAsync(query, cancellationToken).ConfigureAwait(false))
                if (!candidates.Contains(title, StringComparer.OrdinalIgnoreCase)) candidates.Add(title);
            if (candidates.Count > 0) break;
        }
        if (candidates.Count == 0) return (true, null);
        bool sawPage = false;
        foreach (var title in candidates.Take(4))
        {
            var page = await FetchAsync(title, cancellationToken).ConfigureAwait(false);
            if (page is null) continue;
            sawPage = true;
            // Steam games: the page must name the same AppID; a different game with a similar name is worse than no data.
            if (game.SteamAppId is { } id)
            {
                if (page.SteamAppId is null) continue;
                if (!page.SteamAppId.Split(',').Select(s => s.Trim()).Contains(id)) continue;
            }
            else if (Similarity(page.Title, game.Name) < 0.6) continue;
            return (true, page);
        }
        return (sawPage, null);
    }

    static IEnumerable<string> Queries(string name)
    {
        yield return name;
        var trimmed = EditionSuffix.Replace(name, "").Trim();
        if (!string.Equals(trimmed, name, StringComparison.Ordinal) && trimmed.Length > 2) yield return trimmed;
    }

    async Task<List<string>> SearchAsync(string query, CancellationToken cancellationToken)
    {
        var titles = new List<string>();
        using (var response = await SendAsync(new Uri($"{Api}?action=opensearch&format=json&limit=5&search={Uri.EscapeDataString(query)}"), cancellationToken).ConfigureAwait(false))
        {
            if (response.IsSuccessStatusCode)
            {
                using var json = JsonDocument.Parse(await ReadBodyAsync(response.Content, cancellationToken).ConfigureAwait(false));
                if (json.RootElement.ValueKind == JsonValueKind.Array && json.RootElement.GetArrayLength() > 1 && json.RootElement[1].ValueKind == JsonValueKind.Array)
                    foreach (var title in json.RootElement[1].EnumerateArray())
                        if (title.ValueKind == JsonValueKind.String && ValidTitle(title.GetString())) titles.Add(title.GetString()!);
            }
        }
        if (titles.Count > 0) return titles;
        using var search = await SendAsync(new Uri($"{Api}?action=query&list=search&format=json&srlimit=4&srsearch={Uri.EscapeDataString(query)}"), cancellationToken).ConfigureAwait(false);
        if (!search.IsSuccessStatusCode) return titles;
        using var document = JsonDocument.Parse(await ReadBodyAsync(search.Content, cancellationToken).ConfigureAwait(false));
        if (document.RootElement.TryGetProperty("query", out var q) && q.TryGetProperty("search", out var hits) && hits.ValueKind == JsonValueKind.Array)
            foreach (var hit in hits.EnumerateArray())
                if (hit.TryGetProperty("title", out var title) && title.ValueKind == JsonValueKind.String && ValidTitle(title.GetString())) titles.Add(title.GetString()!);
        return titles;
    }

    async Task<WikiSupport?> FetchAsync(string title, CancellationToken cancellationToken)
    {
        using var response = await SendAsync(new Uri($"{Api}?action=parse&format=json&redirects=1&prop=wikitext&page={Uri.EscapeDataString(title)}"), cancellationToken).ConfigureAwait(false);
        if (!response.IsSuccessStatusCode) return null;
        using var json = JsonDocument.Parse(await ReadBodyAsync(response.Content, cancellationToken).ConfigureAwait(false));
        var root = json.RootElement;
        if (root.ValueKind != JsonValueKind.Object || !root.TryGetProperty("parse", out var parse) || parse.ValueKind != JsonValueKind.Object
            || !parse.TryGetProperty("title", out var titleValue) || titleValue.ValueKind != JsonValueKind.String
            || !parse.TryGetProperty("wikitext", out var wiki) || wiki.ValueKind != JsonValueKind.Object
            || !wiki.TryGetProperty("*", out var textValue) || textValue.ValueKind != JsonValueKind.String) return null;
        var pageTitle = titleValue.GetString()!;
        if (!ValidTitle(pageTitle)) return null;
        var text = textValue.GetString()!;
        return new WikiSupport
        {
            Title = pageTitle,
            Page = pageTitle,
            Fetched = DateTimeOffset.UtcNow,
            Fields = ParseTemplate(text, "Input"),
            SteamAppId = ParseTemplate(text, "Infobox game").TryGetValue("steam appid", out var appId) && appId.Length > 0 ? appId : null,
        };
    }

    async Task<HttpResponseMessage> SendAsync(Uri uri, CancellationToken cancellationToken)
    {
        if (hasRequested && minimumRequestInterval > TimeSpan.Zero)
        {
            var remaining = minimumRequestInterval - Stopwatch.GetElapsedTime(lastRequestStart);
            if (remaining > TimeSpan.Zero) await Task.Delay(remaining, cancellationToken).ConfigureAwait(false);
        }
        using var budget = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        budget.CancelAfter(RequestBudget);
        using var request = new HttpRequestMessage(HttpMethod.Get, uri);
        request.Headers.UserAgent.ParseAdd(UserAgent);
        request.Headers.Accept.ParseAdd("application/json");
        lastRequestStart = Stopwatch.GetTimestamp();
        hasRequested = true;
        var response = await http.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, budget.Token).ConfigureAwait(false);
        if (response.RequestMessage?.RequestUri is { } actual && actual != uri) { response.Dispose(); throw new HttpRequestException("Unexpected PCGamingWiki response URL."); }
        if (response.StatusCode is HttpStatusCode.Forbidden or HttpStatusCode.TooManyRequests) serverBackoffUntil = DateTimeOffset.UtcNow + AccessBackoff;
        return response;
    }

    static async Task<byte[]> ReadBodyAsync(HttpContent content, CancellationToken cancellationToken)
    {
        if (content.Headers.ContentLength > MaximumBodyBytes) throw new FormatException("PCGamingWiki response is too large.");
        await using var stream = await content.ReadAsStreamAsync(cancellationToken).ConfigureAwait(false);
        using var output = new MemoryStream();
        var buffer = new byte[16 * 1024];
        while (true)
        {
            var read = await stream.ReadAsync(buffer, cancellationToken).ConfigureAwait(false);
            if (read == 0) break;
            if (output.Length + read > MaximumBodyBytes) throw new FormatException("PCGamingWiki response is too large.");
            output.Write(buffer, 0, read);
        }
        return output.ToArray();
    }

    static bool ValidTitle(string? title) => !string.IsNullOrWhiteSpace(title) && title.Length <= 256 && !title.Any(char.IsControl) && !title.Contains(':');

    // ── wikitext ──────────────────────────────────────────────────────────────────────────────
    /// <summary>The fields of the first {{Name|...}} template, comments and references removed.</summary>
    internal static Dictionary<string, string> ParseTemplate(string wikitext, string name)
    {
        var fields = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        if (FindTemplate(wikitext, name) is not { } body) return fields;
        foreach (var field in SplitFields(body))
        {
            var equals = field.IndexOf('=');
            if (equals < 0) continue;
            var key = field[..equals].Trim();
            if (key.Length == 0 || key.Length > 64) continue;
            var value = Regex.Replace(StripComments(field[(equals + 1)..]), @"<ref[^>]*/>|<ref[^>]*>.*?</ref>", "", RegexOptions.Singleline).Trim();
            fields.TryAdd(key, value);
        }
        return fields;
    }

    static string? FindTemplate(string text, string name)
    {
        for (var index = 0; index < text.Length; index++)
        {
            if (SkipLiteral(text, ref index)) { index--; continue; }
            if (!text.AsSpan(index).StartsWith("{{")) continue;
            var end = BalancedEnd(text, index);
            if (end < 0) return null;
            var width = text.AsSpan(index).StartsWith("{{{") ? 3 : 2;
            var pipe = text.IndexOf('|', index + width, end - index - width);
            if (width == 2 && pipe >= 0 && text[(index + width)..pipe].Trim().Equals(name, StringComparison.OrdinalIgnoreCase))
                return text[(pipe + 1)..(end - 2)];
            index = end - 1;
        }
        return null;
    }

    static IEnumerable<string> SplitFields(string input)
    {
        var start = 0; var linkDepth = 0;
        for (var index = 0; index < input.Length; index++)
        {
            if (SkipLiteral(input, ref index)) { index--; continue; }
            var remaining = input.AsSpan(index);
            if (remaining.StartsWith("{{")) { var end = BalancedEnd(input, index); if (end < 0) yield break; index = end - 1; }
            else if (remaining.StartsWith("[[")) { linkDepth++; index++; }
            else if (remaining.StartsWith("]]")) { if (linkDepth > 0) linkDepth--; index++; }
            else if (input[index] == '|' && linkDepth == 0) { yield return input[start..index]; start = index + 1; }
        }
        yield return input[start..];
    }

    static int BalancedEnd(string text, int start)
    {
        var stack = new Stack<int>();
        for (var index = start; index < text.Length;)
        {
            if (SkipLiteral(text, ref index)) continue;
            var remaining = text.AsSpan(index);
            if (remaining.StartsWith("{{")) { var width = remaining.StartsWith("{{{") ? 3 : 2; stack.Push(width); if (stack.Count > 64) return -1; index += width; }
            else if (stack.Count > 0 && remaining.StartsWith(stack.Peek() == 3 ? "}}}" : "}}")) { index += stack.Pop(); if (stack.Count == 0) return index; }
            else index++;
        }
        return -1;
    }

    static bool SkipLiteral(string text, ref int index)
    {
        var remaining = text.AsSpan(index);
        if (remaining.StartsWith("<!--")) { var end = text.IndexOf("-->", index + 4, StringComparison.Ordinal); index = end < 0 ? text.Length : end + 3; return true; }
        if (remaining.StartsWith("<nowiki>", StringComparison.OrdinalIgnoreCase)) { var end = text.IndexOf("</nowiki>", index + 8, StringComparison.OrdinalIgnoreCase); index = end < 0 ? text.Length : end + 9; return true; }
        return false;
    }

    static string StripComments(string value)
    {
        var output = new StringBuilder();
        for (var index = 0; index < value.Length;)
        {
            if (value.AsSpan(index).StartsWith("<!--")) { var end = value.IndexOf("-->", index + 4, StringComparison.Ordinal); if (end < 0) break; index = end + 3; }
            else output.Append(value[index++]);
        }
        return output.ToString();
    }

    // ── matching ──────────────────────────────────────────────────────────────────────────────
    static readonly HashSet<string> StopWords = new(StringComparer.OrdinalIgnoreCase) { "the", "of", "a", "an", "and", "edition", "game" };

    internal static string Normalize(string s) => Regex.Replace(s.ToLowerInvariant().Replace('’', '\'').Replace("'", ""), @"[^a-z0-9]+", " ").Trim();

    internal static double Similarity(string a, string b)
    {
        var x = Normalize(a).Split(' ', StringSplitOptions.RemoveEmptyEntries).Where(t => !StopWords.Contains(t)).ToHashSet();
        var y = Normalize(b).Split(' ', StringSplitOptions.RemoveEmptyEntries).Where(t => !StopWords.Contains(t)).ToHashSet();
        if (x.Count == 0 || y.Count == 0) return 0;
        return (double)x.Intersect(y).Count() / Math.Max(x.Count, y.Count);
    }

    // ── cache ─────────────────────────────────────────────────────────────────────────────────
    void LoadCache()
    {
        if (cachePath is null || !File.Exists(cachePath)) return;
        try
        {
            var stored = JsonSerializer.Deserialize<Dictionary<string, WikiSupport?>>(File.ReadAllText(cachePath), Json);
            if (stored is null) return;
            lock (cache) foreach (var pair in stored) cache[pair.Key] = pair.Value;
        }
        catch (Exception e) when (e is IOException or JsonException or UnauthorizedAccessException) { }
    }

    void SaveCache()
    {
        if (cachePath is null) return;
        try
        {
            string text;
            lock (cache) { if (!cacheDirty) return; cacheDirty = false; text = JsonSerializer.Serialize(cache, Json); }
            Directory.CreateDirectory(Path.GetDirectoryName(cachePath)!);
            File.WriteAllText(cachePath + ".tmp", text);
            File.Move(cachePath + ".tmp", cachePath, true);
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException) { }
    }

    public void Dispose()
    {
        if (disposed) return;
        disposed = true;
        lifetime.Cancel();
        if (ownsHttp) http.Dispose();
    }
}
