using System.Net.Http;
using System.Runtime.InteropServices;
using System.Text.Json;

namespace MadSense;

sealed record LinuxUpdate(Version Version, Uri Download, Uri Release);
sealed record LinuxUpdateResult(bool Available, bool HasLinuxRelease, LinuxUpdate? Update = null);
static class LinuxUpdates
{
    const string Repository = "https://github.com/AlexGarciaMartin/MadSense";
    const int Limit = 512 * 1024;
    static readonly HttpClient Http = new(new HttpClientHandler { AllowAutoRedirect = false }) { Timeout = TimeSpan.FromSeconds(10) };
    public static async Task<LinuxUpdateResult> Check(CancellationToken token = default)
    {
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(token); timeout.CancelAfter(TimeSpan.FromSeconds(10));
        try
        {
            using var request = new HttpRequestMessage(HttpMethod.Get, "https://api.github.com/repos/AlexGarciaMartin/MadSense/releases?per_page=30");
            request.Headers.UserAgent.ParseAdd("MadSense-Linux-UpdateCheck");
            request.Headers.Accept.ParseAdd("application/vnd.github+json");
            using var response = await Http.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, timeout.Token);
            if (!response.IsSuccessStatusCode || response.Content.Headers.ContentLength > Limit) return new(false, false);
            using var source = await response.Content.ReadAsStreamAsync(timeout.Token);
            var bytes = new byte[Limit + 1]; var size = 0;
            while (size < bytes.Length)
            { var count = await source.ReadAsync(bytes.AsMemory(size), timeout.Token); if (count == 0) break; size += count; }
            if (size > Limit) return new(false, false);
            using var document = JsonDocument.Parse(bytes.AsMemory(0, size));
            var architecture = RuntimeInformation.ProcessArchitecture switch { Architecture.Arm64 => "arm64", Architecture.X64 => "x64", _ => "unsupported" };
            // Current packages and the visible version badge are development
            // builds. Their first stable release may have the same base version.
            return Parse(document.RootElement, new Version(0, 5, 0), architecture, developmentBuild: true);
        }
        catch (Exception error) when (error is HttpRequestException or IOException or JsonException or OperationCanceledException)
        { return new(false, false); }
    }
    internal static LinuxUpdateResult Parse(JsonElement releases, Version current, string architecture, bool developmentBuild = false)
    {
        if (architecture is not ("arm64" or "x64") || releases.ValueKind != JsonValueKind.Array) return new(false, false);
        LinuxUpdate? latest = null;
        foreach (var release in releases.EnumerateArray())
        {
            if (release.ValueKind != JsonValueKind.Object ||
                !release.TryGetProperty("draft", out var draft) || draft.ValueKind != JsonValueKind.False ||
                !release.TryGetProperty("prerelease", out var prerelease) || prerelease.ValueKind != JsonValueKind.False ||
                !release.TryGetProperty("tag_name", out var tagValue) || tagValue.ValueKind != JsonValueKind.String) continue;
            var tag = tagValue.GetString()!;
            if (!tag.StartsWith("linux-v", StringComparison.Ordinal) || !Version.TryParse(tag[7..], out var version) ||
                version.Build < 0 || version.Revision >= 0 || tag != $"linux-v{version.Major}.{version.Minor}.{version.Build}") continue;
            var releaseUrl = $"{Repository}/releases/tag/{tag}";
            if (!release.TryGetProperty("html_url", out var page) || page.ValueKind != JsonValueKind.String || page.GetString() != releaseUrl ||
                !release.TryGetProperty("assets", out var assets) || assets.ValueKind != JsonValueKind.Array) continue;
            var filename = $"MadSense-{version}-linux-{architecture}.tar.gz";
            var download = $"{Repository}/releases/download/{tag}/{filename}";
            var matches = assets.EnumerateArray().Count(asset => asset.ValueKind == JsonValueKind.Object &&
                asset.TryGetProperty("name", out var name) && name.ValueKind == JsonValueKind.String && name.GetString() == filename &&
                asset.TryGetProperty("browser_download_url", out var url) && url.ValueKind == JsonValueKind.String && url.GetString() == download);
            if (matches == 1 && (latest == null || version > latest.Version)) latest = new(version, new Uri(download), new Uri(releaseUrl));
        }
        return latest == null ? new(true, false) : new(true, true,
            latest.Version > current || (developmentBuild && latest.Version == current) ? latest : null);
    }
}
