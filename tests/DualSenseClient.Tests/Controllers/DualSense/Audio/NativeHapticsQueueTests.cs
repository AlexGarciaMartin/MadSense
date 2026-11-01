using System.Buffers.Binary;
using DualSenseClient.Controllers.DualSense.Audio;

namespace DualSenseClient.Tests.Controllers.DualSense.Audio;

public class NativeHapticsQueueTests
{
    [Test]
    public void UsbPacketBoundariesDoNotChangeFrequencyOrChannelOrder()
    {
        var pcm = new byte[512 * 4];
        for (int i = 0; i < 512; i++)
        {
            BinaryPrimitives.WriteInt16LittleEndian(pcm.AsSpan(i*4,2), (short)(15000 * Math.Sin(2*Math.PI*120*i/48000)));
            BinaryPrimitives.WriteInt16LittleEndian(pcm.AsSpan(i*4+2,2), (short)(7000 * Math.Sin(2*Math.PI*210*i/48000)));
        }
        var whole = new NativeHapticsQueue(); var fragmented = new NativeHapticsQueue();
        whole.Feed(pcm);
        int offset = 0;
        while (offset < pcm.Length) { int length = Math.Min(47*4,pcm.Length-offset); fragmented.Feed(pcm.AsSpan(offset,length)); offset += length; }
        byte[] a = new byte[64], b = new byte[64]; whole.Read(a); fragmented.Read(b);
        Assert.That(b, Is.EqualTo(a));
        Assert.That(fragmented.Count, Is.Zero);
        for (int sample=0; sample<32; sample++)
            for (int channel=0;channel<2;channel++)
            {
                int sum=0;
                for(int frame=0;frame<16;frame++) sum += BinaryPrimitives.ReadInt16LittleEndian(pcm.AsSpan((sample*16+frame)*4+channel*2,2));
                Assert.That(Math.Abs((sbyte)b[sample*2+channel] - sum/4096.0), Is.LessThanOrEqualTo(0.5));
            }
    }

    [TestCase(0, 0)]
    [TestCase(127, 0)]
    [TestCase(128, 0)]
    [TestCase(129, 1)]
    [TestCase(255, 1)]
    [TestCase(384, 2)]
    [TestCase(640, 2)]
    public void QuietAmplitudesUseNearestSymmetricQuantization(int value, int expected)
    {
        var queue = new NativeHapticsQueue();
        var pcm = new byte[16 * 4];
        for (int i = 0; i < 16; i++)
        {
            BinaryPrimitives.WriteInt16LittleEndian(pcm.AsSpan(i * 4, 2), (short)value);
            BinaryPrimitives.WriteInt16LittleEndian(pcm.AsSpan(i * 4 + 2, 2), (short)-value);
        }
        queue.Feed(pcm);
        byte[] output = new byte[2]; queue.Read(output);
        Assert.That((sbyte)output[0], Is.EqualTo(expected));
        Assert.That((sbyte)output[1], Is.EqualTo(-expected));
    }

    [Test]
    public void QuantizationErrorIsAtMostHalfLsbExceptPositiveSaturation()
    {
        var queue = new NativeHapticsQueue();
        var pcm = new byte[16 * 4];
        byte[] output = new byte[2];
        for (int value = short.MinValue; value <= short.MaxValue; value++)
        {
            for (int i = 0; i < pcm.Length; i += 2)
                BinaryPrimitives.WriteInt16LittleEndian(pcm.AsSpan(i, 2), (short)value);
            queue.Feed(pcm); queue.Read(output);
            double ideal = value / 256.0;
            if (ideal <= 127.5)
                Assert.That(Math.Abs((sbyte)output[0] - ideal), Is.LessThanOrEqualTo(0.5), $"S16 input {value}");
            else
                Assert.That((sbyte)output[0], Is.EqualTo(127), "Positive full scale must saturate, not wrap.");
        }
    }

    [Test]
    public void UnderflowDoesNotRepeatLastEffect()
    {
        var queue = new NativeHapticsQueue();
        var pcm = new byte[512*4];
        for(int i=0;i<pcm.Length;i+=2) BinaryPrimitives.WriteInt16LittleEndian(pcm.AsSpan(i,2),8192);
        queue.Feed(pcm); byte[] output = new byte[64]; queue.Read(output);
        Assert.That(output.All(x=>x==32),Is.True);
        queue.Read(output); Assert.That(output.All(x=>x==0),Is.True);
    }

    [Test]
    public void FlushDiscardsPartialFramesAndOldEffects()
    {
        var queue = new NativeHapticsQueue();
        var partial = Enumerable.Repeat((byte)0x7f,15*4).ToArray();
        queue.Feed(partial); queue.Clear(); queue.Feed(new byte[16*4]);
        byte[] output = new byte[2]; queue.Read(output);
        Assert.That(output,Is.EqualTo(new byte[2])); Assert.That(queue.Count,Is.Zero);
    }

    [Test]
    public void OverflowIsBoundedAndKeepsWholeStereoFrames()
    {
        var queue = new NativeHapticsQueue(); queue.Feed(new byte[48000*4]);
        Assert.That(queue.Count,Is.EqualTo(2048));
        Assert.Throws<ArgumentException>(()=>queue.Feed(new byte[3]));
        Assert.Throws<ArgumentException>(()=>queue.Read(new byte[3]));
    }
}
