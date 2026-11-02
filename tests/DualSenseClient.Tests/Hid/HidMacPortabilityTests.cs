using DualSenseClient.Hid;
using DualSenseClient.Hid.Interop;

namespace DualSenseClient.Tests.Hid;

[TestFixture]
public sealed class HidMacPortabilityTests
{
    [Test]
    public void NativeLibrary_SearchesApplicationBeforeSystemLibraries()
    {
        string executableDirectory = Path.Combine(Path.GetTempPath(), "MadSense.app", "Contents", "MacOS");
        string[] candidates = HidNativeLibrary.GetMacCandidates(executableDirectory).ToArray();

        Assert.That(candidates.Take(4), Is.EqualTo(new[]
        {
            Path.Combine(executableDirectory, "libhidapi.dylib"),
            Path.Combine(executableDirectory, "libhidapi.0.dylib"),
            Path.GetFullPath(Path.Combine(executableDirectory, "..", "Frameworks", "libhidapi.dylib")),
            Path.GetFullPath(Path.Combine(executableDirectory, "..", "Frameworks", "libhidapi.0.dylib"))
        }));
        Assert.That(candidates, Has.None.EndsWith(".so"));
    }

    [Test]
    public void ExclusiveOpen_RejectsOtherPlatformsBeforeTouchingHardware()
    {
        if (OperatingSystem.IsMacOS())
            Assert.Ignore("Other-platform guard only.");

        using HidDeviceEnumerator enumerator = new();
        Assert.That(() => enumerator.OpenDeviceExclusive("unused"),
            Throws.TypeOf<PlatformNotSupportedException>());
    }

    [Test]
    public void MacNativeLibrary_LoadsAndExportsExclusiveOpenPolicy()
    {
        if (!OperatingSystem.IsMacOS())
            Assert.Ignore("Requires the packaged macOS HIDAPI binary; no controller needed.");

        Assert.That(HidApi.Init(), Is.Zero, "Native HIDAPI initialization failed.");
        int originalPolicy = HidApi.DarwinGetOpenExclusive();
        try
        {
            HidApi.DarwinSetOpenExclusive(0);
            Assert.That(HidApi.DarwinGetOpenExclusive(), Is.Zero);
            HidApi.DarwinSetOpenExclusive(1);
            Assert.That(HidApi.DarwinGetOpenExclusive(), Is.EqualTo(1));
        }
        finally
        {
            HidApi.DarwinSetOpenExclusive(originalPolicy);
            HidApi.Exit();
        }
    }
}
