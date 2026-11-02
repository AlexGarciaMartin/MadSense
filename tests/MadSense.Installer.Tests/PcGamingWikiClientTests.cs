using System.Net;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using MadSense.Games;

namespace MadSense.Installer.Tests;

[TestFixture]
public sealed class PcGamingWikiClientTests
{
    [Test]
    public async Task ResolvesSteamIdParsesExactFieldsAndCachesEvidence()
    {
        using var transport = new FakeTransport(Redirect("/wiki/Example_Game"), Wiki("Example Game", """
            {{Input
            |playstation controllers = true
            |dualsense haptics support = false
            |dualsense adaptive trigger support = limited
            |playstation speaker = hackable
            |playstation motion sensors = n/a
            |light bar support = true <!-- checked by editor -->
            }}
            """));
        using var http = new HttpClient(transport);
        using var client = new PcGamingWikiClient(http, TimeSpan.Zero);

        var result = await client.GetAsync("42");
        var cached = await client.GetAsync("42");

        Assert.Multiple(() =>
        {
            Assert.That(result, Is.Not.Null);
            Assert.That(cached, Is.SameAs(result));
            Assert.That(result!.Title, Is.EqualTo("Example Game"));
            Assert.That(result.Source.AbsoluteUri, Is.EqualTo("https://www.pcgamingwiki.com/wiki/Example_Game"));
            Assert.That(result.Features["controller"], Is.EqualTo(WikiValue.Yes));
            Assert.That(result.Features["haptics"], Is.EqualTo(WikiValue.No));
            Assert.That(result.Features["triggers"], Is.EqualTo(WikiValue.Limited));
            Assert.That(result.Features["speaker"], Is.EqualTo(WikiValue.ModRequired));
            Assert.That(result.Features["gyro"], Is.EqualTo(WikiValue.NotApplicable));
            Assert.That(result.Features["lightbar"], Is.EqualTo(WikiValue.Yes));
            Assert.That(result.Features["touchpad"], Is.EqualTo(WikiValue.Unknown));
            Assert.That(transport.Uris, Has.Count.EqualTo(2));
            Assert.That(transport.Uris[0].AbsoluteUri, Is.EqualTo("https://www.pcgamingwiki.com/api/appid.php?appid=42"));
            Assert.That(transport.Uris[1].AbsoluteUri, Does.Contain("prop=wikitext&page=Example%20Game"));
            Assert.That(transport.UserAgents, Has.All.Contains("github.com/AlexGarciaMartin/MadSense"));
        });
    }

    [Test]
    public async Task DoesNotReadFieldsFromOtherTemplatesCommentsOrNestedNotes()
    {
        const string text = """
            <!-- {{Input|dualsense haptics support=true}} -->
            <nowiki>{{Input|dualsense haptics support=true}}</nowiki>
            {{Infobox game|dualsense haptics support=true}}
            {{Input
            |notes = {{Nested|dualsense haptics support=true|other={{{parameter|value}}}}}}
            |controller notes = [[Test|dualsense haptics support=true]]
            |dualsense haptics support = limited
            |playstation speaker = {{Unknown}}
            }}
            {{Other|dualsense adaptive trigger support=true}}
            """;
        using var transport = new FakeTransport(Redirect("/wiki/Game"), Wiki("Game", text));
        using var http = new HttpClient(transport);
        using var client = new PcGamingWikiClient(http, TimeSpan.Zero);

        var result = await client.GetAsync("42");

        Assert.That(result!.Features["haptics"], Is.EqualTo(WikiValue.Limited));
        Assert.That(result.Features["triggers"], Is.EqualTo(WikiValue.Unknown));
        Assert.That(result.Features["speaker"], Is.EqualTo(WikiValue.Unknown));
    }

    [TestCase("No Input template here.")]
    [TestCase("{{Input|dualsense haptics support=maybe}}")]
    [TestCase("{{Input|dualsense haptics support=true|dualsense haptics support=false}}")]
    [TestCase("{{Input|dualsense haptics support=true")]
    public async Task MissingAmbiguousOrMalformedTemplateIsUnknown(string text)
    {
        using var transport = new FakeTransport(Redirect("/wiki/Game"), Wiki("Game", text));
        using var http = new HttpClient(transport);
        using var client = new PcGamingWikiClient(http, TimeSpan.Zero);

        var result = await client.GetAsync("42");

        Assert.That(result, Is.Not.Null);
        Assert.That(result!.Features.Values, Has.All.EqualTo(WikiValue.Unknown));
    }

