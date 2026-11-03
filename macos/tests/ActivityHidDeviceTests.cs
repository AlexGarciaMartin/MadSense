using DualSenseClient.Controllers.DualSense.Output;
using DualSenseClient.Controllers.DualSense.Triggers;
using DualSenseClient.Hid;

namespace MadSense.Mac.Tests;

public class ActivityHidDeviceTests
{
    private sealed class FakeHid : IHidDevice
    {
        public Func<int, int>? WriteAction { get; set; }
        public bool FailRead { get; set; }
        public bool Connected { get; set; } = true;
        public int DisposeCount { get; private set; }
        public ushort VendorId => 0x054c;
        public ushort ProductId => 0x0ce6;
        public string DevicePath => "test-bluetooth";
        public bool IsConnected => Connected;
        public string GetProductName() => "Fake DualSense";
        public int Write(byte[] buffer, int offset, int count) => WriteAction?.Invoke(count) ?? count;
        public int Read(byte[] buffer, int offset, int count, int timeoutMs) => FailRead ? throw new HidException("Disconnected") : 0;
        public Task<int> ReadAsync(byte[] buffer, int offset, int count, CancellationToken ct) =>
            FailRead ? Task.FromException<int>(new HidException("Disconnected")) : Task.FromResult(0);
        public byte[] GetFeatureReport(byte reportId, int bufferSize = 64) => [reportId];
        public void SendFeatureReport(byte[] buffer, int offset, int count) { }
        public void Dispose() => DisposeCount++;
    }

    private static SetStateData Effects(bool active) => new()
    {
        ValidFlag0 = ValidFlags.AllowRightTriggerFfb | ValidFlags.AllowLeftTriggerFfb | ValidFlags.UseRumbleNotHaptics,
        R2TriggerEffect = active ? TriggerEffectBuilder.Feedback(2, 4) : TriggerEffectBuilder.Off(),
        L2TriggerEffect = active ? TriggerEffectBuilder.Feedback(3, 5) : TriggerEffectBuilder.Off(),
        RumbleLeft = active ? (byte)80 : (byte)0,
        RumbleRight = active ? (byte)60 : (byte)0
    };

    private static void Send(ActivityHidDevice device, byte[] report) => device.Write(report, 0, report.Length);

    [Test]
    public void RecentAudioAndNonzeroHapticsExpireButTriggerEffectsPersist()
    {
        long now = 0;
        using var device = new ActivityHidDevice(new FakeHid(), () => now);
        Assert.That(device.Activity, Is.EqualTo(default(ControllerActivity)), "Startup must not resemble a recent write.");
        var builder = new BluetoothAudioReportBuilder();
        byte[] pcm = new byte[64]; pcm[0] = 1;
        Send(device, builder.BuildCombinedReport(Effects(true), new byte[200], pcm, BluetoothAudioRoute.Speaker));
        Assert.That(device.Activity, Is.EqualTo(new ControllerActivity(true, true, true, false)));
        now = 1499;
        Assert.That(device.Activity.Haptics && device.Activity.Audio, Is.True);
        now = 1500;
        Assert.That(device.Activity, Is.EqualTo(new ControllerActivity(false, true, false, false)));
        // Speaker bytes, state, CRC and priming must never impersonate haptic PCM.
        Send(device, builder.BuildCombinedReport(new SetStateData(), Enumerable.Repeat((byte)99, 200).ToArray(), new byte[64], BluetoothAudioRoute.Headset));
        Assert.That(device.Activity.Haptics, Is.False);
        Assert.That(device.Activity.Audio, Is.True);
        Assert.That(device.Activity.Triggers, Is.True);
    }

    [Test]
    public void StandaloneHapticAndAudioReportsTrackTheirOwnLanes()
    {
        long now = 100;
        using var device = new ActivityHidDevice(new FakeHid(), () => now);
        var builder = new BluetoothAudioReportBuilder();
        Send(device, builder.BuildInitPrime(Effects(true)));
        Assert.That(device.Activity, Is.EqualTo(new ControllerActivity(false, true, false, false)));
        byte[] pcm = new byte[64]; pcm[63] = 0xff;
        Send(device, builder.BuildHapticsReport(pcm));
        Assert.That(device.Activity.Haptics, Is.True);
        Assert.That(device.Activity.Audio, Is.False);
        now = 1600;
        Send(device, builder.BuildAudioReport(new byte[200], BluetoothAudioRoute.Headset));
        Assert.That(device.Activity, Is.EqualTo(new ControllerActivity(false, true, true, false)));
        Send(device, builder.BuildInitPrime(Effects(false)));
        Assert.That(device.Activity.Triggers, Is.False);
    }

