using System.Net;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using MadSense;

namespace MadSense.Installer.Tests;

[TestFixture]
public sealed class UpdateCheckerTests
{
    static string Manifest(string version = "0.4.5", string? field = null, object? replacement = null)
    {
        var values = new Dictionary<string, object?>
        {
            ["version"] = version,
            ["filename"] = $"MadSense-{version}-Setup.exe",
            ["downloadUrl"] = $"https://github.com/AlexGarciaMartin/MadSense/releases/download/v{version}/MadSense-{version}-Setup.exe",
            ["releaseUrl"] = $"https://github.com/AlexGarciaMartin/MadSense/releases/tag/v{version}"
        };
        if (field is not null) values[field] = replacement;
        return JsonSerializer.Serialize(values);
    }

    static async Task<UpdateCheckResult> Check(string body, string current = "0.4.4.0")
    {
        using var client = new HttpClient(new StubHandler((_, _) => Task.FromResult(
            new HttpResponseMessage(HttpStatusCode.OK) { Content = new StringContent(body, Encoding.UTF8, "application/json") })));
        return await new UpdateChecker(client).CheckAsync(new Version(current));
    }

    [Test]
    public async Task NewerStableReleaseOffersOnlyTheOfficialDownload()
    {
        var result = await Check(Manifest());
        Assert.Multiple(() =>
        {
            Assert.That(result.Status, Is.EqualTo(UpdateCheckStatus.Available));
            Assert.That(result.Update?.Version, Is.EqualTo("0.4.5"));
            Assert.That(result.Update?.DownloadUri.AbsoluteUri, Is.EqualTo("https://github.com/AlexGarciaMartin/MadSense/releases/download/v0.4.5/MadSense-0.4.5-Setup.exe"));
            Assert.That(result.Update?.ReleaseUri.AbsoluteUri, Is.EqualTo("https://github.com/AlexGarciaMartin/MadSense/releases/tag/v0.4.5"));
        });
    }

    [TestCase("0.4.4", "0.4.4.0", false)]
    [TestCase("0.4.4", "0.4.4.12", false)]
    [TestCase("0.4.3", "0.4.4.0", false)]
    [TestCase("0.4.10", "0.4.9.0", true)]
    [TestCase("0.10.0", "0.9.9.0", true)]
    [TestCase("1.0.0", "0.4.4.0", true)]
    [TestCase("0.4.0", "0.4", false)]
    public async Task VersionsAreComparedNumericallyWithoutAssemblyRevision(string remote, string current, bool newer)
    {
        var expected = newer ? UpdateCheckStatus.Available : UpdateCheckStatus.Current;
        var result = await Check(Manifest(remote), current);
        Assert.That(result.Status, Is.EqualTo(expected));
        if (expected == UpdateCheckStatus.Current) Assert.That(result.Update, Is.Null);
    }

    [TestCase("0.4.5-beta")]
    [TestCase("0.4.5+build")]
    [TestCase("v0.4.5")]
    [TestCase("0.4")]
    [TestCase("0.4.5.0")]
    [TestCase("00.4.5")]
    [TestCase("0.04.5")]
    [TestCase(" 0.4.5")]
    [TestCase("0.4.5 ")]
    [TestCase("-1.4.5")]
    [TestCase("2147483648.0.0")]
    public async Task NonCanonicalOrPrereleaseVersionsAreRejected(string version)
        => Assert.That((await Check(Manifest(version))).Status, Is.EqualTo(UpdateCheckStatus.Unavailable));

    [TestCase("filename", "MadSense-0.4.5-Setup-TEST.exe")]
    [TestCase("filename", "MadSense-0.4.4-Setup.exe")]
    [TestCase("filename", "../MadSense-0.4.5-Setup.exe")]
    [TestCase("downloadUrl", "http://github.com/AlexGarciaMartin/MadSense/releases/download/v0.4.5/MadSense-0.4.5-Setup.exe")]
    [TestCase("downloadUrl", "https://evil.test/MadSense-0.4.5-Setup.exe")]
    [TestCase("downloadUrl", "https://github.com.evil.test/AlexGarciaMartin/MadSense/releases/download/v0.4.5/MadSense-0.4.5-Setup.exe")]
    [TestCase("downloadUrl", "https://github.com@evil.test/AlexGarciaMartin/MadSense/releases/download/v0.4.5/MadSense-0.4.5-Setup.exe")]
    [TestCase("downloadUrl", "https://github.com/other/MadSense/releases/download/v0.4.5/MadSense-0.4.5-Setup.exe")]
    [TestCase("downloadUrl", "https://github.com/AlexGarciaMartin/MadSense/releases/download/v0.4.5/MadSense-0.4.5-Setup.exe?redirect=evil")]
    [TestCase("downloadUrl", "file:///C:/evil.exe")]
    [TestCase("releaseUrl", "https://evil.test")]
    [TestCase("releaseUrl", "https://github.com/AlexGarciaMartin/MadSense/releases/tag/v0.4.5#anything")]
    public async Task ManifestCannotSubstituteDownloadOrReleaseTargets(string field, string replacement)
        => Assert.That((await Check(Manifest(field: field, replacement: replacement))).Status, Is.EqualTo(UpdateCheckStatus.Unavailable));

    [TestCase("{")]
    [TestCase("null")]
    [TestCase("[]")]
    [TestCase("42")]
    [TestCase("{}")]
    [TestCase("{\"version\":\"0.4.5\"}")]
    public async Task InvalidOrIncompleteJsonIsUnavailable(string body)
        => Assert.That((await Check(body)).Status, Is.EqualTo(UpdateCheckStatus.Unavailable));

