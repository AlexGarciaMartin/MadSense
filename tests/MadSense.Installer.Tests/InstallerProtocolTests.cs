using System.Reflection;
using System.Text;
using System.Text.Json;
using MadSense;

namespace MadSense.Installer.Tests;

[TestFixture]
public sealed class InstallerProtocolTests
{
    [Test]
    public void AssemblyAndSidecarAdvertiseTheSameMaintenanceProtocol()
    {
        var assembly = typeof(InstallerMaintenance).Assembly;
        var metadata = assembly.GetCustomAttributes<AssemblyMetadataAttribute>()
            .Single(a => a.Key == "MadSense.InstallerProtocol");
        using var sidecar = JsonDocument.Parse(File.ReadAllText(
            Path.Combine(Path.GetDirectoryName(assembly.Location)!, "MadSense.installer.json")));
        Assert.Multiple(() =>
        {
            Assert.That(metadata.Value, Is.EqualTo("1"));
            Assert.That(sidecar.RootElement.GetProperty("protocol").GetInt32(), Is.EqualTo(1));
            // Packaging can inspect this marker without launching an unknown payload.
            Assert.That(Encoding.UTF8.GetString(File.ReadAllBytes(assembly.Location)),
                Does.Contain("MadSense.InstallerProtocol"));
        });
    }
}
