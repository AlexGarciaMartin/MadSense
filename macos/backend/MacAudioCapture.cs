using System.Buffers.Binary;
using System.Runtime.InteropServices;
using System.Text;
using DualSenseClient.Controllers.DualSense.Audio;
using DualSenseClient.Controllers.Emulation;
using DualSenseClient.Core.Utilities;

namespace MadSense.Mac;

internal static class PcmLanes
{
    // HAL delivers front L/R for speaker and rear L/R for actuators. Never derive
    // haptics from the speaker pair, including when the rear pair is silent.
    internal static void Split(ReadOnlySpan<float> quad, Span<float> stereo, Span<byte> haptics)
    {
        if (quad.Length % 4 != 0 || stereo.Length < quad.Length / 2 || haptics.Length < quad.Length)
            throw new ArgumentException("A four-channel PCM frame and sufficient lane buffers are required.");
        for (int frame = 0; frame < quad.Length / 4; frame++)
        {
            stereo[frame * 2] = Finite(quad[frame * 4]);
            stereo[frame * 2 + 1] = Finite(quad[frame * 4 + 1]);
            BinaryPrimitives.WriteInt16LittleEndian(haptics.Slice(frame * 4, 2), ToInt16(quad[frame * 4 + 2]));
            BinaryPrimitives.WriteInt16LittleEndian(haptics.Slice(frame * 4 + 2, 2), ToInt16(quad[frame * 4 + 3]));
        }
    }
    private static float Finite(float sample) => float.IsFinite(sample) ? Math.Clamp(sample, -1f, 1f) : 0f;
    private static short ToInt16(float sample) => (short)Math.Clamp((int)MathF.Round(Finite(sample) * 32768f), short.MinValue, short.MaxValue);
}

/// <summary>Optional CoreAudio HAL capture. This consumes only the dedicated
/// MadSense endpoint; it does not change or record the system default output.</summary>
internal sealed class MacAudioCapture : IDisposable
{
    private readonly DualSenseAudioForwarder forwarder;
    private readonly VirtualDualSenseController adapter;
    private readonly Func<string?>? transportFault;
    private readonly CancellationTokenSource stop = new();
    private readonly Thread worker;
    private IntPtr handle;
    private string? fault;
    private long dropped;
    private int active;
    public string? Fault => Volatile.Read(ref fault) ?? transportFault?.Invoke() ?? forwarder.LastFault;
    public long DroppedFrames => Interlocked.Read(ref dropped);
    public int ActiveClients => Volatile.Read(ref active);
    public bool PlayToHeadset { set => forwarder.PlayToHeadset = value; }
    public void SetSpeaker(string value) => SpeakerLevels.Apply(forwarder, value);

    public MacAudioCapture(IDualSenseAudioOutputs outputs, VirtualDualSenseController adapter,
        Func<string?>? transportFault = null, string speaker = "high")
    {
        this.transportFault = transportFault;
        if (AbiVersion() != 2) throw new IOException("The MadSense CoreAudio bridge ABI is incompatible; reinstall its matching package.");
        byte[] error = new byte[2048];
        int result = Open(out handle, error, (uint)error.Length);
        if (result != 0 || handle == IntPtr.Zero)
        {
            if (handle != IntPtr.Zero) { Close(handle); handle = IntPtr.Zero; }
            int end = Array.IndexOf(error, (byte)0);
            throw new IOException($"Audio endpoint unavailable (0x{result:x8}): {Encoding.UTF8.GetString(error, 0, end < 0 ? error.Length : end)}");
        }
        this.adapter = adapter;
        forwarder = new DualSenseAudioForwarder(outputs, null)
        {
            StrictNativeHaptics = true, SpeakerVolume = 0x64, SpeakerPreGain = 7,
            HapticsEnabled = true, ForceAudioHaptics = false
        };
        worker = new Thread(Pump) { IsBackground = true, Name = "MadSense CoreAudio capture" };
        try
        {
            SetSpeaker(speaker);
            adapter.OutputStateReceived += forwarder.UpdateGameOutputState;
            if (!forwarder.Start()) throw new IOException("Bluetooth audio forwarding is unavailable.");
            worker.Start();
        }
        catch
        {
            adapter.OutputStateReceived -= forwarder.UpdateGameOutputState;
            forwarder.Dispose(); Close(handle); handle = IntPtr.Zero; stop.Dispose();
            throw;
        }
    }

