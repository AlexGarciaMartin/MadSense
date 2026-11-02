using System.Net;
using System.Net.Http;
using System.Text;
using MadSense.Games;

namespace MadSense.Installer.Tests;

[TestFixture]
public sealed class ModDiscoveryClientTests
{
    // Reduced fixtures preserve the shapes of the upstream README sections inspected
    // on 2026-11-02; tests never contact a source or install a mod.
    const string Paliverse = """
        ## **Screenshot:**
        [Outside](https://github.com/example/outside)
        ## **Game Mods with DualSenseX**
        List of available Game Mods using DualSenseX to communicate with the controller:
        [DualSenseAT Central Hub Mod App](https://github.com/josealissonbr/DualSenseAT)
        ```
        Central Hub Mod App: DualSenseAT
        Game: Many games supported
        [Not a listed mod](https://github.com/example/code)
        ```
        [Cyberpunk 2077 Mod](https://www.nexusmods.com/cyberpunk2077/mods/4156?tab=description)
        [Duck Game Mod](https://steamcommunity.com/sharedfiles/filedetails/?id=2654985616)
        # **For Game Devs**
        [Example ZIP](https://github.com/Paliverse/DualSenseX/raw/main/UDPExample.zip)
        """;

    const string Dsz = """
        ### Automatic DualSense switching
        [Not a mod](https://github.com/example/other)
        ### Mod compatibility
        Trigger modes follow [generator](https://github.com/example/generator).
        | Mod | Game | License |
        |---|---|---|
        | [dvize/TarkovDSX](https://github.com/dvize/TarkovDSX) | Escape from Tarkov | MIT |
        | [SYSTEMATI0N/WF2_DSX](https://github.com/SYSTEMATI0N/WF2_DSX) | Wreckfest 2 | MIT |
        | [tpetsas/DSXpp](https://github.com/tpetsas/DSXpp) | C++ client library for mods | MIT |
        | [Tio-Henry/DualSenseX-Support](https://github.com/Tio-Henry/DualSenseX-Support) | Godot engine add-on | MIT |
        ### Drivers
        | Mod | Game | License |
        |---|---|---|
        | [Not a mod](https://github.com/example/driver) | Driver | MIT |
        """;

    [Test]
    public void PaliverseImportsOnlyListedLinksInTheSpecificSection()
    {
        var items = ModDiscoveryClient.ParsePaliverse(Paliverse)!;
        Assert.That(items.Select(item => item.Name), Is.EqualTo(new[]
        {
            "DualSenseAT Central Hub Mod App", "Cyberpunk 2077 Mod", "Duck Game Mod",
        }));
        Assert.That(items.All(item => item.SourceName == "Paliverse"), Is.True);
        Assert.That(items[2].Page.Query, Is.EqualTo("?id=2654985616"), "Workshop identity is in the query.");
    }

    [Test]
    public void DszImportsModRowsButExcludesLibrariesAndNarrativeLinks()
    {
        var items = ModDiscoveryClient.ParseDsz(Dsz)!;
        Assert.That(items.Select(item => item.Name), Is.EqualTo(new[] { "dvize/TarkovDSX", "SYSTEMATI0N/WF2_DSX" }));
        Assert.That(items.All(item => item.SourceName == "DSZ"), Is.True);
    }

    [Test]
    public void MissingSectionAndAnIntentionallyEmptySectionAreDifferent()
    {
        Assert.That(ModDiscoveryClient.ParsePaliverse("# New document\nNo list here"), Is.Null);
        Assert.That(ModDiscoveryClient.ParseDsz("# Mod support has moved"), Is.Null);
        Assert.That(ModDiscoveryClient.ParsePaliverse("## Game Mods with DualSenseX\nNo mods listed.\n## Next"), Is.Empty);
        Assert.That(ModDiscoveryClient.ParseDsz("### Mod compatibility\n| Mod | Game | License |\n|---|---|---|\n### Next"), Is.Empty);
    }

    [TestCase("http://github.com/example/mod")]
    [TestCase("javascript:alert(1)")]
    [TestCase("file:///C:/Windows/notepad.exe")]
    [TestCase("https://github.com.evil.example/example/mod")]
    [TestCase("https://github.com@evil.example/example/mod")]
    [TestCase("https://user:password@github.com/example/mod")]
    [TestCase("https://github.com:8443/example/mod")]
    [TestCase("https://dualsensex.com/mod")]
    [TestCase("https://dualsensex.org/mod")]
    public void RejectsUntrustedPageAddresses(string address)
    {
        Assert.That(ModDiscoveryClient.ParsePaliverse($"## Game Mods with DualSenseX\n[Mod]({address})"), Is.Empty);
    }

    [Test]
    public void NormalizesGithubRepositoryIdentityAndDeduplicatesIt()
    {
        var items = ModDiscoveryClient.ParsePaliverse("""
            ## Game Mods with DualSenseX
            [First](https://github.com/owner/Mod)
            [Duplicate](https://github.com/OWNER/mod.git/)
            [Readme](https://github.com/owner/mod/blob/main/README.md#usage)
            """);
        Assert.That(items, Has.Count.EqualTo(1));
        Assert.That(items![0].Name, Is.EqualTo("First"));
        Assert.That(items[0].Page.AbsoluteUri, Is.EqualTo("https://github.com/owner/Mod"));
    }

