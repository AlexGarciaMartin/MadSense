using DualSenseClient.Controllers.Devices;
using DualSenseClient.Controllers.DualSense.Output;
using DualSenseClient.Controllers.Emulation;
using DualSenseClient.Hid;

namespace MadSense.Mac;

/// <summary>
/// Forwards Mac feedback without hiding write failures behind the persona's
/// subscriber exception isolation. The owning session observes the fault even
/// when the virtual persona catches the rethrown exception.
/// </summary>
internal sealed class TrackedOutputs(DualSenseDevice device, Action<string> recordFault)
    : IDualSenseAudioOutputs
{
    private readonly Lock writeLock = new();
    private string? fault;
    private string? audioFault;

    public ConnectionType ConnectionType => device.ConnectionType;
    // The Mac bridge does not run profile-light or special-action overrides.
    public bool NeedsOutputStateBlock => false;
    public string? Fault => Volatile.Read(ref fault);
    public string? AudioFault => Volatile.Read(ref audioFault);

    public void SetVibration(byte left, byte right)
    {
        lock (writeLock)
        {
            try
            {
                EnsureLive();
                // The device's convenience SetVibration logs and swallows HID
                // errors. Use its same minimal state through the observable
                // SendOutputState primitive so the session can react to failure.
                device.SendOutputState(new SetStateData
                {
                    ValidFlag0 = ValidFlags.UseRumbleNotHaptics |
                        (device.UsesVibrationV2 ? ValidFlags.None : ValidFlags.EnableRumbleEmulation),
                    ValidFlag2 = device.UsesVibrationV2 ? ValidFlags.EnableImprovedRumbleEmu : ValidFlags.None,
                    RumbleLeft = left, RumbleRight = right
                });
            }
            catch (Exception e) { Record(e); throw; }
        }
    }

    public void SendOutputState(SetStateData payload)
    {
        lock (writeLock)
        {
            try { EnsureLive(); device.SendOutputState(payload); }
            catch (Exception e) { Record(e); throw; }
        }
    }

    public void ResetBluetoothAudioStream()
    {
        lock (writeLock)
        {
            try { EnsureLive(); device.ResetBluetoothAudioStream(); }
            catch (Exception e) { RecordAudio(e); throw; }
        }
    }

    public void SendBluetoothAudioPrime(SetStateData state)
    {
        lock (writeLock)
        {
            try { EnsureLive(); device.SendBluetoothAudioPrime(state); }
            catch (Exception e) { RecordAudio(e); throw; }
        }
    }

    public void SendBluetoothAudioAndHaptics(SetStateData state, ReadOnlySpan<byte> opusFrame,
        ReadOnlySpan<byte> hapticsPcm, BluetoothAudioRoute route)
    {
        lock (writeLock)
        {
            try { EnsureLive(); device.SendBluetoothAudioAndHaptics(state, opusFrame, hapticsPcm, route); }
            catch (Exception e) { RecordAudio(e); throw; }
        }
    }

    public void SendBluetoothAudio(ReadOnlySpan<byte> opusFrame, BluetoothAudioRoute route)
    {
        lock (writeLock)
        {
            try { EnsureLive(); device.SendBluetoothAudio(opusFrame, route); }
            catch (Exception e) { RecordAudio(e); throw; }
        }
    }

    public void SetAudioOutput(AudioControl outputControl, byte speakerVolume,
        byte headphoneVolume, byte preampGain = 0x02)
    {
        lock (writeLock)
        {
            try { EnsureLive(); device.SetAudioOutput(outputControl, speakerVolume, headphoneVolume, preampGain); }
            catch (Exception e) { RecordAudio(e); throw; }
        }
    }

    private void EnsureLive() => ObjectDisposedException.ThrowIf(device.IsDisposed, device);

    private void RecordAudio(Exception error) =>
        Interlocked.CompareExchange(ref audioFault, $"Controller audio failed: {error.Message}", null);

    private void Record(Exception error)
    {
        string message = $"Controller feedback failed: {error.Message}";
        // Preserve the initiating error, including failures swallowed upstream.
        if (Interlocked.CompareExchange(ref fault, message, null) != null) return;
        try { recordFault(message); }
        catch { /* A reporting failure must not replace the original I/O error. */ }
    }
}
