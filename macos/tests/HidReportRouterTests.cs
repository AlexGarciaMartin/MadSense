using DualSenseClient.Emulation.Devices.DualSense;
using MadSense.Mac;

namespace MadSense.Mac.Tests;

public class HidReportRouterTests
{
    [Test]
    public void InputPreservesRawMotionTouchpadAndButtonBytes()
    {
        var pad = new DualSenseDevice(new DualSenseIdentity());
        var router = new HidReportRouter(pad);
        byte[] raw = Enumerable.Range(0, 64).Select(i => (byte)i).ToArray();
        raw[0] = 1;
        pad.SetRawInputReport(raw);
        byte[] output = new byte[64];
        Assert.That(router.Get(0, 1, output, out int count), Is.Zero);
        Assert.That(count, Is.EqualTo(64));
        Assert.That(output, Is.EqualTo(raw));
    }

    [TestCase(0)] [TestCase(1)] [TestCase(7)] [TestCase(40)] [TestCase(64)]
    public void FeatureReadHonorsHostCapacity(int capacity)
    {
        var pad = new DualSenseDevice(new DualSenseIdentity());
        byte[] physical = Enumerable.Repeat((byte)42, 41).ToArray();
        physical[0] = 5;
        Assert.That(pad.TrySetMotionCalibration(physical), Is.True);
        var router = new HidReportRouter(pad);
        byte[] destination = Enumerable.Repeat((byte)0xcc, capacity + 8).ToArray();
        Assert.That(router.Get(2, 5, destination.AsSpan(0, capacity), out int count), Is.Zero);
        Assert.That(count, Is.EqualTo(Math.Min(capacity, physical.Length)));
        Assert.That(destination.Take(count), Is.EqualTo(physical.Take(count)));
        Assert.That(destination.Skip(capacity), Is.All.EqualTo((byte)0xcc));
    }

    [Test]
    public void OutputIsNotPrefixedWithASecondReportId()
    {
        var pad = new DualSenseDevice(new DualSenseIdentity());
        var router = new HidReportRouter(pad);
        DualSenseOutputReport? received = null;
        pad.OutputReportReceived += r => received = r;
        byte[] report = new byte[63];
        report[0] = 2;
        report[1] = 3;
        report[3] = 70;
        report[4] = 90;
        Assert.That(router.Set(1, 2, report), Is.Zero);
        Assert.That(received.HasValue, Is.True);
        Assert.That(received!.Value.RumbleRight, Is.EqualTo(70));
        Assert.That(received.Value.RumbleLeft, Is.EqualTo(90));
        Assert.That(received.Value.RumbleEmulationEnabled, Is.True);
        Assert.That(received.Value.UseRumbleNotHaptics, Is.True);
    }

    [Test]
    public void EdgeFeatureWriteStripsIdExactlyOnce()
    {
        var router = new HidReportRouter(new DualSenseDevice(new DualSenseIdentity(), true));
        byte[] handshake = Enumerable.Range(0, 64).Select(i => (byte)(i + 30)).ToArray();
        handshake[0] = 0x65;
        Assert.That(router.Set(2, 0x65, handshake), Is.Zero);
        byte[] result = new byte[64];
        Assert.That(router.Get(2, 0x65, result, out int count), Is.Zero);
        Assert.That(count, Is.EqualTo(handshake.Length));
        Assert.That(result, Is.EqualTo(handshake));
    }

    [Test]
    public void RejectsUnknownTypesMismatchedIdsAndTruncatedOutput()
    {
        var router = new HidReportRouter(new DualSenseDevice(new DualSenseIdentity()));
        Assert.That(router.Get(1, 2, new byte[64], out _), Is.EqualTo(HidReportRouter.Unsupported));
        Assert.That(router.Get(2, 0xff, new byte[64], out _), Is.EqualTo(HidReportRouter.Unsupported));
        Assert.That(router.Get(0, 2, new byte[64], out _), Is.EqualTo(HidReportRouter.Unsupported));
        Assert.That(router.Set(0, 1, [1, 2]), Is.EqualTo(HidReportRouter.Unsupported));
        Assert.That(router.Set(1, 2, [2]), Is.EqualTo(HidReportRouter.BadArgument));
        Assert.That(router.Set(1, 2, [3, 0, 0]), Is.EqualTo(HidReportRouter.BadArgument));
        Assert.That(router.Set(2, 300, [44]), Is.EqualTo(HidReportRouter.Unsupported));
    }
}