    [Test]
    public async Task ASourceFailureStillReturnsOtherSourcesAndSendsIdentifiableRequests()
    {
        using var handler = new SourceHandler((request, _) => request.RequestUri!.AbsolutePath.Contains("Paliverse")
            ? Reply(HttpStatusCode.ServiceUnavailable, "Unavailable")
            : Reply(HttpStatusCode.OK, Dsz));
        using var http = new HttpClient(handler);
        using var client = new ModDiscoveryClient(http);
        var result = await client.GetAsync();
        Assert.That(result, Has.Count.EqualTo(2));
        Assert.That(handler.Requests, Has.Count.EqualTo(2));
        Assert.That(handler.Requests.All(request => request.Host == "raw.githubusercontent.com"
            && request.Agent.Contains("MadSense/") && request.Agent.Contains("github.com/AlexGarciaMartin/MadSense")), Is.True);
    }

    [Test]
    public async Task BothFailuresReturnNullButValidEmptySourcesReturnEmpty()
    {
        using var failingHttp = new HttpClient(new SourceHandler((_, _) => Reply(HttpStatusCode.Forbidden, "Blocked")));
        using var failing = new ModDiscoveryClient(failingHttp);
        Assert.That(await failing.GetAsync(), Is.Null);
        using var emptyHttp = new HttpClient(new SourceHandler((request, _) => Reply(HttpStatusCode.OK,
            request.RequestUri!.AbsolutePath.Contains("Paliverse") ? "## Game Mods with DualSenseX\n" : "### Mod compatibility\n")));
        using var empty = new ModDiscoveryClient(emptyHttp);
        Assert.That(await empty.GetAsync(), Is.Empty);
    }

    [Test]
    public async Task MergesSourcesByCanonicalRepositoryUrl()
    {
        using var http = new HttpClient(new SourceHandler((request, _) => Reply(HttpStatusCode.OK,
            request.RequestUri!.AbsolutePath.Contains("Paliverse")
                ? "## Game Mods with DualSenseX\n[Listed first](https://github.com/dvize/tarkovdsx.git/)" : Dsz)));
        using var client = new ModDiscoveryClient(http);
        var result = await client.GetAsync();
        Assert.That(result, Has.Count.EqualTo(2));
        Assert.That(result![0].SourceName, Is.EqualTo("Paliverse"));
    }

    [Test]
    public async Task ARefreshReflectsUpstreamRemovalInsteadOfKeepingOldEntries()
    {
        int paliverseReads = 0;
        using var http = new HttpClient(new SourceHandler((request, _) => Reply(HttpStatusCode.OK,
            request.RequestUri!.AbsolutePath.Contains("Paliverse")
                ? (++paliverseReads == 1 ? Paliverse : "## Game Mods with DualSenseX\n")
                : "### Mod compatibility\n")));
        using var client = new ModDiscoveryClient(http);
        Assert.That(await client.GetAsync(), Has.Count.EqualTo(3));
        Assert.That(await client.GetAsync(), Is.Empty);
    }

    [Test]
    public async Task RefusesOversizedResponsesEvenWithoutContentLength()
    {
        using var http = new HttpClient(new SourceHandler((_, _) => new HttpResponseMessage(HttpStatusCode.OK)
        {
            Content = new UnknownLengthContent(new byte[ModDiscoveryClient.MaximumResponseBytes + 1]),
        }));
        using var client = new ModDiscoveryClient(http);
        Assert.That(await client.GetAsync(), Is.Null);
    }

    [Test]
    public async Task RejectsHtmlAndRedirectResponsesInsteadOfParsingUnrelatedContent()
    {
        using var http = new HttpClient(new SourceHandler((request, _) =>
        {
            if (request.RequestUri!.AbsolutePath.Contains("Paliverse"))
                return new HttpResponseMessage(HttpStatusCode.Found) { Headers = { Location = new Uri("https://example.com/") } };
            return new HttpResponseMessage(HttpStatusCode.OK) { Content = new StringContent(Paliverse, Encoding.UTF8, "text/html") };
        }));
        using var client = new ModDiscoveryClient(http);
        Assert.That(await client.GetAsync(), Is.Null);
    }

    [Test]
    public void CallerCancellationIsNotReportedAsAFeedFailure()
    {
        using var http = new HttpClient(new SourceHandler((_, _) => Reply(HttpStatusCode.OK, Paliverse)));
        using var client = new ModDiscoveryClient(http);
        using var cancellation = new CancellationTokenSource();
        cancellation.Cancel();
        Assert.ThrowsAsync<OperationCanceledException>(() => client.GetAsync(cancellation.Token));
    }

    static HttpResponseMessage Reply(HttpStatusCode status, string body) => new(status)
    {
        Content = new StringContent(body, Encoding.UTF8, "text/plain"),
    };

    sealed class SourceHandler(Func<HttpRequestMessage, CancellationToken, HttpResponseMessage> reply) : HttpMessageHandler
    {
        public List<(string Host, string Agent)> Requests { get; } = [];
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
        {
            Requests.Add((request.RequestUri!.Host, request.Headers.UserAgent.ToString()));
            return Task.FromResult(reply(request, cancellationToken));
        }
    }

    sealed class UnknownLengthContent(byte[] bytes) : HttpContent
    {
        protected override bool TryComputeLength(out long length) { length = 0; return false; }
        protected override Task SerializeToStreamAsync(Stream stream, TransportContext? context) => stream.WriteAsync(bytes).AsTask();
        protected override Task<Stream> CreateContentReadStreamAsync() => Task.FromResult<Stream>(new MemoryStream(bytes, false));
    }
}
