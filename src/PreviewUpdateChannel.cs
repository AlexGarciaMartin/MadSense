using System.Net.Http;
using System.Security.Cryptography;
using System.Text.Json;

namespace MadSense;

/// <summary>Only explicit four-component preview builds use this separate, signed channel.</summary>
internal static class PreviewUpdateChannel
{
    internal static readonly Uri FeedUri = new(
        "https://github.com/AlexGarciaMartin/MadSense/releases/download/update-preview-channel/preview.update.json");

    internal static async Task<UpdateCheckResult> CheckAsync(Version current, CancellationToken token = default,
        HttpClient? client = null, string publicKey = IntegratedUpdate.PublicKey)
    {
        // Do not contact the preview feed at all from a stable build.
        if (current.Revision <= 0) return new(UpdateCheckStatus.Unavailable);
        using var deadline = CancellationTokenSource.CreateLinkedTokenSource(token);
        deadline.CancelAfter(TimeSpan.FromSeconds(20));
        try
        {
            using var memory = new MemoryStream();
            await IntegratedUpdate.Fetch(FeedUri, memory, UpdateChecker.MaximumManifestBytes, null, deadline.Token, client).ConfigureAwait(false);
            var p = IntegratedUpdate.VerifyDescriptor(memory.ToArray(), current, preview: true, publicKey, requireNewer: false);
            if (Version.Parse(p.Version) <= current) return new(UpdateCheckStatus.Current);
            return new(UpdateCheckStatus.Available, new(p.Version, new Uri(p.DownloadUrl),
                new Uri($"https://github.com/AlexGarciaMartin/MadSense/releases/tag/update-preview-{p.Version}")));
        }
        catch (OperationCanceledException) when (!token.IsCancellationRequested) { return new(UpdateCheckStatus.Unavailable); }
        catch (Exception e) when (e is HttpRequestException or IOException or InvalidDataException or JsonException or
            CryptographicException or FormatException or InvalidOperationException or ArgumentException)
        {
            return new(UpdateCheckStatus.Unavailable);
        }
    }
}
