using System.Buffers.Binary;

namespace MadSense.Mac.Tests;

public class PcmLanesTests
{
    [Test]
    public void SpeakerNeverBecomesHaptics()
    {
        float[] stereo = new float[4];
        byte[] haptics = new byte[8];
        PcmLanes.Split([0.5f, -0.25f, 0, 0, 1, -1, 0, 0], stereo, haptics);
        Assert.That(stereo, Is.EqualTo(new[] { 0.5f, -0.25f, 1f, -1f }));
        Assert.That(haptics, Is.All.Zero);
    }

    [Test]
    public void HapticChannelsRemainSeparateAndClipSafely()
    {
        float[] stereo = new float[4];
        byte[] haptics = new byte[8];
        PcmLanes.Split([0, 0, 0.5f, -0.5f, 0, 0, 2, -2], stereo, haptics);
        Assert.That(stereo, Is.All.Zero);
        Assert.That(BinaryPrimitives.ReadInt16LittleEndian(haptics), Is.EqualTo(16384));
        Assert.That(BinaryPrimitives.ReadInt16LittleEndian(haptics.AsSpan(2)), Is.EqualTo(-16384));
        Assert.That(BinaryPrimitives.ReadInt16LittleEndian(haptics.AsSpan(4)), Is.EqualTo(32767));
        Assert.That(BinaryPrimitives.ReadInt16LittleEndian(haptics.AsSpan(6)), Is.EqualTo(-32768));
    }

    [Test]
    public void NonFiniteSamplesBecomeSilenceAndPartialFramesAreRejected()
    {
        float[] stereo = new float[2];
        byte[] haptics = new byte[4];
        PcmLanes.Split([float.NaN, float.PositiveInfinity, float.NegativeInfinity, float.NaN], stereo, haptics);
        Assert.That(stereo, Is.All.Zero);
        Assert.That(haptics, Is.All.Zero);
        Assert.Throws<ArgumentException>(() => PcmLanes.Split([1, 2, 3], stereo, haptics));
        Assert.Throws<ArgumentException>(() => PcmLanes.Split([1, 2, 3, 4], new float[1], haptics));
    }
}
