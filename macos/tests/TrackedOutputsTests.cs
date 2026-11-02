using DualSenseClient.Controllers.DualSense.Audio;
using DualSenseClient.Controllers.DualSense.Output;
using DualSenseClient.Controllers.Emulation;
using DualSenseClient.Emulation.Devices.DualSense;
using DualSenseClient.Hid;
using PhysicalPad = DualSenseClient.Controllers.Devices.DualSenseDevice;

namespace MadSense.Mac.Tests;

public class TrackedOutputsTests
{
    [Test]
    public void RejectedOptionalAudioDoesNotDisableOrdinaryHidFeedback()
    {
        var transport = new RejectableBluetoothHid { RejectAudio = true };
        using var physical = new PhysicalPad(transport, transport) { ThrowOnAudioWriteFailure = true };
        string? sessionFault = null;
        var output = new TrackedOutputs(physical, message => sessionFault = message);
        Assert.Throws<HidException>(() => output.SendBluetoothAudio(new byte[200], BluetoothAudioRoute.Speaker));
        Assert.That(output.AudioFault, Does.Contain("rejected optional Bluetooth audio"));
        Assert.That(output.Fault, Is.Null);
        Assert.That(sessionFault, Is.Null);

        var persona = new DualSenseDevice(new DualSenseIdentity());
        using var adapter = new VirtualDualSenseController(persona, output, false);
        var router = new HidReportRouter(persona, () => sessionFault != null);
        byte[] report = new byte[63];
        report[0] = 2;
        report[1] = 3;
        report[3] = 80;
        Assert.That(router.Set(1, 2, report), Is.Zero);
        Assert.That(transport.LastSuccessfulReport, Is.EqualTo(0x31), "ordinary Bluetooth feedback remains usable");
        Assert.That(sessionFault, Is.Null);
    }

    [Test]
    public void OrdinaryFeedbackFailureStillFaultsTheHidSession()
    {
        var transport = new RejectableBluetoothHid { RejectAll = true };
        using var physical = new PhysicalPad(transport, transport) { ThrowOnAudioWriteFailure = true };
        string? sessionFault = null;
        var output = new TrackedOutputs(physical, message => sessionFault = message);
        Assert.Throws<HidException>(() => output.SetVibration(30, 50));
        Assert.That(sessionFault, Does.Contain("rejected HID feedback"));
        Assert.That(output.Fault, Is.EqualTo(sessionFault));
        Assert.That(output.AudioFault, Is.Null);
    }

    [Test]
    public void PhysicalAudioWritePropagationIsOptIn()
    {
        var transport = new RejectableBluetoothHid { RejectAudio = true };
        using var physical = new PhysicalPad(transport, transport);
        Assert.That(physical.ThrowOnAudioWriteFailure, Is.False);
        Assert.DoesNotThrow(() => physical.SendBluetoothAudio(new byte[200], BluetoothAudioRoute.Speaker));
        physical.ThrowOnAudioWriteFailure = true;
        Assert.Throws<HidException>(() => physical.SendBluetoothAudio(new byte[200], BluetoothAudioRoute.Speaker));
    }

    [Test]
    public void BackgroundAudioWriterFailureIsObservableAndClearedOnRestart()
    {
        var transport = new RejectableBluetoothHid { RejectAudio = true };
        using var physical = new PhysicalPad(transport, transport) { ThrowOnAudioWriteFailure = true };
        string? sessionFault = null;
        var output = new TrackedOutputs(physical, message => sessionFault = message);
        using var forwarder = new DualSenseAudioForwarder(output, null) { StrictNativeHaptics = true };
        Assert.That(forwarder.Start(), Is.True);
        forwarder.FeedPcm(new float[1024]);
        Assert.That(SpinWait.SpinUntil(() => forwarder.HasFailed, TimeSpan.FromSeconds(5)), Is.True,
            "the separate audio writer must expose its failure to the capture owner");
        Assert.That(forwarder.LastFault, Does.Contain("rejected optional Bluetooth audio"));
        Assert.That(output.AudioFault, Does.Contain("rejected optional Bluetooth audio"));
        Assert.That(sessionFault, Is.Null);
        forwarder.Stop();
        Assert.That(forwarder.Start(), Is.True);
        Assert.That(forwarder.LastFault, Is.Null);
        Assert.That(forwarder.HasFailed, Is.False);
    }

    [Test]
    public void AudioRoutingFailureIsReportedOnlyToOptionalAudioOwner()
    {
        var transport = new RejectableBluetoothHid { RejectAll = true };
        using var physical = new PhysicalPad(transport, transport);
        Assert.DoesNotThrow(() => physical.SetAudioOutput(AudioControl.OutputPathSpeaker, 100, 63));
        physical.ThrowOnAudioWriteFailure = true;
        string? sessionFault = null;
        var output = new TrackedOutputs(physical, message => sessionFault = message);
        Assert.Throws<HidException>(() => output.SetAudioOutput(AudioControl.OutputPathSpeaker, 100, 63));
        Assert.That(output.AudioFault, Does.Contain("rejected HID feedback"));
        Assert.That(output.Fault, Is.Null);
        Assert.That(sessionFault, Is.Null);

        transport.RejectAll = false;
        Assert.DoesNotThrow(() => output.SetVibration(20, 30));
        Assert.That(transport.LastSuccessfulReport, Is.EqualTo(0x31));
    }

    private sealed class RejectableBluetoothHid : IHidDevice, IHidDeviceInfo
    {
        public bool RejectAudio { get; init; }
        public bool RejectAll { get; set; }
        public int LastSuccessfulReport { get; private set; }
        public ushort VendorId => 0x054c;
        public ushort ProductId => 0x0ce6;
        public string DevicePath => "test-bluetooth";
        public string Path => DevicePath;
        public string ProductName => "Test DualSense";
        public string Manufacturer => "Test";
        public int InterfaceNumber => 0;
        public ushort UsagePage => 1;
        public HidUsageId Usage => HidUsageId.GamePad;
        public ConnectionType BusType => ConnectionType.Bluetooth;
        public bool IsConnected => true;
        public int Read(byte[] buffer, int offset, int count, int timeoutMs)
        {
            Thread.Sleep(1);
            return 0;
        }
        public Task<int> ReadAsync(byte[] buffer, int offset, int count, CancellationToken ct) => Task.FromResult(0);
        public int Write(byte[] buffer, int offset, int count)
        {
            if (RejectAll) throw new HidException("rejected HID feedback");
            if (RejectAudio && buffer[offset] is 0x32 or 0x35 or 0x36)
                throw new HidException("rejected optional Bluetooth audio");
            LastSuccessfulReport = buffer[offset];
            return count;
        }
        public byte[] GetFeatureReport(byte reportId, int bufferSize = 64) => [];
        public void SendFeatureReport(byte[] buffer, int offset, int count) { }
        public string GetProductName() => ProductName;
        public void Dispose() { }
    }
}
