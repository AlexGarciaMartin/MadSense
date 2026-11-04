using System.Security.Cryptography;
using System.Text.Json;
using MadSense;

namespace MadSense.Installer.Tests;

public class StableIntegratedUpdateTests
{
    [Test]
    public void StableDescriptorWorksWithAssemblyRevisionZeroAndRejectsPreviewOrReinstall()
    {
        using var key = RSA.Create(2048);
        var package = new UpdatePackage("0.4.9", "stable", "MadSense-0.4.9-Setup.exe", 3,
            new string('a', 64),
            "https://github.com/AlexGarciaMartin/MadSense/releases/download/v0.4.9/MadSense-0.4.9-Setup.exe");
        var payload = JsonSerializer.SerializeToUtf8Bytes(package);
        var envelope = JsonSerializer.SerializeToUtf8Bytes(new {
            payload = Convert.ToBase64String(payload),
            signature = Convert.ToBase64String(key.SignData(payload, HashAlgorithmName.SHA256, RSASignaturePadding.Pss))
        });
        Assert.That(IntegratedUpdate.VerifyDescriptor(envelope, new("0.4.8.0"), false,
            key.ExportSubjectPublicKeyInfoPem()), Is.EqualTo(package));
        Assert.Throws<InvalidDataException>(() => IntegratedUpdate.VerifyDescriptor(envelope,
            new("0.4.8.4"), true, key.ExportSubjectPublicKeyInfoPem()));
        Assert.Throws<InvalidDataException>(() => IntegratedUpdate.VerifyDescriptor(envelope,
            new("0.4.9.0"), false, key.ExportSubjectPublicKeyInfoPem()));
    }

    [Test, Explicit("Reads the public stable feed and downloads its installer without executing it.")]
    public async Task LiveStableDiscoveryAndFullDownload()
    {
        using var timeout = new CancellationTokenSource(TimeSpan.FromMinutes(3));
        var checker = new UpdateChecker();
        var offer = await checker.CheckAsync(new("0.4.8.0"), timeout.Token);
        Assert.That(offer.Status, Is.EqualTo(UpdateCheckStatus.Available));
        Assert.That(offer.Update!.Version, Is.EqualTo("0.4.9"));
        using var descriptor = new MemoryStream();
        await IntegratedUpdate.Fetch(new Uri(offer.Update.DownloadUri + ".update.json"),
            descriptor, 32768, null, timeout.Token);
        var package = IntegratedUpdate.VerifyDescriptor(descriptor.ToArray(), new("0.4.8.0"), false);
        using var bytes = new MemoryStream();
        await IntegratedUpdate.Fetch(new Uri(package.DownloadUrl), bytes, package.Bytes, null, timeout.Token);
        IntegratedUpdate.VerifyPackage(bytes, package);
        Assert.That((await checker.CheckAsync(new("0.4.9.0"), timeout.Token)).Status, Is.EqualTo(UpdateCheckStatus.Current));
        TestContext.Out.WriteLine($"Stable HTTP download and signature verified: {package.Version}, {bytes.Length} bytes, {package.Sha256}");
    }
}