    private void Pump()
    {
        float[] quad = new float[480 * 4];
        float[] stereo = new float[480 * 2];
        byte[] haptics = new byte[480 * 4];
        bool wasActive = false;
        ulong? previousGeneration = null;
        ulong previousDropped = 0;
        try
        {
            using IDisposable? scheduling = MultimediaThread.Enter("Pro Audio");
            while (!stop.IsCancellationRequested)
            {
                // The Bluetooth writer is a separate worker. Its failure must
                // stop capture and mark optional audio unavailable without
                // tearing down the otherwise usable HID bridge.
                if ((transportFault?.Invoke() ?? forwarder.LastFault) is { } failure)
                    throw new IOException(failure);
                int result = Read(handle, quad, 480, out uint frames, out ulong droppedFrames, out uint clients, out ulong generation);
                if (result != 0) throw new IOException($"CoreAudio capture failed (0x{result:x8}).");
                if (frames > 480) throw new IOException("CoreAudio returned an oversized frame block.");
                // A stop/start can occur entirely between polls. Native stream
                // generation and drop counters expose that boundary; discard
                // partial PCM before feeding samples from the new generation.
                if (previousGeneration != generation || previousDropped != droppedFrames)
                    forwarder.Flush(preserveGameOutputState: true);
                previousGeneration = generation;
                previousDropped = droppedFrames;
                Interlocked.Exchange(ref dropped, (long)Math.Min(droppedFrames, (ulong)long.MaxValue));
                Volatile.Write(ref active, (int)Math.Min(clients, int.MaxValue));
                if (clients == 0)
                {
                    if (wasActive) forwarder.Flush(preserveGameOutputState: true);
                    wasActive = false;
                }
                else if (frames > 0)
                {
                    wasActive = true;
                    int frameCount = (int)frames;
                    PcmLanes.Split(quad.AsSpan(0, frameCount * 4), stereo, haptics);
                    // The existing forwarder accepts arbitrary chunk boundaries and
                    // retains silence; it performs the Bluetooth clock conversion.
                    forwarder.UpdateGameHaptics(haptics.AsSpan(0, frameCount * 4).ToArray());
                    forwarder.FeedPcm(stereo.AsSpan(0, frameCount * 2));
                }
                // A five-millisecond idle wait can be coalesced beyond the
                // endpoint's bounded backlog. Stay cancellable and blocking,
                // but poll at one millisecond when a full packet is unavailable.
                if (frames < 480) stop.Token.WaitHandle.WaitOne(1);
            }
        }
        catch (Exception e) { Volatile.Write(ref fault, e.Message); }
        finally { forwarder.Flush(); forwarder.Stop(); Volatile.Write(ref active, 0); }
    }

    public void Dispose()
    {
        if (handle == IntPtr.Zero) return;
        stop.Cancel();
        worker.Join(); // AudioObject calls complete before destroying the lease.
        adapter.OutputStateReceived -= forwarder.UpdateGameOutputState;
        forwarder.Dispose();
        Close(handle); handle = IntPtr.Zero;
        stop.Dispose();
    }

    [DllImport("madsense_audio", EntryPoint = "ms_audio_open", CallingConvention = CallingConvention.Cdecl)]
    private static extern int Open(out IntPtr handle, byte[] error, uint capacity);
    [DllImport("madsense_audio", EntryPoint = "ms_audio_abi_version", CallingConvention = CallingConvention.Cdecl)]
    private static extern uint AbiVersion();
    [DllImport("madsense_audio", EntryPoint = "ms_audio_read", CallingConvention = CallingConvention.Cdecl)]
    private static extern int Read(IntPtr handle, [Out] float[] samples, uint capacityFrames, out uint frames, out ulong droppedFrames, out uint activeClients, out ulong generation);
    [DllImport("madsense_audio", EntryPoint = "ms_audio_close", CallingConvention = CallingConvention.Cdecl)]
    private static extern void Close(IntPtr handle);
}
