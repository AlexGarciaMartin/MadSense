using System.Net;
using System.Net.Http;
using System.Security.Cryptography;
using System.Text.Json;
using MadSense;

namespace MadSense.Installer.Tests;

public class PreviewUpdateChannelTests
{
    static byte[] Descriptor(RSA rsa, string channel = "preview")
    {
        var bytes = JsonSerializer.SerializeToUtf8Bytes(new UpdatePackage("0.4.8.4", channel,
            "MadSense-0.4.8.4-Setup.exe", 3, new string('a', 64),
            "https://github.com/AlexGarciaMartin/MadSense/releases/download/update-preview-0.4.8.4/MadSense-0.4.8.4-Setup.exe"));
        return JsonSerializer.SerializeToUtf8Bytes(new {
            payload = Convert.ToBase64String(bytes),
            signature = Convert.ToBase64String(rsa.SignData(bytes, HashAlgorithmName.SHA256, RSASignaturePadding.Pss))
        });
    }

    sealed class Handler(Func<HttpRequestMessage, HttpResponseMessage> respond) : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token)
        {
            token.ThrowIfCancellationRequested();
            return Task.FromResult(respond(request));
        }
    }

    [TestCase("0.4.7")]
    [TestCase("0.4.7.0")]
    public async Task StableBuildNeverContactsPreview(string version)
    {
        using var client = new HttpClient(new Handler(_ => throw new AssertionException("Stable contacted preview.")));
        Assert.That((await PreviewUpdateChannel.CheckAsync(new(version), client: client)).Status, Is.EqualTo(UpdateCheckStatus.Unavailable));
    }

    [TestCase("0.4.8.3", true)]
    [TestCase("0.4.8.4", false)]
    [TestCase("0.4.8.5", false)]
    public async Task SignedChannelDetectsNewerAndDoesNotOfferDowngrades(string current, bool newer)
    {
        using var key = RSA.Create(2048);
        using var client = new HttpClient(new Handler(request => {
            Assert.That(request.RequestUri, Is.EqualTo(PreviewUpdateChannel.FeedUri));
            return new(HttpStatusCode.OK) { Content = new ByteArrayContent(Descriptor(key)) };
        }));
        var result = await PreviewUpdateChannel.CheckAsync(new(current), client: client, publicKey: key.ExportSubjectPublicKeyInfoPem());
        Assert.That(result.Status, Is.EqualTo(newer ? UpdateCheckStatus.Available : UpdateCheckStatus.Current));
        if (newer) Assert.That(result.Update!.Version, Is.EqualTo("0.4.8.4"));
    }

    [TestCase("signature")]
    [TestCase("channel")]
    [TestCase("oversize")]
    [TestCase("offline")]
    public async Task InvalidOrUnavailableChannelKeepsAppUsable(string failure)
    {
        using var key = RSA.Create(2048);
        using var other = RSA.Create(2048);
        using var client = new HttpClient(new Handler(_ => failure == "offline"
            ? new(HttpStatusCode.ServiceUnavailable)
            : new(HttpStatusCode.OK) { Content = new ByteArrayContent(failure == "oversize" ? new byte[32769]
                : Descriptor(failure == "signature" ? other : key, failure == "channel" ? "stable" : "preview")) }));
        Assert.That((await PreviewUpdateChannel.CheckAsync(new("0.4.8.3"), client: client,
            publicKey: key.ExportSubjectPublicKeyInfoPem())).Status, Is.EqualTo(UpdateCheckStatus.Unavailable));
    }

    [Test]
    public void CancellationIsNotHiddenAsOffline()
    {
        using var client = new HttpClient(new Handler(_ => new(HttpStatusCode.OK)));
        Assert.That(async () => await PreviewUpdateChannel.CheckAsync(new("0.4.8.3"), new CancellationToken(true), client),
            Throws.InstanceOf<OperationCanceledException>());
    }

    [TestCase("https://evil.test/setup.exe", false)]
    [TestCase("https://release-assets.githubusercontent.com/test", true)]
    public async Task RedirectsAreCheckedBeforeContactingDestination(string target, bool accepted)
    {
        int requests = 0;
        using var client = new HttpClient(new Handler(_ => ++requests == 1
            ? new(HttpStatusCode.Found) { Headers = { Location = new Uri(target) } }
            : new(HttpStatusCode.OK) { Content = new ByteArrayContent([1, 2, 3]) }));
        using var stream = new MemoryStream();
        if (accepted)
        {
            await IntegratedUpdate.Fetch(PreviewUpdateChannel.FeedUri, stream, 3, null, default, client);
            Assert.That(stream.ToArray(), Is.EqualTo(new byte[] { 1, 2, 3 }));
        }
        else
        {
            await Assert.ThrowsAsync<InvalidDataException>(async () =>
                await IntegratedUpdate.Fetch(PreviewUpdateChannel.FeedUri, stream, 3, null, default, client));
            Assert.That(requests, Is.EqualTo(1));
        }
    }

    [Test, Explicit("Downloads the real preview package from GitHub; never runs it or opens a controller.")]
    public async Task LivePreviewDiscoveryAndFullDownload()
    {
        using var timeout = new CancellationTokenSource(TimeSpan.FromMinutes(3));
        var offer = await PreviewUpdateChannel.CheckAsync(new("0.4.8.3"), timeout.Token);
        Assert.That(offer.Status, Is.EqualTo(UpdateCheckStatus.Available));
        Assert.That(offer.Update!.Version, Is.EqualTo("0.4.8.4"));
        using var descriptor = new MemoryStream();
        await IntegratedUpdate.Fetch(new Uri(offer.Update.DownloadUri + ".update.json"),
            descriptor, 32768, null, timeout.Token);
        var package = IntegratedUpdate.VerifyDescriptor(descriptor.ToArray(), new("0.4.8.3"), true);
        using var downloaded = new MemoryStream();
        await IntegratedUpdate.Fetch(new Uri(package.DownloadUrl), downloaded, package.Bytes, null, timeout.Token);
        IntegratedUpdate.VerifyPackage(downloaded, package);
        Assert.That((await PreviewUpdateChannel.CheckAsync(new("0.4.8.4"), timeout.Token)).Status,
            Is.EqualTo(UpdateCheckStatus.Current));
        TestContext.Out.WriteLine($"Verified real HTTPS package: {package.Version}, {downloaded.Length} bytes, SHA256 {package.Sha256}");
    }
}
