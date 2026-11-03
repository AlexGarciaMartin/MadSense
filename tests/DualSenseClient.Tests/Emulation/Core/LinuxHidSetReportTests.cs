using DualSenseClient.Emulation;
using DualSenseClient.Emulation.Core;
using DualSenseClient.Emulation.Devices.DualSense;
using DualSenseClient.Emulation.UsbIp;
using DualSenseClient.Tests.Emulation.TestSupport;

namespace DualSenseClient.Tests.Emulation.Core;

[TestFixture]
public sealed class LinuxHidSetReportTests
{
    static UsbSetupPacket Set(ushort value, ushort length, ushort index = 0) =>
        new() { RequestType = 0x21, Request = 9, Value = value, Index = index, Length = length };

    static byte[] Feature(DualSenseDevice device, byte id, bool prefixed)
    {
        byte[] response = new byte[64];
        var setup = new UsbSetupPacket { RequestType = 0xa1, Request = 1, Value = (ushort)(0x0300 | id), Index = 3, Length = 64 };
        Assert.That(device.HandleControlRequest(setup, [], response, prefixed), Is.EqualTo(64));
        return response;
    }

    [TestCase(false, (byte)0)]
    [TestCase(true, (byte)1)]
    public void RecordedLinuxStatusCommand_OptInSelectsPayloadInsteadOfReportId(bool enabled, byte status)
    {
        var device = new DualSenseDevice(new DualSenseIdentity());
        // Exact setup and OUT data captured in native run 41659561451.
        byte[] captured = [0x80, 0x03, 0x00];
        Assert.That(device.HandleControlRequest(Set(0x0380, 3, 3), captured, [], enabled), Is.Zero);
        byte[] expected = new byte[64]; expected[0] = 0x81; expected[1] = 1; expected[4] = status;
        Assert.That(Feature(device, 0x81, enabled), Is.EqualTo(expected));
        Assert.That(captured, Is.EqualTo(new byte[] { 0x80, 3, 0 }), "Caller memory must not be normalized in place.");
    }

    [Test]
    public void LegacyThreeArgumentStatusCommand_RemainsUnprefixed()
    {
        var device = new DualSenseDevice(new DualSenseIdentity());
        Assert.That(device.HandleControlRequest(Set(0x0380, 2, 3), new byte[] { 3, 0 }, []), Is.Zero);
        Assert.That(Feature(device, 0x81, false)[4], Is.EqualTo(1));
        Assert.That(new EmulationServerOptions().HidSetReportIncludesReportId, Is.False);
        Assert.That(new UsbIpServerOptions().HidSetReportIncludesReportId, Is.False);
    }

    [Test]
    public void EdgeHandshakePrefixed64Bytes_EchoesExactlyWithoutLosingTheLastByte()
    {
        var device = new DualSenseDevice(new DualSenseIdentity(), true);
        byte[] handshake = new byte[64]; handshake[0] = 0x65;
        for (int i = 1; i < handshake.Length; i++) handshake[i] = (byte)(17 + 3 * i);
        Assert.That(device.HandleControlRequest(Set(0x0365, 64, 3), handshake, [], true), Is.Zero);
        Assert.That(Feature(device, 0x65, true), Is.EqualTo(handshake));
    }

    [Test]
    public void SonyOutput_ControlOptInDeliversExactlyTheInterruptShape()
    {
        var device = new DualSenseDevice(new DualSenseIdentity());
        byte[] report = new byte[48]; report[0] = 2; report[2] = 4; report[45] = 0x45; report[46] = 0x67; report[47] = 0x89;
        List<byte[]> observed = [];
        device.RawOutputReportReceived += data => observed.Add(data.ToArray());
        Assert.That(device.HandleControlRequest(Set(0x0202, 48, 3), report, [], true), Is.Zero);
        device.OnOutputReport(report); // The interrupt API is not modified.
        Assert.That(observed, Has.Count.EqualTo(2));
        Assert.That(observed.All(value => value.SequenceEqual(report)), Is.True);
    }

    [TestCase(0x0205)]
    [TestCase(0x0305)]
    public void InvalidNumberedWireData_StallsBeforeAnyReportCallback(int value)
    {
        var device = new RecordingHid();
        foreach (var (declared, data) in new (ushort, byte[])[] { (0, []), (1, []), (3, [5, 1]), (2, [6, 1]) })
            Assert.That(device.HandleControlRequest(Set((ushort)value, declared), data, [], true), Is.EqualTo(-1));
        Assert.That(device.Delivered, Is.Empty);
    }

