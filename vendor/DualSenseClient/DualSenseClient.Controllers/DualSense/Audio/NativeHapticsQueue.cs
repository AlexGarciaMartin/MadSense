// SenseBridge modification, 2026-10-31. GPL-3.0, like the containing project.
using System.Buffers.Binary;

namespace DualSenseClient.Controllers.DualSense.Audio;

/// <summary>
/// Continuous 48 kHz stereo S16LE to 3 kHz stereo S8 conversion. USB packet
/// boundaries do not reset the 16-frame decimator. Each Bluetooth sample is consumed
/// once; underflow emits silence rather than repeating a previous waveform.
/// Callers serialize access. The bounded queue drops the oldest stereo frame on overflow.
/// </summary>
public sealed class NativeHapticsQueue
{
    private readonly byte[] ring = new byte[2048];
    private int head, count, partial, leftSum, rightSum;
    public int Count => count;
    public void Feed(ReadOnlySpan<byte> pcm)
    {
        if (pcm.Length % 4 != 0) throw new ArgumentException("Expected complete S16LE stereo frames.", nameof(pcm));
        for (int i = 0; i < pcm.Length; i += 4)
        {
            leftSum += BinaryPrimitives.ReadInt16LittleEndian(pcm.Slice(i, 2));
            rightSum += BinaryPrimitives.ReadInt16LittleEndian(pcm.Slice(i + 2, 2));
            if (++partial != 16) continue;
            if (count == ring.Length) { head = (head + 2) % ring.Length; count -= 2; }
            ring[(head + count) % ring.Length] = QuantizeSum(leftSum);
            ring[(head + count + 1) % ring.Length] = QuantizeSum(rightSum);
            count += 2; partial = leftSum = rightSum = 0;
        }
    }
    // 16 input frames * 256 S16 units per S8 unit. Nearest/ties-even minimizes
    // quantization error without a sign bias at half steps; truncation erased
    // quiet averages between half and one output LSB. Saturate, never wrap.
    private static byte QuantizeSum(int sum) => unchecked((byte)(sbyte)Math.Clamp(
        (int)Math.Round(sum / 4096.0, MidpointRounding.ToEven), -128, 127));
    /// <summary>Discard stale complete stereo samples before playback, keeping the newest tail.</summary>
    public void TrimToNewest(int maxBytes)
    {
        if (maxBytes < 0 || (maxBytes & 1) != 0) throw new ArgumentOutOfRangeException(nameof(maxBytes));
        int discard = Math.Max(0, count - maxBytes);
        head = (head + discard) % ring.Length;
        count -= discard;
    }
    public void Read(Span<byte> destination)
    {
        if ((destination.Length & 1) != 0) throw new ArgumentException("Expected stereo output.", nameof(destination));
        destination.Clear();
        int length = Math.Min(count, destination.Length);
        for (int i = 0; i < length; i++) destination[i] = ring[(head + i) % ring.Length];
        head = (head + length) % ring.Length; count -= length;
    }
    public void Clear() { head = count = partial = leftSum = rightSum = 0; }
}