    [TestCase("https://example.org/wiki/Game")]
    [TestCase("http://www.pcgamingwiki.com/wiki/Game")]
    [TestCase("https://www.pcgamingwiki.com:8443/wiki/Game")]
    [TestCase("https://someone@www.pcgamingwiki.com/wiki/Game")]
    [TestCase("https://www.pcgamingwiki.com/wiki/Game?redirect=elsewhere")]
    [TestCase("https://www.pcgamingwiki.com/wiki/")]
    [TestCase("/w/index.php?title=Game")]
    public async Task RejectsUnexpectedRedirectWithoutFollowingIt(string location)
    {
        using var transport = new FakeTransport(Redirect(location));
        using var http = new HttpClient(transport);
        using var client = new PcGamingWikiClient(http, TimeSpan.Zero);

        Assert.That(await client.GetAsync("42"), Is.Null);
        Assert.That(transport.Uris, Has.Count.EqualTo(1));
    }

    [TestCase("0")]
    [TestCase("0042")]
    [TestCase("42&other=1")]
    [TestCase("-42")]
    [TestCase("4294967296")]
    [TestCase("")]
    public async Task InvalidSteamIdMakesNoRequest(string appId)
    {
        using var transport = new FakeTransport();
        using var http = new HttpClient(transport);
        using var client = new PcGamingWikiClient(http, TimeSpan.Zero);

        Assert.That(await client.GetAsync(appId), Is.Null);
        Assert.That(transport.Uris, Is.Empty);
    }

    [Test]
    public async Task NotFoundIsCachedWithoutTurningItIntoNegativeFeatureClaims()
    {
        using var transport = new FakeTransport(new HttpResponseMessage(HttpStatusCode.NotFound));
        using var http = new HttpClient(transport);
        using var client = new PcGamingWikiClient(http, TimeSpan.Zero);

        Assert.That(await client.GetAsync("42"), Is.Null);
        Assert.That(await client.GetAsync("42"), Is.Null);
        Assert.That(transport.Uris, Has.Count.EqualTo(1));
    }

    [TestCase(HttpStatusCode.ServiceUnavailable)]
    public async Task TransientServerFailuresStopImmediatelyAndAreNotCached(HttpStatusCode status)
    {
        using var transport = new FakeTransport(new HttpResponseMessage(status), Redirect("/wiki/Game"), Wiki("Game", "{{Input|playstation controllers=true}}"));
        using var http = new HttpClient(transport);
        using var client = new PcGamingWikiClient(http, TimeSpan.Zero);

        Assert.That(await client.GetAsync("42"), Is.Null);
        Assert.That(transport.Uris, Has.Count.EqualTo(1));
        Assert.That(await client.GetAsync("42"), Is.Not.Null);
        Assert.That(transport.Uris, Has.Count.EqualTo(3));
    }

    [TestCase(HttpStatusCode.Forbidden)]
    [TestCase(HttpStatusCode.TooManyRequests)]
    public async Task AccessOrRateLimitFailureBacksOffAcrossDifferentGames(HttpStatusCode status)
    {
        using var transport = new FakeTransport(new HttpResponseMessage(status));
        using var http = new HttpClient(transport);
        using var client = new PcGamingWikiClient(http, TimeSpan.Zero);

        Assert.That(await client.GetAsync("42"), Is.Null);
        Assert.That(await client.GetAsync("43"), Is.Null);
        Assert.That(await client.GetAsync("42"), Is.Null);
        Assert.That(transport.Uris, Has.Count.EqualTo(1));
    }

    [Test]
    public async Task BackoffKeepsPreviouslyCachedEvidenceAvailable()
    {
        using var transport = new FakeTransport(Redirect("/wiki/Game"), Wiki("Game", "{{Input|playstation controllers=true}}"),
            new HttpResponseMessage(HttpStatusCode.Forbidden));
        using var http = new HttpClient(transport);
        using var client = new PcGamingWikiClient(http, TimeSpan.Zero);

        var evidence = await client.GetAsync("42");
        Assert.That(evidence, Is.Not.Null);
        Assert.That(await client.GetAsync("43"), Is.Null);
        Assert.That(await client.GetAsync("42"), Is.SameAs(evidence));
        Assert.That(await client.GetAsync("44"), Is.Null);
        Assert.That(transport.Uris, Has.Count.EqualTo(3));
    }

    [TestCase("{")]
    [TestCase("null")]
    [TestCase("{\"parse\":{\"title\":12,\"wikitext\":{\"*\":\"text\"}}}")]
    [TestCase("{\"parse\":{\"title\":\"Game\",\"wikitext\":null}}")]
    public async Task MalformedResponseFailsWithoutPoisoningCache(string body)
    {
        using var transport = new FakeTransport(Redirect("/wiki/Game"), Json(body), Redirect("/wiki/Game"), Wiki("Game", "{{Input|playstation controllers=true}}"));
        using var http = new HttpClient(transport);
        using var client = new PcGamingWikiClient(http, TimeSpan.Zero);

        Assert.That(await client.GetAsync("42"), Is.Null);
        Assert.That(await client.GetAsync("42"), Is.Not.Null);
    }

