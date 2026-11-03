using DualSenseClient.Hid;
using MadSense.Mac;

namespace MadSense.Mac.Tests;

public class VirtualHidPreflightTests
{
    [TestCase((uint)VirtualHidEntitlementState.Missing, "Boolean entitlement absent.")]
    [TestCase((uint)VirtualHidEntitlementState.Unknown, "Entitlement query failed: test error.")]
    public void FailedPreflightNeverAttemptsPhysicalCapture(uint rawState, string detail)
    {
        var state = (VirtualHidEntitlementState)rawState;
        int opens = 0;
        var error = Assert.Throws<VirtualHidUnavailableException>(() => new BridgeSession(
            () => { opens++; throw new IOException("physical open sentinel"); }, new TestInfo(), "high",
            () => MacVirtualHid.RequirePreflight(new(state, detail))));
        Assert.Multiple(() =>
        {
            Assert.That(opens, Is.Zero);
            Assert.That(error!.NativeErrorCode, Is.Null, "A preflight result is not an IOKit creation error.");
            Assert.That(error.Message, Does.Contain(detail).And.Contain("Physical capture was not attempted"));
        });
        var retry = new BridgeRetryPolicy();
        retry.RecordFailure(error!, 0);
        Assert.That(retry.RequiresExplicitRetry, Is.True);
    }

    [Test]
    public void PresentOnlyAllowsTheNextStepAndPreservesItsActualFailure()
    {
        var order = new List<string>();
        var physicalError = new IOException("Real physical-open failure sentinel.");
        var error = Assert.Throws<IOException>(() => new BridgeSession(
            () => { order.Add("open"); throw physicalError; }, new TestInfo(), "high", () =>
            {
                order.Add("preflight");
                MacVirtualHid.RequirePreflight(new(VirtualHidEntitlementState.Present, "Presence only."));
            }));
        Assert.That(order, Is.EqualTo(new[] { "preflight", "open" }));
        Assert.That(error, Is.SameAs(physicalError));
    }

    [Test]
    public void UnexpectedResultNeverAllowsCapture() => Assert.Throws<VirtualHidUnavailableException>(() =>
        MacVirtualHid.RequirePreflight(new((VirtualHidEntitlementState)uint.MaxValue, "Unexpected result.")));

    private sealed class TestInfo : IHidDeviceInfo
    {
        public string Path => "test-only-no-hardware";
        public ushort VendorId => 0x054c;
        public ushort ProductId => 0x0ce6;
        public string ProductName => "Test sentinel";
        public string Manufacturer => "Test";
        public int InterfaceNumber => 0;
        public ushort UsagePage => 1;
        public HidUsageId Usage => default;
        public ConnectionType BusType => ConnectionType.Bluetooth;
    }
}
