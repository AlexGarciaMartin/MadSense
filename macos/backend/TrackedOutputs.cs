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

    public ConnectionType ConnectionType => device.ConnectionType;
    // The Mac bridge does not run profile-light or special-action overrides.
    public bool NeedsOutputStateBlock => false;
    public string? Fault => Volatile.Read(ref fault);

    public void SetVibration(byte left, byte right)
    {
        lock (writeLock)
        {
            try { EnsureLive(); device.SetVibration(left, right); }
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
            catch (Exception e) { Record(e); throw; }
        }
    }

    public void SendBluetoothAudioPrime(SetStateData state)
    {
        lock (writeLock)
        {
            try { EnsureLive(); device.SendBluetoothAudioPrime(state); }
            catch (Exception e) { Record(e); throw; }
        }
    }

    public void SendBluetoothAudioAndHaptics(SetStateData state, ReadOnlySpan<byte> opusFrame,
        ReadOnlySpan<byte> hapticsPcm, BluetoothAudioRoute route)
    {
        lock (writeLock)
        {
            try { EnsureLive(); device.SendBluetoothAudioAndHaptics(state, opusFrame, hapticsPcm, route); }
            catch (Exception e) { Record(e); throw; }
        }
    }

    public void SendBluetoothAudio(ReadOnlySpan<byte> opusFrame, BluetoothAudioRoute route)
    {
        lock (writeLock)
        {
            try { EnsureLive(); device.SendBluetoothAudio(opusFrame, route); }
            catch (Exception e) { Record(e); throw; }
        }
    }

    public void SetAudioOutput(AudioControl outputControl, byte speakerVolume,
        byte headphoneVolume, byte preampGain = 0x02)
    {
        lock (writeLock)
        {
            try { EnsureLive(); device.SetAudioOutput(outputControl, speakerVolume, headphoneVolume, preampGain); }
            catch (Exception e) { Record(e); throw; }
        }
    }

    private void EnsureLive() => ObjectDisposedException.ThrowIf(device.IsDisposed, device);

    private void Record(Exception error)
    {
        string message = $"Controller feedback failed: {error.Message}";
        // Preserve the initiating error, including failures swallowed upstream.
        if (Interlocked.CompareExchange(ref fault, message, null) != null) return;
        try { recordFault(message); }
        catch { /* A reporting failure must not replace the original I/O error. */ }
    }
}
