using System.Text.Json;
using MadSense;
using NUnit.Framework;

namespace MadSense.Linux.App.Tests;

public sealed class LinuxUpdateTests
{
    static object Release(string tag, string filename, string? url = null, bool prerelease = false) => new
    {
        draft = false, prerelease, tag_name = tag,
        html_url = $"https://github.com/AlexGarciaMartin/MadSense/releases/tag/{tag}",
        assets = new[] { new { name = filename, browser_download_url = url ?? $"https://github.com/AlexGarciaMartin/MadSense/releases/download/{tag}/{filename}" } }
    };
    static LinuxUpdateResult Parse(params object[] releases)
    {
        using var document = JsonDocument.Parse(JsonSerializer.Serialize(releases));
        return LinuxUpdates.Parse(document.RootElement, new Version(0, 5, 0), "x64");
    }
    [Test]
    public void WindowsReleasesDoNotBecomeLinuxUpdates() => Assert.That(Parse(Release("v9.0.0", "MadSense-9.0.0-Setup.exe")).HasLinuxRelease, Is.False);
    [Test]
    public void ExactArchitectureAndRepositoryAreRequired()
    {
        Assert.That(Parse(Release("linux-v0.6.0", "MadSense-0.6.0-linux-arm64.tar.gz")).HasLinuxRelease, Is.False);
        Assert.That(Parse(Release("linux-v0.6.0", "MadSense-0.6.0-linux-x64.tar.gz", "https://example.com/package.tar.gz")).HasLinuxRelease, Is.False);
        Assert.That(Parse(Release("linux-v0.6.0", "MadSense-0.6.0-linux-x64.tar.gz", prerelease: true)).HasLinuxRelease, Is.False);
    }
    [Test]
    public void NewestVerifiedLinuxAssetWinsAndCurrentIsDistinguishedFromMissing()
    {
        var result = Parse(Release("linux-v0.6.0", "MadSense-0.6.0-linux-x64.tar.gz"), Release("linux-v0.7.0", "MadSense-0.7.0-linux-x64.tar.gz"));
        Assert.That(result.Update?.Version, Is.EqualTo(new Version(0, 7, 0)));
        var current = Parse(Release("linux-v0.5.0", "MadSense-0.5.0-linux-x64.tar.gz"));
        Assert.That(current.HasLinuxRelease, Is.True); Assert.That(current.Update, Is.Null);
        Assert.That(Parse().HasLinuxRelease, Is.False);
    }

    [Test]
    public void DevelopmentBuildCanMoveToItsFirstStableVersionWithoutDowngrading()
    {
        using var same = JsonDocument.Parse(JsonSerializer.Serialize(new[]
        { Release("linux-v0.5.0", "MadSense-0.5.0-linux-x64.tar.gz") }));
        Assert.That(LinuxUpdates.Parse(same.RootElement, new Version(0, 5, 0), "x64", developmentBuild: true)
            .Update?.Version, Is.EqualTo(new Version(0, 5, 0)));
        Assert.That(LinuxUpdates.Parse(same.RootElement, new Version(0, 5, 0), "x64").Update, Is.Null);
        Assert.That(LinuxUpdates.Parse(same.RootElement, new Version(0, 6, 0), "x64", developmentBuild: true).Update, Is.Null);
        using var preview = JsonDocument.Parse(JsonSerializer.Serialize(new[]
        { Release("linux-v0.5.0-alpha.1", "MadSense-0.5.0-linux-x64.tar.gz", prerelease: true) }));
        Assert.That(LinuxUpdates.Parse(preview.RootElement, new Version(0, 5, 0), "x64", developmentBuild: true)
            .HasLinuxRelease, Is.False);
    }
}