    [TestCase(0x0200)]
    [TestCase(0x0300)]
    public void IdZero_IsUnnumberedAndNeverStripsItsFirstByte(int value)
    {
        var device = new RecordingHid();
        Assert.That(device.HandleControlRequest(Set((ushort)value, 3), new byte[] { 0, 0x45, 0x67 }, [], true), Is.Zero);
        Assert.That(device.Delivered.Single(), Is.EqualTo(new byte[] { 0, 0x45, 0x67 }));
        device.Delivered.Clear();
        Assert.That(device.HandleControlRequest(Set((ushort)value, 0), [], [], true), Is.Zero);
        Assert.That(device.Delivered.Single(), Is.Empty);
    }

    [TestCase(0x0205, true)]
    [TestCase(0x0305, false)]
    public void IdOnly_DeliversEmptyBodyToExistingHandler_AndIgnoresUndeclaredTail(int value, bool output)
    {
        var device = new RecordingHid();
        Assert.That(device.HandleControlRequest(Set((ushort)value, 1), new byte[] { 5, 0xee, 0xff }, [], true), Is.Zero);
        Assert.That(device.Delivered.Single(), Is.EqualTo(output ? new byte[] { 5 } : Array.Empty<byte>()));
    }

    [TestCase(0x0205, 65535)]
    [TestCase(0x0305, 65534)]
    public void MaximumDeclaredLength_DoesNotWrapOrConsumeUndeclaredData(int value, int delivered)
    {
        var device = new RecordingHid();
        byte[] data = new byte[65536]; data[0] = 5; data[65534] = 0xa5; data[65535] = 0xee;
        Assert.That(device.HandleControlRequest(Set((ushort)value, ushort.MaxValue), data, [], true), Is.Zero);
        Assert.That(device.Delivered.Single(), Has.Length.EqualTo(delivered));
        Assert.That(device.Delivered.Single()[^1], Is.EqualTo(0xa5));
    }

    [TestCase(0xa1, 0x0205, 0)] // Wrong direction for SET_REPORT.
    [TestCase(0x20, 0x0205, 0)] // Wrong recipient.
    [TestCase(0x21, 0x0105, 0)] // Input report type is not writable.
    [TestCase(0x21, 0x0205, 9)] // Undeclared interface.
    public void NonmatchingControlRequests_StillStallWithoutReportCallbacks(int requestType, int value, int index)
    {
        var device = new RecordingHid();
        var setup = new UsbSetupPacket { RequestType = (byte)requestType, Request = 9, Value = (ushort)value, Index = (ushort)index, Length = 2 };
        Assert.That(device.HandleControlRequest(setup, new byte[] { 5, 0x44 }, [], true), Is.EqualTo(-1));
        Assert.That(device.Delivered, Is.Empty);
    }

    [Test]
    public void SonyAudioSetAndGet_KeepTheirExistingClassRouting()
    {
        var device = new DualSenseDevice(new DualSenseIdentity());
        var set = new UsbSetupPacket { RequestType = 0x21, Request = 1, Value = 0x0200, Index = 0x0200, Length = 2 };
        byte[] volume = [0, 0xf6]; // -10 dB in Q8.
        Assert.That(device.HandleControlRequest(set, volume, [], true), Is.Zero);
        var get = new UsbSetupPacket { RequestType = 0xa1, Request = 0x81, Value = set.Value, Index = set.Index, Length = 2 };
        byte[] response = new byte[2];
        Assert.That(device.HandleControlRequest(get, [], response, true), Is.EqualTo(2));
        Assert.That(response, Is.EqualTo(volume));
    }

    sealed class RecordingHid() : VirtualHidDevice(TestDevices.CreateSimpleDevice())
    {
        internal List<byte[]> Delivered { get; } = [];
        public override void OnOutputReport(ReadOnlySpan<byte> data) => Delivered.Add(data.ToArray());
        protected override bool OnFeatureReport(byte reportId, ReadOnlySpan<byte> data)
        { Delivered.Add(data.ToArray()); return true; }
    }
}