    [Test]
    public void PersistentEffectsRespectAllowBitsAndSuccessfulExplicitOff()
    {
        long now = 100;
        var inner = new FakeHid();
        using var device = new ActivityHidDevice(inner, () => now);
        Send(device, OutputReport.ForBluetooth(Effects(true), 0).Raw);
        now = 100000;
        Send(device, OutputReport.ForBluetooth(new SetStateData(), 1).Raw);
        Assert.That(device.Activity, Is.EqualTo(new ControllerActivity(false, true, false, true)));
        inner.WriteAction = _ => throw new HidException("Reset rejected");
        Assert.Throws<HidException>(() => Send(device, OutputReport.ForBluetooth(Effects(false), 2).Raw));
        Assert.That(device.Activity.Triggers && device.Activity.Rumble, Is.True);
        inner.WriteAction = count => count - 1;
        Send(device, OutputReport.ForBluetooth(Effects(false), 3).Raw);
        Assert.That(device.Activity.Triggers && device.Activity.Rumble, Is.True);
        inner.WriteAction = null;
        Send(device, OutputReport.ForBluetooth(new SetStateData
        {
            ValidFlag0 = ValidFlags.AllowRightTriggerFfb,
            R2TriggerEffect = TriggerEffectBuilder.Off()
        }, 4).Raw);
        Assert.That(device.Activity.Triggers, Is.True, "Left trigger retains its previous delivered effect.");
        Send(device, OutputReport.ForBluetooth(Effects(false), 5).Raw);
        Assert.That(device.Activity, Is.EqualTo(default(ControllerActivity)));
    }

    [Test]
    public void FailedAndShortWritesCannotActivateOrRefreshIndicators()
    {
        long now = 100;
        var inner = new FakeHid { WriteAction = count => count - 1 };
        using var device = new ActivityHidDevice(inner, () => now);
        var builder = new BluetoothAudioReportBuilder();
        byte[] report = builder.BuildCombinedReport(Effects(true), new byte[200], Enumerable.Repeat((byte)1, 64).ToArray(), BluetoothAudioRoute.Speaker);
        Send(device, report);
        Assert.That(device.Activity, Is.EqualTo(default(ControllerActivity)));
        inner.WriteAction = _ => throw new HidException("Write rejected");
        Assert.Throws<HidException>(() => Send(device, report));
        Assert.That(device.Activity, Is.EqualTo(default(ControllerActivity)));
        inner.WriteAction = null;
        Send(device, report);
        now += 1500;
        inner.WriteAction = count => count - 1;
        Send(device, report);
        Assert.That(device.Activity.Haptics || device.Activity.Audio, Is.False);
        Assert.That(device.Activity.Triggers, Is.True);
    }

    [TestCase(false)]
    [TestCase(true)]
    public async Task PendingWriteDoesNotBecomeActivityAndCannotReviveDisposedSession(bool disposeBeforeCompletion)
    {
        using var entered = new ManualResetEventSlim();
        using var complete = new ManualResetEventSlim();
        var inner = new FakeHid { WriteAction = count => { entered.Set(); complete.Wait(); return count; } };
        using var device = new ActivityHidDevice(inner, () => 100);
        Task writing = Task.Run(() => Send(device, OutputReport.ForBluetooth(Effects(true), 0).Raw));
        try
        {
            Assert.That(entered.Wait(TimeSpan.FromSeconds(5)), Is.True);
            Assert.That(device.Activity, Is.EqualTo(default(ControllerActivity)));
            if (disposeBeforeCompletion) device.Dispose();
        }
        finally { complete.Set(); }
        await writing;
        Assert.That(device.Activity, Is.EqualTo(disposeBeforeCompletion ? default : new ControllerActivity(false, true, false, true)));
        device.Dispose();
        Assert.That(device.Activity, Is.EqualTo(default(ControllerActivity)));
        Assert.That(inner.DisposeCount, Is.EqualTo(1));
    }

    [TestCase(false)]
    [TestCase(true)]
    public async Task DisconnectClearsPersistentStateAndCannotBeRevivedByLateWrites(bool asynchronous)
    {
        var inner = new FakeHid();
        using var device = new ActivityHidDevice(inner, () => 100);
        Send(device, OutputReport.ForBluetooth(Effects(true), 0).Raw);
        inner.FailRead = true;
        if (asynchronous) await Assert.ThrowsAsync<HidException>(() => device.ReadAsync(new byte[78], 0, 78, CancellationToken.None));
        else Assert.Throws<HidException>(() => device.Read(new byte[78], 0, 78, 25));
        Send(device, OutputReport.ForBluetooth(Effects(true), 1).Raw);
        Assert.That(device.Activity, Is.EqualTo(default(ControllerActivity)));
    }

    [Test]
    public void MeterHonorsBufferSliceAndIgnoresMalformedOrUnrelatedReports()
    {
        using var device = new ActivityHidDevice(new FakeHid(), () => 100);
        Send(device, [0x36]);
        Send(device, new byte[398]);
        byte[] report = OutputReport.ForBluetooth(Effects(true), 0).Raw;
        byte[] padded = new byte[report.Length + 20];
        report.CopyTo(padded, 10);
        device.Write(padded, 10, report.Length);
        Assert.That(device.Activity.Triggers && device.Activity.Rumble, Is.True);
    }
}