    [TestCase("version")]
    [TestCase("filename")]
    [TestCase("downloadUrl")]
    [TestCase("releaseUrl")]
    public async Task WrongFieldTypesAreUnavailable(string field)
        => Assert.That((await Check(Manifest(field: field, replacement: 42))).Status, Is.EqualTo(UpdateCheckStatus.Unavailable));

    [TestCase("version")]
    [TestCase("filename")]
    [TestCase("downloadUrl")]
    [TestCase("releaseUrl")]
    public async Task DuplicateSecurityRelevantFieldsAreRejected(string field)
    {
        var json = Manifest();
        using var original = JsonDocument.Parse(json);
        var value = original.RootElement.GetProperty(field).GetRawText();
        var duplicate = json[..^1] + $",\"{field}\":{value}}}";
        Assert.That((await Check(duplicate)).Status, Is.EqualTo(UpdateCheckStatus.Unavailable));
    }

    [Test]
    public async Task RequestUsesOnlyFixedAnonymousManifestEndpoint()
    {
        using var client = new HttpClient(new StubHandler((request, _) =>
        {
            Assert.Multiple(() =>
            {
                Assert.That(request.Method, Is.EqualTo(HttpMethod.Get));
                Assert.That(request.RequestUri, Is.EqualTo(new Uri("https://madsense.pro/downloads/release.json")));
                Assert.That(request.Headers.Authorization, Is.Null);
                Assert.That(request.Content, Is.Null);
                Assert.That(request.Headers.UserAgent.ToString(), Is.EqualTo("MadSense-UpdateCheck"));
            });
            return Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK) { Content = new StringContent(Manifest()) });
        }));
        Assert.That((await new UpdateChecker(client).CheckAsync(new Version(0, 4, 4))).Status, Is.EqualTo(UpdateCheckStatus.Available));
    }

    [TestCase(HttpStatusCode.NotFound)]
    [TestCase(HttpStatusCode.TooManyRequests)]
    [TestCase(HttpStatusCode.InternalServerError)]
    [TestCase(HttpStatusCode.Redirect)]
    public async Task FailedOrRedirectedRequestsAreUnavailable(HttpStatusCode status)
    {
        using var client = new HttpClient(new StubHandler((_, _) => Task.FromResult(
            new HttpResponseMessage(status) { Content = new StringContent(Manifest()) })));
        Assert.That((await new UpdateChecker(client).CheckAsync(new Version(0, 4, 4))).Status, Is.EqualTo(UpdateCheckStatus.Unavailable));
    }

    [Test]
    public async Task OfflineDoesNotThrow()
    {
        using var client = new HttpClient(new StubHandler((_, _) => throw new HttpRequestException("Offline")));
        Assert.That((await new UpdateChecker(client).CheckAsync(new Version(0, 4, 4))).Status, Is.EqualTo(UpdateCheckStatus.Unavailable));
    }

    [Test]
    public async Task RequestTimeoutDoesNotThrow()
    {
        using var client = new HttpClient(new StubHandler((_, _) => throw new TaskCanceledException("Timeout")));
        Assert.That((await new UpdateChecker(client).CheckAsync(new Version(0, 4, 4))).Status, Is.EqualTo(UpdateCheckStatus.Unavailable));
    }

    [Test]
    public void CallerCancellationIsPropagated()
    {
        using var cancellation = new CancellationTokenSource();
        using var client = new HttpClient(new StubHandler(async (_, token) =>
        {
            cancellation.Cancel();
            await Task.Delay(Timeout.Infinite, token);
            return new HttpResponseMessage(HttpStatusCode.OK);
        }));
        Assert.That(async () => await new UpdateChecker(client).CheckAsync(new Version(0, 4, 4), cancellation.Token), Throws.InstanceOf<OperationCanceledException>());
    }

    [Test]
    public void AlreadyCancelledCheckDoesNotSendRequest()
    {
        using var cancellation = new CancellationTokenSource();
        cancellation.Cancel();
        using var client = new HttpClient(new StubHandler((_, _) => throw new AssertionException("Request should not be sent")));
        Assert.That(async () => await new UpdateChecker(client).CheckAsync(new Version(0, 4, 4), cancellation.Token), Throws.InstanceOf<OperationCanceledException>());
    }

    [Test]
    public async Task OversizeDeclaredResponseIsRejected()
        => Assert.That((await Check(Manifest() + new string(' ', UpdateChecker.MaximumManifestBytes))).Status, Is.EqualTo(UpdateCheckStatus.Unavailable));

    [Test]
    public async Task OversizeChunkedResponseIsRejectedWithoutReadingUnboundedBytes()
    {
        using var source = new UnknownLengthStream(Encoding.UTF8.GetBytes(Manifest() + new string(' ', UpdateChecker.MaximumManifestBytes * 2)));
        using var client = new HttpClient(new StubHandler((_, _) => Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK)
        {
            Content = new StreamContent(source)
        })));
        var result = await new UpdateChecker(client).CheckAsync(new Version(0, 4, 4));
        Assert.Multiple(() =>
        {
            Assert.That(result.Status, Is.EqualTo(UpdateCheckStatus.Unavailable));
            Assert.That(source.BytesRead, Is.EqualTo(UpdateChecker.MaximumManifestBytes + 1));
        });
    }

    sealed class StubHandler(Func<HttpRequestMessage, CancellationToken, Task<HttpResponseMessage>> handler) : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
            => handler(request, cancellationToken);
    }

    sealed class UnknownLengthStream(byte[] bytes) : MemoryStream(bytes)
    {
        public int BytesRead { get; private set; }
        public override bool CanSeek => false;
        public override async ValueTask<int> ReadAsync(Memory<byte> buffer, CancellationToken cancellationToken = default)
        {
            var read = await base.ReadAsync(buffer, cancellationToken);
            BytesRead += read;
            return read;
        }
    }
}
