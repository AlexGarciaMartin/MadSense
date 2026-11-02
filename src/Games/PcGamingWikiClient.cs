using System.Diagnostics;
using System.Net;
using System.Net.Http;
using System.Text;
using System.Text.Json;

namespace MadSense.Games;

internal enum WikiValue { Unknown, Yes, No, Limited, ModRequired, NotApplicable }
internal sealed record WikiSupport(string Title, Uri Source, IReadOnlyDictionary<string, WikiValue> Features);

/// <summary>
/// Looks up one Steam app on demand. PCGamingWiki is evidence about a game, not a guarantee that
/// its current build exposes every feature. Missing/unrecognised values remain explicitly unknown.
/// </summary>
internal sealed class PcGamingWikiClient : IDisposable
{
    const int MaximumBodyBytes = 1024 * 1024;
    const string Origin = "https://www.pcgamingwiki.com";
    const string UserAgent = "MadSense/0.4.6 (+https://github.com/AlexGarciaMartin/MadSense)";
    static readonly TimeSpan RequestBudget = TimeSpan.FromSeconds(10);
    static readonly TimeSpan AccessBackoff = TimeSpan.FromSeconds(60);
    static readonly IReadOnlyDictionary<string, string> Fields = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
    {
        ["playstation controllers"] = "controller",
        ["dualsense haptics support"] = "haptics",
        ["dualsense adaptive trigger support"] = "triggers",
        ["playstation speaker"] = "speaker",
        ["playstation motion sensors"] = "gyro",
        ["light bar support"] = "lightbar",
    };
    readonly HttpClient http;
    readonly bool ownsHttp;
    readonly TimeSpan minimumRequestInterval;
    readonly SemaphoreSlim requests = new(1, 1);
    readonly CancellationTokenSource lifetime = new();
    readonly Dictionary<string, WikiSupport?> cache = new(StringComparer.Ordinal);
    long lastRequestStart;
    bool hasRequested;
    bool disposed;
    DateTimeOffset serverBackoffUntil;

    public PcGamingWikiClient() : this(new HttpClient(new HttpClientHandler { AllowAutoRedirect = false })
        { Timeout = Timeout.InfiniteTimeSpan }, TimeSpan.FromSeconds(2.1), ownsHttp: true) { }

    internal PcGamingWikiClient(HttpClient http) : this(http, TimeSpan.FromSeconds(2.1), ownsHttp: false) { }

    // Test transport can use zero spacing; the application constructors always respect the API limit.
    internal PcGamingWikiClient(HttpClient http, TimeSpan minimumRequestInterval) : this(http, minimumRequestInterval, ownsHttp: false) { }

    PcGamingWikiClient(HttpClient http, TimeSpan minimumRequestInterval, bool ownsHttp)
    {
        this.http = http ?? throw new ArgumentNullException(nameof(http));
        if (minimumRequestInterval < TimeSpan.Zero) throw new ArgumentOutOfRangeException(nameof(minimumRequestInterval));
        this.minimumRequestInterval = minimumRequestInterval;
        this.ownsHttp = ownsHttp;
    }