    [Test]
    public async Task MissingTitleApiErrorIsCached()
    {
        using var transport = new FakeTransport(Redirect("/wiki/Game"), Json("{\"error\":{\"code\":\"missingtitle\"}}"));
        using var http = new HttpClient(transport);
        using var client = new PcGamingWikiClient(http, TimeSpan.Zero);

        Assert.That(await client.GetAsync("42"), Is.Null);
        Assert.That(await client.GetAsync("42"), Is.Null);
        Assert.That(transport.Uris, Has.Count.EqualTo(2));
    }

    [Test]
    public async Task RejectsOversizedBodyEvenWithoutContentLength()
    {
        var oversized = Json(new string(' ', 1024 * 1024 + 1));
        oversized.Content = new StreamContent(new NonSeekableStream(new byte[1024 * 1024 + 1]));
        using var transport = new FakeTransport(Redirect("/wiki/Game"), oversized);
        using var http = new HttpClient(transport);
        using var client = new PcGamingWikiClient(http, TimeSpan.Zero);

        Assert.That(await client.GetAsync("42"), Is.Null);
    }

    [Test]
    public async Task ConcurrentLookupsForTheSameGameShareOneCompletedResult()
    {
        using var transport = new FakeTransport(Redirect("/wiki/Game"), Wiki("Game", "{{Input|playstation controllers=true}}"));
        using var http = new HttpClient(transport);
        using var client = new PcGamingWikiClient(http, TimeSpan.FromMilliseconds(10));

        var results = await Task.WhenAll(client.GetAsync("42"), client.GetAsync("42"));

        Assert.That(results[0], Is.SameAs(results[1]));
        Assert.That(transport.Uris, Has.Count.EqualTo(2));
    }

    [Test]
    public async Task CallerCancellationPropagates()
    {
        using var transport = new FakeTransport();
        using var http = new HttpClient(transport);
        using var client = new PcGamingWikiClient(http, TimeSpan.Zero);
        using var cancellation = new CancellationTokenSource();
        cancellation.Cancel();

        await Assert.ThrowsAsync<OperationCanceledException>(async () => await client.GetAsync("42", cancellation.Token));
        Assert.That(transport.Uris, Is.Empty);
    }

    [Test]
    public async Task CancellationDuringRateLimitWaitPreventsTheNextRequest()
    {
        using var transport = new FakeTransport(Redirect("/wiki/Game"));
        using var http = new HttpClient(transport);
        using var client = new PcGamingWikiClient(http, TimeSpan.FromSeconds(2.1));
        using var cancellation = new CancellationTokenSource();

        var lookup = client.GetAsync("42", cancellation.Token);
        await transport.FirstRequest.Task;
        cancellation.Cancel();

        await Assert.CatchAsync<OperationCanceledException>(async () => await lookup);
        Assert.That(transport.Uris, Has.Count.EqualTo(1));
    }

    static HttpResponseMessage Redirect(string location)
    {
        var response = new HttpResponseMessage(HttpStatusCode.MovedPermanently);
        response.Headers.Location = new Uri(location, UriKind.RelativeOrAbsolute);
        return response;
    }

    static HttpResponseMessage Wiki(string title, string wikitext) => Json(JsonSerializer.Serialize(new
    {
        parse = new { title, wikitext = new Dictionary<string, string> { ["*"] = wikitext } }
    }));

    static HttpResponseMessage Json(string body) => new(HttpStatusCode.OK)
    {
        Content = new StringContent(body, Encoding.UTF8, "application/json")
    };

    sealed class FakeTransport(params HttpResponseMessage[] responses) : HttpMessageHandler
    {
        readonly Queue<HttpResponseMessage> replies = new(responses);
        public List<Uri> Uris { get; } = new();
        public List<string> UserAgents { get; } = new();
        public TaskCompletionSource FirstRequest { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
        {
            cancellationToken.ThrowIfCancellationRequested();
            Uris.Add(request.RequestUri!);
            UserAgents.Add(request.Headers.UserAgent.ToString());
            FirstRequest.TrySetResult();
            return Task.FromResult(replies.Dequeue());
        }
    }

    sealed class NonSeekableStream(byte[] bytes) : MemoryStream(bytes)
    {
        public override bool CanSeek => false;
    }
}
