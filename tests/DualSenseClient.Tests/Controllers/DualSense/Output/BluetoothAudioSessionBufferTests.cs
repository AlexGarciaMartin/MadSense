using DualSenseClient.Controllers.DualSense.Output;

namespace DualSenseClient.Tests.Controllers.DualSense.Output;

[TestFixture]
public class BluetoothAudioSessionBufferTests
{
    static readonly byte[] Opus = new byte[BluetoothAudioReportBuilder.AudioPayloadSize];
    static readonly byte[] Haptics = new byte[BluetoothAudioReportBuilder.HapticsPayloadSize];

    [Test]
    public void DefaultSessionBytes_AreTheReferenceValue()
    {
        var builder = new BluetoothAudioReportBuilder();
        Assert.That(builder.SessionBufferLengths, Is.EqualTo(new byte[] { 0x40, 0x40, 0x40, 0x40, 0x40 }));
        byte[] combined = builder.BuildCombinedReport(new SetStateData(), Opus, Haptics, BluetoothAudioRoute.Speaker);
        Assert.That(combined[5..10], Is.EqualTo(new byte[] { 0x40, 0x40, 0x40, 0x40, 0x40 }));
    }

    [Test]
    public void IndividualSessionBytes_ReachEveryReportFamily()
    {
        var builder = new BluetoothAudioReportBuilder { SessionBufferLengths = [0x40, 0x40, 0x40, 0x40, 0x20] };
        byte[] expected = [0x40, 0x40, 0x40, 0x40, 0x20];
        Assert.That(builder.BuildCombinedReport(new SetStateData(), Opus, Haptics, BluetoothAudioRoute.Speaker)[5..10], Is.EqualTo(expected));
        Assert.That(builder.BuildAudioReport(Opus, BluetoothAudioRoute.Speaker)[5..10], Is.EqualTo(expected));
        Assert.That(builder.BuildHapticsReport(Haptics)[5..10], Is.EqualTo(expected));
        // The getter hands out a copy: mutating it must not touch the builder.
        builder.SessionBufferLengths[4] = 0x08;
        Assert.That(builder.SessionBufferLengths[4], Is.EqualTo(0x20));
    }

    [Test]
    public void SingleValueSetter_FillsAllFive()
    {
        var builder = new BluetoothAudioReportBuilder { SessionBufferLength = 0x10 };
        Assert.That(builder.SessionBufferLengths, Is.EqualTo(new byte[] { 0x10, 0x10, 0x10, 0x10, 0x10 }));
        Assert.Throws<ArgumentException>(() => builder.SessionBufferLengths = [0x40, 0x40]);
    }
}
