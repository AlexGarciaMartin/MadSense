using System.Security.Cryptography;
using System.Text.Json;
using MadSense;

namespace MadSense.Installer.Tests;

public class IntegratedUpdateTests
{
    static readonly Version Current = new("0.4.8.1");
    static UpdatePackage Package => new("0.4.8.2", "preview", "MadSense-0.4.8.2-Setup.exe", 3,
        Convert.ToHexString(SHA256.HashData(new byte[] { 1, 2, 3 })),
        "https://github.com/AlexGarciaMartin/MadSense/releases/download/update-preview-0.4.8.2/MadSense-0.4.8.2-Setup.exe");
    static byte[] Sign(UpdatePackage package, RSA rsa)
    {
        var payload = JsonSerializer.SerializeToUtf8Bytes(package);
        return JsonSerializer.SerializeToUtf8Bytes(new { payload = Convert.ToBase64String(payload),
            signature = Convert.ToBase64String(rsa.SignData(payload, HashAlgorithmName.SHA256, RSASignaturePadding.Pss)) });
    }

    [Test]
    public void ValidSignatureAllowsOnlyExplicitPreviewAndNewerVersion()
    {
        using var rsa = RSA.Create(2048);
        var signed = Sign(Package, rsa);
        Assert.That(IntegratedUpdate.VerifyDescriptor(signed, Current, true, rsa.ExportSubjectPublicKeyInfoPem()), Is.EqualTo(Package));
        Assert.Throws<InvalidDataException>(() => IntegratedUpdate.VerifyDescriptor(signed, Current, false, rsa.ExportSubjectPublicKeyInfoPem()));
        Assert.Throws<InvalidDataException>(() => IntegratedUpdate.VerifyDescriptor(signed, new("0.4.8.2"), true, rsa.ExportSubjectPublicKeyInfoPem()));
    }

    [TestCase("url")]
    [TestCase("filename")]
    [TestCase("bytes")]
    [TestCase("hash")]
    [TestCase("channel")]
    [TestCase("version")]
    public void AuthenticatedButInvalidMetadataIsRejected(string field)
    {
        using var rsa = RSA.Create(2048);
        var package = field switch {
            "url" => Package with { DownloadUrl = "https://evil.test/setup.exe" },
            "filename" => Package with { Filename = "../setup.exe" },
            "bytes" => Package with { Bytes = IntegratedUpdate.MaximumPackageBytes + 1 },
            "hash" => Package with { Sha256 = "bad" },
            "channel" => Package with { Channel = "stable" },
            _ => Package with { Version = "0.4.8" }
        };
        Assert.Throws<InvalidDataException>(() => IntegratedUpdate.VerifyDescriptor(Sign(package, rsa), Current, true, rsa.ExportSubjectPublicKeyInfoPem()));
    }

    [Test]
    public void WrongKeyAndTamperedPayloadAreRejected()
    {
        using var rsa = RSA.Create(2048);
        using var other = RSA.Create(2048);
        Assert.Throws<CryptographicException>(() => IntegratedUpdate.VerifyDescriptor(Sign(Package, rsa), Current, true, other.ExportSubjectPublicKeyInfoPem()));
        var envelope = JsonSerializer.Deserialize<Dictionary<string, string>>(Sign(Package, rsa))!;
        envelope["payload"] = Convert.ToBase64String(JsonSerializer.SerializeToUtf8Bytes(Package with { Bytes = 4 }));
        Assert.Throws<CryptographicException>(() => IntegratedUpdate.VerifyDescriptor(JsonSerializer.SerializeToUtf8Bytes(envelope), Current, true, rsa.ExportSubjectPublicKeyInfoPem()));
    }

    [TestCase("https://github.com/a", true)]
    [TestCase("https://release-assets.githubusercontent.com/a?token=x", true)]
    [TestCase("http://github.com/a", false)]
    [TestCase("https://github.com.evil.test/a", false)]
    [TestCase("https://user@github.com/a", false)]
    [TestCase("https://github.com:444/a", false)]
    [TestCase("file:///C:/setup.exe", false)]
    public void RedirectPolicy(string url, bool accepted) =>
        Assert.That(IntegratedUpdate.AllowedDownload(new(url)), Is.EqualTo(accepted));

    [Test]
    public void ExactPackageRequiredAndStreamRewound()
    {
        using var correct = new MemoryStream(new byte[] { 1, 2, 3 });
        IntegratedUpdate.VerifyPackage(correct, Package);
        Assert.That(correct.Position, Is.Zero);
        using var corrupt = new MemoryStream(new byte[] { 1, 2, 4 });
        Assert.Throws<CryptographicException>(() => IntegratedUpdate.VerifyPackage(corrupt, Package));
        using var truncated = new MemoryStream(new byte[] { 1, 2 });
        Assert.Throws<InvalidDataException>(() => IntegratedUpdate.VerifyPackage(truncated, Package));
    }

    [Test]
    public void OversizeAndCancelledTransfersFail()
    {
        Assert.ThrowsAsync<InvalidDataException>(async () => await IntegratedUpdate.CopyBounded(
            new MemoryStream(new byte[4]), new MemoryStream(), 3, null, CancellationToken.None));
        Assert.That(async () => await IntegratedUpdate.CopyBounded(
            new MemoryStream(new byte[4]), new MemoryStream(), 4, null, new CancellationToken(true)), Throws.InstanceOf<OperationCanceledException>());
    }
}
