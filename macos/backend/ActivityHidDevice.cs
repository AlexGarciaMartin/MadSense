using System.Text.Json.Serialization;
using DualSenseClient.Controllers.DualSense.Output;
using DualSenseClient.Hid;

namespace MadSense.Mac;

/// <summary>Delivered output commands/data, not confirmation of actuator motion.</summary>
internal readonly record struct ControllerActivity(
    [property: JsonPropertyName("haptics")] bool Haptics,
    [property: JsonPropertyName("triggers")] bool Triggers,
    [property: JsonPropertyName("audio")] bool Audio,
    [property: JsonPropertyName("rumble")] bool Rumble);

/// <summary>Meters complete physical HID writes. PCM/audio expire after 1.5s;
/// trigger and rumble commands persist until a delivered command replaces them.</summary>
internal sealed class ActivityHidDevice(IHidDevice inner, Func<long>? timestamp = null) : IHidDevice
{
    private readonly Lock stateLock = new();
    private readonly Func<long> clock = timestamp ?? (() => Environment.TickCount64);
    private long? lastHaptics, lastAudio;
    private bool leftTrigger, rightTrigger, rumble, disconnected, disposed;

    public ControllerActivity Activity
    {
        get
        {
            lock (stateLock)
            {
                if (disconnected || disposed) return default;
                long now = clock();
                return new(Recent(lastHaptics, now), leftTrigger || rightTrigger, Recent(lastAudio, now), rumble);
            }
        }
    }

    private static bool Recent(long? last, long now) => last is { } value && now >= value && now - value < 1500;

    public int Write(byte[] buffer, int offset, int count)
    {
        // Never hold the status lock across I/O: a pending write is not activity.
        int result = inner.Write(buffer, offset, count);
        if (result == count && count > 0) RecordDelivered(buffer.AsSpan(offset, count));
        return result;
    }

    private void RecordDelivered(ReadOnlySpan<byte> report)
    {
        lock (stateLock)
        {
            // A write completing after a disconnect/dispose must not revive UI state.
            if (disconnected || disposed) return;
            long now = clock();
            int stateOffset = -1;
            bool standalone = report[0] == 0x31 && report.Length == 78;
            if (standalone) stateOffset = 3;
            else if (report[0] == 0x32 && report.Length == BluetoothAudioReportBuilder.HapticsReportSize)
            {
                if (report[2] == 0x90 && report[3] == 63) stateOffset = 4; // Init-prime also carries trigger state.
                else if (report[11] == 0x92 && report[12] == 64 && report.Slice(13, 64).ContainsAnyExcept((byte)0))
                    lastHaptics = now;
            }
            else if (report[0] == 0x35 && report.Length == BluetoothAudioReportBuilder.AudioReportSize)
            {
                if (IsAudioPacket(report, 11)) lastAudio = now;
            }
            else if (report[0] == 0x36 && report.Length == BluetoothAudioReportBuilder.CombinedReportSize)
            {
                if (report[11] == 0x90 && report[12] == 63) stateOffset = 13;
                if (report[76] == 0x92 && report[77] == 64 && report.Slice(78, 64).ContainsAnyExcept((byte)0))
                    lastHaptics = now;
                if (IsAudioPacket(report, 142)) lastAudio = now;
            }
            if (stateOffset < 0) return;
            byte flags = report[stateOffset];
            // The controller retains each effect when its allow bit is absent.
            if ((flags & (byte)ValidFlags.AllowRightTriggerFfb) != 0)
                rightTrigger = report[stateOffset + 10] is not (0 or 5);
            if ((flags & (byte)ValidFlags.AllowLeftTriggerFfb) != 0)
                leftTrigger = report[stateOffset + 21] is not (0 or 5);
            // In combined audio frames classic rumble is folded into PCM instead.
            if (standalone && (flags & 3) != 0)
                rumble = (report[stateOffset + 2] | report[stateOffset + 3]) != 0;
        }
    }

    private static bool IsAudioPacket(ReadOnlySpan<byte> report, int offset) =>
        report[offset] is (byte)BluetoothAudioRoute.Speaker or (byte)BluetoothAudioRoute.Headset &&
        report[offset + 1] == BluetoothAudioReportBuilder.AudioPayloadSize;

    private void MarkDisconnected()
    {
        lock (stateLock) disconnected = true;
    }

    public int Read(byte[] buffer, int offset, int count, int timeoutMs)
    {
        try { return inner.Read(buffer, offset, count, timeoutMs); }
        catch (Exception e) when (e is HidException or ObjectDisposedException) { MarkDisconnected(); throw; }
    }

    public async Task<int> ReadAsync(byte[] buffer, int offset, int count, CancellationToken ct)
    {
        try { return await inner.ReadAsync(buffer, offset, count, ct); }
        catch (Exception e) when (e is HidException or ObjectDisposedException) { MarkDisconnected(); throw; }
    }

    public bool IsConnected
    {
        get
        {
            bool connected = inner.IsConnected;
            if (!connected) MarkDisconnected();
            return connected;
        }
    }

    public ushort VendorId => inner.VendorId;
    public ushort ProductId => inner.ProductId;
    public string DevicePath => inner.DevicePath;
    public string GetProductName() => inner.GetProductName();
    public byte[] GetFeatureReport(byte reportId, int bufferSize = 64) => inner.GetFeatureReport(reportId, bufferSize);
    public void SendFeatureReport(byte[] buffer, int offset, int count) => inner.SendFeatureReport(buffer, offset, count);

    public void Dispose()
    {
        lock (stateLock)
        {
            if (disposed) return;
            disposed = true;
        }
        inner.Dispose();
    }
}