    public async Task<WikiSupport?> GetAsync(string appId, CancellationToken cancellationToken = default)
    {
        cancellationToken.ThrowIfCancellationRequested();
        if (string.IsNullOrEmpty(appId) || appId.Length > 10 || appId.Any(character => character is < '0' or > '9')
            || !uint.TryParse(appId, out var numericId) || numericId == 0 || appId != numericId.ToString(System.Globalization.CultureInfo.InvariantCulture))
            return null;
        ObjectDisposedException.ThrowIf(disposed, this);
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken, lifetime.Token);
        timeout.CancelAfter(RequestBudget);
        var locked = false;
        try
        {
            await requests.WaitAsync(timeout.Token).ConfigureAwait(false);
            locked = true;
            if (cache.TryGetValue(appId, out var cached)) return cached;
            // Cloudflare/access blocks and rate limits apply to the client, not just one game.
            // Leave cached evidence available, but do not generate more requests while blocked.
            if (DateTimeOffset.UtcNow < serverBackoffUntil) return null;
            var result = await FetchAsync(appId, timeout.Token).ConfigureAwait(false);
            if (result.Cacheable) cache[appId] = result.Support;
            return result.Support;
        }
        catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested) { return null; }
        catch (Exception error) when (error is HttpRequestException or IOException or JsonException or FormatException) { return null; }
        finally { if (locked) requests.Release(); }
    }

    async Task<(bool Cacheable, WikiSupport? Support)> FetchAsync(string appId, CancellationToken cancellationToken)
    {
        using var lookup = await SendAsync(new Uri($"{Origin}/api/appid.php?appid={appId}"), cancellationToken).ConfigureAwait(false);
        if (lookup.StatusCode == HttpStatusCode.NotFound) return (true, null);
        if (lookup.StatusCode is not (HttpStatusCode.MovedPermanently or HttpStatusCode.Redirect or HttpStatusCode.SeeOther
            or HttpStatusCode.TemporaryRedirect or HttpStatusCode.PermanentRedirect)) return (false, null);
        var location = lookup.Headers.Location;
        if (location is null || !Uri.TryCreate(new Uri(Origin), location, out var source)
            || !IsWikiArticle(source)) return (false, null);
        var page = Uri.UnescapeDataString(source.AbsolutePath["/wiki/".Length..]).Replace('_', ' ');
        if (!ValidTitle(page)) return (false, null);
        var query = new Uri($"{Origin}/w/api.php?action=parse&format=json&redirects=1&prop=wikitext&page={Uri.EscapeDataString(page)}");
        using var response = await SendAsync(query, cancellationToken).ConfigureAwait(false);
        if (response.StatusCode == HttpStatusCode.NotFound) return (true, null);
        if (!response.IsSuccessStatusCode) return (false, null);
        var body = await ReadBodyAsync(response.Content, cancellationToken).ConfigureAwait(false);
        using var json = JsonDocument.Parse(body);
        var root = json.RootElement;
        if (root.ValueKind != JsonValueKind.Object) return (false, null);
        if (root.TryGetProperty("error", out var error) && error.ValueKind == JsonValueKind.Object)
            return (error.TryGetProperty("code", out var code) && code.ValueKind == JsonValueKind.String && code.GetString() == "missingtitle", null);
        if (!root.TryGetProperty("parse", out var parse) || parse.ValueKind != JsonValueKind.Object
            || !parse.TryGetProperty("title", out var titleValue) || titleValue.ValueKind != JsonValueKind.String
            || !parse.TryGetProperty("wikitext", out var wiki) || wiki.ValueKind != JsonValueKind.Object
            || !wiki.TryGetProperty("*", out var textValue) || textValue.ValueKind != JsonValueKind.String) return (false, null);
        var title = titleValue.GetString()!;
        if (!ValidTitle(title)) return (false, null);
        var canonical = new Uri($"{Origin}/wiki/{Uri.EscapeDataString(title.Replace(' ', '_'))}");
        return (true, new WikiSupport(title, canonical, ParseFeatures(textValue.GetString()!)));
    }

    async Task<HttpResponseMessage> SendAsync(Uri uri, CancellationToken cancellationToken)
    {
        if (hasRequested)
        {
            var remaining = minimumRequestInterval - Stopwatch.GetElapsedTime(lastRequestStart);
            if (remaining > TimeSpan.Zero) await Task.Delay(remaining, cancellationToken).ConfigureAwait(false);
        }
        using var request = new HttpRequestMessage(HttpMethod.Get, uri);
        request.Headers.UserAgent.ParseAdd(UserAgent);
        request.Headers.Accept.ParseAdd("application/json");
        lastRequestStart = Stopwatch.GetTimestamp();
        hasRequested = true;
        var response = await http.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, cancellationToken).ConfigureAwait(false);
        // The production handler never follows redirects. Also reject an injected transport that
        // silently follows one, so content from an unexpected URL is never interpreted as evidence.
        if (response.RequestMessage?.RequestUri is { } actual && actual != uri)
        {
            response.Dispose();
            throw new HttpRequestException("Unexpected PCGamingWiki response URL.");
        }
        if (response.StatusCode is HttpStatusCode.Forbidden or HttpStatusCode.TooManyRequests)
            serverBackoffUntil = DateTimeOffset.UtcNow + AccessBackoff;
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

    static bool IsWikiArticle(Uri source) => source.Scheme == Uri.UriSchemeHttps
        && source.Host.Equals("www.pcgamingwiki.com", StringComparison.OrdinalIgnoreCase) && source.IsDefaultPort
        && source.UserInfo.Length == 0 && source.Query.Length == 0 && source.Fragment.Length == 0
        && source.AbsolutePath.StartsWith("/wiki/", StringComparison.Ordinal) && source.AbsolutePath.Length > "/wiki/".Length;

    static bool ValidTitle(string title) => !string.IsNullOrWhiteSpace(title) && title.Length <= 512 && !title.Any(char.IsControl);

    static IReadOnlyDictionary<string, WikiValue> ParseFeatures(string wikitext)
    {
        var features = Fields.Values.Append("touchpad").ToDictionary(key => key, _ => WikiValue.Unknown, StringComparer.Ordinal);
        var seen = new HashSet<string>(StringComparer.Ordinal);
        if (FindInput(wikitext) is not { } input) return features;
        foreach (var field in SplitFields(input))
        {
            var equals = field.IndexOf('=');
            if (equals < 0 || !Fields.TryGetValue(field[..equals].Trim(), out var feature)) continue;
            if (!seen.Add(feature)) { features[feature] = WikiValue.Unknown; continue; }
            features[feature] = StripComments(field[(equals + 1)..]).Trim().ToLowerInvariant() switch
            {
                "true" => WikiValue.Yes,
                "false" => WikiValue.No,
                "limited" => WikiValue.Limited,
                "hackable" => WikiValue.ModRequired,
                "n/a" => WikiValue.NotApplicable,
                _ => WikiValue.Unknown,
            };
        }
        return features;
    }

    static string? FindInput(string text)
    {
        for (var index = 0; index < text.Length; index++)
        {
            if (SkipLiteral(text, ref index)) { index--; continue; }
            if (!text.AsSpan(index).StartsWith("{{")) continue;
            var end = BalancedEnd(text, index);
            if (end < 0) return null;
            var width = text.AsSpan(index).StartsWith("{{{") ? 3 : 2;
            var pipe = text.IndexOf('|', index + width, end - index - width);
            if (width == 2 && pipe >= 0 && text[(index + width)..pipe].Trim().Equals("Input", StringComparison.OrdinalIgnoreCase))
                return text[(pipe + 1)..(end - 2)];
            index = end - 1;
        }
        return null;
    }

    static IEnumerable<string> SplitFields(string input)
    {
        var start = 0;
        var linkDepth = 0;
        for (var index = 0; index < input.Length; index++)
        {
            if (SkipLiteral(input, ref index)) { index--; continue; }
            var remaining = input.AsSpan(index);
            if (remaining.StartsWith("{{"))
            {
                var end = BalancedEnd(input, index);
                if (end < 0) yield break;
                index = end - 1;
            }
            else if (remaining.StartsWith("[[")) { linkDepth++; index++; }
            else if (remaining.StartsWith("]]")) { if (linkDepth > 0) linkDepth--; index++; }
            else if (input[index] == '|' && linkDepth == 0)
            {
                yield return input[start..index];
                start = index + 1;
            }
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
            if (remaining.StartsWith("{{"))
            {
                var width = remaining.StartsWith("{{{") ? 3 : 2;
                stack.Push(width);
                if (stack.Count > 64) return -1;
                index += width;
            }
            else if (stack.Count > 0 && remaining.StartsWith(stack.Peek() == 3 ? "}}}" : "}}"))
            {
                index += stack.Pop();
                if (stack.Count == 0) return index;
            }
            else index++;
        }
        return -1;
    }

    static bool SkipLiteral(string text, ref int index)
    {
        var remaining = text.AsSpan(index);
        if (remaining.StartsWith("<!--"))
        {
            var end = text.IndexOf("-->", index + 4, StringComparison.Ordinal);
            index = end < 0 ? text.Length : end + 3;
            return true;
        }
        if (remaining.StartsWith("<nowiki>", StringComparison.OrdinalIgnoreCase))
        {
            var end = text.IndexOf("</nowiki>", index + 8, StringComparison.OrdinalIgnoreCase);
            index = end < 0 ? text.Length : end + 9;
            return true;
        }
        return false;
    }

    static string StripComments(string value)
    {
        var output = new StringBuilder();
        for (var index = 0; index < value.Length;)
        {
            if (value.AsSpan(index).StartsWith("<!--"))
            {
                var end = value.IndexOf("-->", index + 4, StringComparison.Ordinal);
                if (end < 0) break;
                index = end + 3;
            }
            else output.Append(value[index++]);
        }
        return output.ToString();
    }

    public void Dispose()
    {
        if (disposed) return;
        disposed = true;
        lifetime.Cancel();
        if (ownsHttp) http.Dispose();
        // Pending calls may still release the semaphore; it has no unmanaged handle unless
        // AvailableWaitHandle is requested. Leave both synchronisation objects alive for them.
    }
}
