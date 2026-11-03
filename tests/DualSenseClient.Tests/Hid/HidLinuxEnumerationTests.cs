using System.Reflection;
using DualSenseClient.Hid;

namespace DualSenseClient.Tests.Hid;

[TestFixture]
public sealed class HidLinuxEnumerationTests
{
    [Test]
    public void CurrentLinuxSnapshot_DoesNotOpenOrDemandInputFromItsBluetoothEntries()
    {
        if (!OperatingSystem.IsLinux()) Assert.Ignore("Linux hidraw snapshot policy.");
        using var enumerator = new HidDeviceEnumerator();
        List<IHidDeviceInfo> snapshot = [new HidDeviceInfo
        {
            Path = "/nonexistent/madsense-enumeration-no-open", VendorId = 0x054c, ProductId = 0x0ce6,
            BusType = ConnectionType.Bluetooth, UsagePage = 1, Usage = HidUsageId.GamePad
        }];
        // Invoke the actual post-enumeration policy with an already supplied
        // snapshot. The old probe-open/read path drops this entry. The owned
        // UHID integration separately supplies a real present, silent device.
        var policy = typeof(HidDeviceEnumerator).GetMethod("FilterConnected", BindingFlags.Instance | BindingFlags.NonPublic)!;
        var filtered = (List<IHidDeviceInfo>)policy.Invoke(enumerator, [snapshot])!;
        Assert.That(filtered, Is.SameAs(snapshot));
        Assert.That(filtered.Single(), Is.SameAs(snapshot[0]));
    }
}
