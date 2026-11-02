using System.Net.Http;
using System.Text.Json;

namespace MadSense;

internal sealed record AppUpdate(string Version, Uri DownloadUri, Uri ReleaseUri);
internal enum UpdateCheckStatus { Available, Current, Unavailable }
internal sealed record UpdateCheckResult(UpdateCheckStatus Status, AppUpdate? Update = null);

/// <summary>
/// Reads the same small, public release manifest as the website. Never downloads or runs an
/// installer, and never performs controller work. A failed check leaves the app usable offline.
/// </summary>
internal sealed class UpdateChecker
{
    internal const int MaximumManifestBytes = 32 * 1024;
    internal static readonly Uri ManifestUri = new("https://madsense.pro/downloads/release.json");
    static readonly TimeSpan RequestTimeout = TimeSpan.FromSeconds(10);
    static readonly HttpClient sharedHttp = new(new HttpClientHandler { AllowAutoRedirect = false })
    {
        Timeout = RequestTimeout
    };
    readonly HttpClient http;

    public UpdateChecker() : this(sharedHttp) { }
    internal UpdateChecker(HttpClient http) => this.http = http ?? throw new ArgumentNullException(nameof(http));

    public async Task<UpdateCheckResult> CheckAsync(Version currentVersion, CancellationToken cancellationToken = default)
    {
        ArgumentNullException.ThrowIfNull(currentVersion);
        cancellationToken.ThrowIfCancellationRequested();
        // HttpClient's timeout ends at the headers with ResponseHeadersRead. Keep the body read
        // under the same deadline, including an HTTP server that stops sending halfway through.
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        timeout.CancelAfter(RequestTimeout);
        try
        {
            using var request = new HttpRequestMessage(HttpMethod.Get, ManifestUri);
            request.Headers.UserAgent.ParseAdd("MadSense-UpdateCheck");
            request.Headers.Accept.ParseAdd("application/json");
            using var response = await http.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, timeout.Token).ConfigureAwait(false);
            if (!response.IsSuccessStatusCode || response.Content.Headers.ContentLength > MaximumManifestBytes)
                return new(UpdateCheckStatus.Unavailable);

            await using var stream = await response.Content.ReadAsStreamAsync(timeout.Token).ConfigureAwait(false);
            // One extra byte detects oversize chunked responses without trusting Content-Length.
            var buffer = new byte[MaximumManifestBytes + 1];
            var length = 0;
            while (length < buffer.Length)
            {
                var read = await stream.ReadAsync(buffer.AsMemory(length), timeout.Token).ConfigureAwait(false);
                if (read == 0) break;
                length += read;
            }
            if (length > MaximumManifestBytes) return new(UpdateCheckStatus.Unavailable);
            cancellationToken.ThrowIfCancellationRequested();
            using var document = JsonDocument.Parse(buffer.AsMemory(0, length));
            return ReadManifest(document.RootElement, currentVersion);
        }
        catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested)
        {
            return new(UpdateCheckStatus.Unavailable);
        }
        catch (Exception error) when (error is HttpRequestException or IOException or JsonException)
        {
            return new(UpdateCheckStatus.Unavailable);
        }
    }

    static UpdateCheckResult ReadManifest(JsonElement manifest, Version current)
    {
        if (manifest.ValueKind != JsonValueKind.Object
            || !TryString(manifest, "version", out var versionText)
            || !Version.TryParse(versionText, out var version)
            || version.Build < 0 || version.Revision >= 0
            || versionText != $"{version.Major}.{version.Minor}.{version.Build}")
            return new(UpdateCheckStatus.Unavailable);

        var filename = $"MadSense-{versionText}-Setup.exe";
        var releaseUrl = $"https://github.com/AlexGarciaMartin/MadSense/releases/tag/v{versionText}";
        var downloadUrl = $"https://github.com/AlexGarciaMartin/MadSense/releases/download/v{versionText}/{filename}";
        // Compare the entire canonical strings, not just hosts or path prefixes. No userinfo,
        // query parameters, alternate repository, traversal or substituted download is accepted.
        if (!TryString(manifest, "filename", out var suppliedFilename) || suppliedFilename != filename
            || !TryString(manifest, "downloadUrl", out var suppliedDownload) || suppliedDownload != downloadUrl
            || !TryString(manifest, "releaseUrl", out var suppliedRelease) || suppliedRelease != releaseUrl)
            return new(UpdateCheckStatus.Unavailable);

        var normalizedCurrent = new Version(current.Major, current.Minor, Math.Max(0, current.Build));
        return version > normalizedCurrent
            ? new(UpdateCheckStatus.Available, new(versionText, new Uri(downloadUrl), new Uri(releaseUrl)))
            : new(UpdateCheckStatus.Current);
    }

    static bool TryString(JsonElement root, string name, out string value)
    {
        value = "";
        var found = false;
        foreach (var property in root.EnumerateObject())
        {
            if (property.Name != name) continue;
            if (found || property.Value.ValueKind != JsonValueKind.String) return false;
            found = true;
            value = property.Value.GetString()!;
        }
        return found;
    }
}
