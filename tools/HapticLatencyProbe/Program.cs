using System.Buffers.Binary;
using System.Diagnostics;
using System.Globalization;
using System.Text.Json;
using DualSenseClient.Controllers.Devices;
using DualSenseClient.Controllers.DualSense.Audio;
using DualSenseClient.Controllers.DualSense.Output;
using DualSenseClient.Core.Utilities;
using DualSenseClient.Hid;
using HapticLatencyProbe;

// Measures real tactile latency without external equipment: writes a haptic impulse to the
// virtual DualSense audio endpoint (rear channels, the way a game does) and detects the
// shake in the physical pad's accelerometer, read over Bluetooth. Works for any bridge
// (MadSense, DSX, ...) that exposes a DualSense render endpoint and leaves the physical pad
// readable by this EXE (HidHide whitelist). --direct skips every virtual layer and sends the
// impulse straight to the physical pad in Bluetooth report 0x36: the floor of this pad on
// this PC (radio, firmware, actuator rise, input report). The pad must rest untouched.
static class Program
{
    record Accel(long Tick, short X, short Y, short Z);
    record Trial(int Index, long WriteTick, double QueuedAheadMs, double? WriteToSpikeMs, double Peak, double BaselineSigma, int BaselineSamples);
    static readonly JsonSerializerOptions Json = new() { WriteIndented = true };
    static string? Opt(string[] a, string key) { var i = Array.IndexOf(a, key); return i >= 0 && i + 1 < a.Length ? a[i + 1] : null; }

    static int Main(string[] args)
    {
        try { return Run(args); }
        catch (Exception e) { Console.Error.WriteLine(e); return 1; }
    }

    sealed class Settings(string[] args)
    {
        public string Label = Opt(args, "--label") ?? "unnamed";
        public string Output = Opt(args, "--out") ?? "haptic-latency.json";
        public int Trials = Math.Clamp(int.Parse(Opt(args, "--trials") ?? "15"), 1, 200);
        public int SpacingMs = Math.Clamp(int.Parse(Opt(args, "--spacing-ms") ?? "2000"), 500, 10000);
        public double Frequency = double.Parse(Opt(args, "--hz") ?? "100", CultureInfo.InvariantCulture);
        public int ImpulseMs = Math.Clamp(int.Parse(Opt(args, "--impulse-ms") ?? "60"), 5, 500);
        public double Amplitude = double.Parse(Opt(args, "--amplitude") ?? "0.9", CultureInfo.InvariantCulture);
        public int TargetAheadFrames = Math.Clamp(int.Parse(Opt(args, "--ahead-frames") ?? "480"), 48, 48000);
        public string EndpointMatch = Opt(args, "--endpoint") ?? "DualSense";
        public string? Physical = Opt(args, "--physical");
        public string Lane = Opt(args, "--lane") ?? "36";
        public byte SessionBuffer = (byte)Convert.ToInt32(Opt(args, "--session-buffer") ?? "0x40", 16);
        /// <summary>--session-bytes 10,40,40,40,40 (hex): the five bytes individually; overrides --session-buffer.</summary>
        public byte[]? SessionBytes = Opt(args, "--session-bytes") is { } sb ? sb.Split(',').Select(x => (byte)Convert.ToInt32(x.Trim(), 16)).ToArray() : null;
        /// <summary>--sustain-ms N: one long tone instead of impulses, to check continuity (dropouts) at the chosen buffer setting.</summary>
        public int SustainMs = Math.Clamp(int.Parse(Opt(args, "--sustain-ms") ?? "0"), 0, 10000);
        /// <summary>--speaker-tone: in --direct mode also play a 440 Hz tone on the speaker lane, so the Opus side is exercised too.</summary>
        public bool SpeakerTone = args.Contains("--speaker-tone");
    }

    static int Run(string[] args)
    {
        var s = new Settings(args);
        if (args.Contains("--list"))
        {
            foreach (var e in WasapiAudioEndpoint.CaptureActiveEndpoints().RenderEndpoints) Console.WriteLine($"render  {e.FriendlyName}  [{e.Id}]");
            return 0;
        }

        if (args.Contains("--speaker-test")) return RunSpeakerTest(s, args);
        if (args.Contains("--endpoint-volume")) return EndpointVolume(s, args);

        using var enumerator = new HidDeviceEnumerator();
        var pads = enumerator.Enumerate(0x054c).Where(x => x.ProductId is 0x0ce6 or 0x0df2 && x.UsagePage == 1 && x.BusType == ConnectionType.Bluetooth).ToArray();
        var info = s.Physical is { } p ? pads.FirstOrDefault(x => x.Path == p) ?? throw new Exception("--physical path not found") : pads.Length == 1 ? pads[0] : throw new Exception($"Expected one Bluetooth DualSense, found {pads.Length}. Authorize this EXE in HidHide or pass --physical.");
        if (Opt(args, "--ladder") is { } ladder) return RunSpeakerLadder(ladder, enumerator, info);
        return args.Contains("--direct") ? RunDirect(s, enumerator, info) : RunVirtual(s, enumerator, info);
    }

    // ── Windows-side volume of the bridge's render endpoint ─────────────────────────────
    // If Windows applies the endpoint volume in software, everything a game writes reaches the
    // bridge already attenuated. --set-endpoint-volume 1.0 sets the master level (0..1).
    static int EndpointVolume(Settings s, string[] args)
    {
        var endpoints = WasapiAudioEndpoint.CaptureActiveEndpoints();
        var candidates = endpoints.RenderEndpoints.Where(e => e.FriendlyName.Contains(s.EndpointMatch, StringComparison.OrdinalIgnoreCase)).ToList();
        if (candidates.Count != 1) throw new Exception($"Expected one render endpoint matching '{s.EndpointMatch}', found {candidates.Count}: {string.Join(" | ", candidates.Select(c => c.FriendlyName))}. Use --list / --endpoint.");
        var endpoint = candidates[0];
        var enumerator = (IMMDeviceEnumerator)Activator.CreateInstance(Type.GetTypeFromCLSID(new Guid("BCDE0395-E52F-467C-8E3D-C4579291692E"))!)!;
        int hr = enumerator.GetDevice(endpoint.Id, out IMMDevice? device);
        if (hr < 0 || device is null) throw new Exception($"IMMDeviceEnumerator.GetDevice failed: 0x{hr:X8}");
        Guid iid = new Guid("5CDF2C82-841E-4546-9722-0CF74078229A");
        hr = device.Activate(ref iid, 1, IntPtr.Zero, out object instance);
        if (hr < 0) throw new Exception($"IMMDevice.Activate(IAudioEndpointVolume) failed: 0x{hr:X8}");
        var volume = (IAudioEndpointVolume)instance;
        if (Opt(args, "--set-endpoint-volume") is { } set)
        {
            float level = float.Parse(set, CultureInfo.InvariantCulture);
            Guid ctx = Guid.Empty;
            hr = volume.SetMasterVolumeLevelScalar(Math.Clamp(level, 0f, 1f), ref ctx);
            if (hr < 0) throw new Exception($"SetMasterVolumeLevelScalar failed: 0x{hr:X8}");
            hr = volume.SetMute(false, ref ctx);
        }
        volume.GetMasterVolumeLevelScalar(out float scalar);
        volume.GetMasterVolumeLevel(out float db);
        volume.GetMute(out bool mute);
        volume.QueryHardwareSupport(out uint hw);
        volume.GetVolumeRange(out float min, out float max, out float step);
        volume.GetChannelCount(out uint channels);
        Console.WriteLine($"endpoint '{endpoint.FriendlyName}': master {scalar * 100:F0} % ({db:F1} dB), mute {mute}, channels {channels}, range {min:F1}..{max:F1} dB step {step:F2}, hardware support mask 0x{hw:X} (bit0 volume, bit1 mute, bit2 meter; 0 = Windows applies the level in software)");
        for (uint c = 0; c < channels; c++) { volume.GetChannelVolumeLevelScalar(c, out float cl); Console.WriteLine($"  channel {c}: {cl * 100:F0} %"); }
        return 0;
    }

    // ── Direct speaker ladder: tone segments with different volume byte / pre-gain, by ear ──
    // --ladder 64:2,64:7,FF:2,FF:7  (hex volume byte : AudioControl2 pre-gain 0-7). Each step is
    // 2 s of 440 Hz tone at 0.5 amplitude plus 1 s of silence, embedded in the per-tick 0x36 state
    // like MadSense does. MadSense must be stopped and this EXE authorized in HidHide.
    static int RunSpeakerLadder(string ladder, HidDeviceEnumerator enumerator, IHidDeviceInfo info)
    {
        var steps = ladder.Split(',').Select(x => x.Split(':')).Select(p => (Volume: (byte)Convert.ToInt32(p[0].Trim(), 16), Gain: (byte)int.Parse(p[1].Trim()))).ToList();
        using var pad = new DualSenseDevice(enumerator.OpenDevice(info.Path), info);
        using var pipeline = new DualSenseBtAudioPipeline();
        short[] pcm = new short[DualSenseBtAudioPipeline.OpusFrameSamples * 2];
        byte[] opus = new byte[DualSenseBtAudioPipeline.OpusBytes];
        byte[] haptics = new byte[DualSenseBtAudioPipeline.HapticsBytes];
        SetStateData State(byte volume, byte gain) => new SetStateData
        {
            ValidFlag0 = ValidFlags.AllowSpeakerVolume | ValidFlags.AllowHeadphoneVolume | ValidFlags.AllowAudioControl,
            ValidFlag1 = ValidFlags.AllowAudioControl2,
            SpeakerVolume = volume, HeadphoneVolume = 0x3F, AudioControl = AudioControl.OutputPathSpeaker, AudioControl2 = gain
        };
        TimerResolution.AddRef();
        try
        {
            pad.BluetoothAudioSessionBufferLengths = [0x40, 0x40, 0x40, 0x40, 0x20];
            pad.ResetBluetoothAudioStream();
            pad.SetAudioOutput(AudioControl.OutputPathSpeaker, steps[0].Volume, 0x3F, steps[0].Gain);
            pad.SendBluetoothAudioPrime(State(steps[0].Volume, steps[0].Gain));
            long period = (long)(Stopwatch.Frequency * 512 / 48000.0);
            long start = Stopwatch.GetTimestamp(), next = start;
            double phase = 0; int announced = -1;
            using var mm = MultimediaThread.Enter("Pro Audio");
            while (true)
            {
                double t = (Stopwatch.GetTimestamp() - start) / (double)Stopwatch.Frequency - 1.0; // 1 s of silence first: lane open + warmup
                int step = t < 0 ? -1 : (int)(t / 3.0);
                if (step >= steps.Count) break;
                bool on = step >= 0 && t - step * 3.0 < 2.0;
                var (volume, gain) = step < 0 ? steps[0] : steps[step];
                if (step != announced && step >= 0) { announced = step; Console.Error.WriteLine($"step {step + 1}/{steps.Count}: volume 0x{volume:X2}, pre-gain {gain}"); }
                for (int i = 0; i < DualSenseBtAudioPipeline.OpusFrameSamples; i++)
                {
                    short v = on ? (short)(Math.Sin(phase) * 0.5 * short.MaxValue) : (short)0;
                    pcm[i * 2] = v; pcm[i * 2 + 1] = v;
                    phase += 2 * Math.PI * 440 / 48000.0; if (phase > 2 * Math.PI) phase -= 2 * Math.PI;
                }
                pipeline.EncodeOpus(pcm, opus);
                pad.SendBluetoothAudioAndHaptics(State(volume, gain), opus, haptics, BluetoothAudioRoute.Speaker);
                next = Math.Max(next + period, Stopwatch.GetTimestamp());
                PrecisionWait.SleepUntil(next);
            }
        }
        finally { TimerResolution.Release(); try { pad.ResetOutputs(); } catch { } }
        Console.Error.WriteLine("ladder done");
        return 0;
    }

    // ── Speaker lane through the bridge: beeps on the FRONT channels, a human listens ───────
    // Needs no physical pad and no HidHide entry: it only writes to the bridge's render endpoint,
    // exactly where a game writes controller-speaker audio. Separates "the bridge does not play
    // speaker audio" from "the game never sent any". --hz, --amplitude and --sustain-ms apply.
    static int RunSpeakerTest(Settings s, string[] args)
    {
        var endpoints = WasapiAudioEndpoint.CaptureActiveEndpoints();
        var candidates = endpoints.RenderEndpoints.Where(e => e.FriendlyName.Contains(s.EndpointMatch, StringComparison.OrdinalIgnoreCase)).ToList();
        if (candidates.Count != 1) throw new Exception($"Expected one render endpoint matching '{s.EndpointMatch}', found {candidates.Count}: {string.Join(" | ", candidates.Select(c => c.FriendlyName))}. Use --list / --endpoint.");
        var endpoint = candidates[0];
        using var stream = WasapiAudioEndpoint.OpenRenderEndpoint(endpoint.Id, new WasapiAudioFormat(4, 48000, 16, false), preferShared: true);
        var format = stream.Format;
        double hz = Opt(args, "--hz") is null ? 440 : s.Frequency;
        double amplitude = Opt(args, "--amplitude") is null ? 0.5 : s.Amplitude;
        int totalMs = s.SustainMs > 0 ? s.SustainMs : 4000;
        int rate = format.SampleRateHz, bps = format.BitsPerSample / 8;
        // --lead-ms: in-stream silence before the first beep (is a click tied to the stream start or to
        // silence→sound?); --gap-ms: silence between beeps (does the click depend on the gap length?).
        int leadMs = Math.Clamp(int.Parse(Opt(args, "--lead-ms") ?? "0"), 0, 20000);
        int gapMs = Math.Clamp(int.Parse(Opt(args, "--gap-ms") ?? "200"), 0, 10000);
        int lead = rate * leadMs / 1000;
        int totalFrames = lead + rate * totalMs / 1000;
        int onFrames = rate * 3 / 10, period = onFrames + rate * gapMs / 1000, ramp = rate / 200;
        Console.Error.WriteLine($"endpoint '{endpoint.FriendlyName}' {stream.ShareMode} {format}; {leadMs} ms of silence, then {hz:F0} Hz beeps (300 ms on / {gapMs} ms off) on the FRONT channels for {totalMs} ms, amplitude {amplitude:F2}");

        const int chunk = 480;
        byte[] block = new byte[format.BlockAlign * chunk];
        int written = 0;
        stream.Start();
        while (written < totalFrames)
        {
            int frames = Math.Min(chunk, totalFrames - written);
            for (int f = 0; f < frames; f++)
            {
                int n = written + f, pos = n < lead ? -1 : (n - lead) % period;
                double env = pos < 0 || pos >= onFrames ? 0.0 : Math.Min(1.0, Math.Min(pos, onFrames - pos) / (double)ramp);
                double v = Math.Sin(2 * Math.PI * hz * n / rate) * amplitude * env;
                for (int c = 0; c < format.Channels; c++)
                {
                    double value = c is 0 or 1 ? v : 0.0;
                    int offset = f * format.BlockAlign + c * bps;
                    if (format.IsFloat) BinaryPrimitives.WriteSingleLittleEndian(block.AsSpan(offset, 4), (float)value);
                    else if (format.BitsPerSample == 16) BinaryPrimitives.WriteInt16LittleEndian(block.AsSpan(offset, 2), (short)Math.Round(value * short.MaxValue));
                    else if (format.BitsPerSample == 32) BinaryPrimitives.WriteInt32LittleEndian(block.AsSpan(offset, 4), (int)Math.Round(value * int.MaxValue));
                    else if (format.BitsPerSample == 24) { int sv = (int)Math.Round(value * 8388607); block[offset] = (byte)sv; block[offset + 1] = (byte)(sv >> 8); block[offset + 2] = (byte)(sv >> 16); }
                }
            }
            while (stream.BufferFrames - stream.Padding < frames) Thread.Sleep(1);
            written += stream.WriteFrames(block, frames);
        }
        Thread.Sleep(400); // let the queued tail reach the bridge
        Console.Error.WriteLine("done: if the pad's speaker stayed silent, the bridge's speaker lane is the problem, not the game");
        return 0;
    }

    // ── Through a bridge's virtual DualSense audio endpoint ─────────────────────────────
    static int RunVirtual(Settings s, HidDeviceEnumerator enumerator, IHidDeviceInfo info)
    {
        var endpoints = WasapiAudioEndpoint.CaptureActiveEndpoints();
        var candidates = endpoints.RenderEndpoints.Where(e => e.FriendlyName.Contains(s.EndpointMatch, StringComparison.OrdinalIgnoreCase)).ToList();
        if (candidates.Count != 1) throw new Exception($"Expected one render endpoint matching '{s.EndpointMatch}', found {candidates.Count}: {string.Join(" | ", candidates.Select(c => c.FriendlyName))}. Use --list / --endpoint.");
        var endpoint = candidates[0];

        using var physical = enumerator.OpenDevice(info.Path);
        var samples = new List<Accel>(200000); var samplesLock = new object();
        using var cancel = new CancellationTokenSource();
        var errors = new List<string>();
        var reader = new Thread(() =>
        {
            byte[] raw = new byte[128];
            try
            {
                while (!cancel.IsCancellationRequested)
                {
                    int n = physical.Read(raw, 0, raw.Length, 25);
                    long tick = Stopwatch.GetTimestamp();
                    if (n < 2 + 63 || raw[0] != 0x31) continue;
                    var span = raw.AsSpan(2);
                    var a = new Accel(tick, BinaryPrimitives.ReadInt16LittleEndian(span[21..]), BinaryPrimitives.ReadInt16LittleEndian(span[23..]), BinaryPrimitives.ReadInt16LittleEndian(span[25..]));
                    lock (samplesLock) samples.Add(a);
                }
            }
            catch (Exception e) { lock (errors) errors.Add("reader: " + e.Message); }
        }) { IsBackground = true, Name = "accel-observer", Priority = ThreadPriority.AboveNormal };
        reader.Start();

        using var stream = WasapiAudioEndpoint.OpenRenderEndpoint(endpoint.Id, new WasapiAudioFormat(4, 48000, 16, false), preferShared: true);
        var format = stream.Format;
        if (format.Channels < 4) throw new Exception($"Endpoint format has {format.Channels} channels; the haptic lane needs channels 3-4. Format: {format}");
        Console.Error.WriteLine($"endpoint '{endpoint.FriendlyName}' {stream.ShareMode} {format} buffer {stream.BufferFrames} frames ({stream.BufferFrames * 1000.0 / format.SampleRateHz:F1} ms); {stream.NegotiationNote}");

        int impulseFrames = (int)(format.SampleRateHz * s.ImpulseMs / 1000.0);
        byte[] silenceBlock = new byte[format.BlockAlign * 4800];
        byte[] impulseBlock = BuildImpulse(format, impulseFrames, s.Frequency, s.Amplitude);
        stream.Prefill(silenceBlock, s.TargetAheadFrames);
        stream.Start();
        int pendingImpulse = 0; long impulseWriteTick = 0; int impulseQueuedFrames = 0;
        var feeder = new Thread(() =>
        {
            try
            {
                while (!cancel.IsCancellationRequested)
                {
                    int padding = stream.Padding;
                    int want = s.TargetAheadFrames - padding;
                    if (want > 0)
                    {
                        if (Volatile.Read(ref pendingImpulse) == 1)
                        {
                            impulseQueuedFrames = padding;
                            impulseWriteTick = Stopwatch.GetTimestamp();
                            stream.WriteFrames(impulseBlock, impulseFrames);
                            Volatile.Write(ref pendingImpulse, 2);
                        }
                        else stream.WriteFrames(silenceBlock, Math.Min(want, 4800));
                    }
                    Thread.Sleep(1);
                }
            }
            catch (Exception e) { lock (errors) errors.Add("feeder: " + e.Message); }
        }) { IsBackground = true, Name = "render-feeder", Priority = ThreadPriority.AboveNormal };
        feeder.Start();

        Thread.Sleep(1500);
        var trials = new List<Trial>();
        for (int t = 0; t < s.Trials; t++)
        {
            Thread.Sleep(s.SpacingMs);
            Volatile.Write(ref pendingImpulse, 1);
            var deadline = Stopwatch.GetTimestamp() + Stopwatch.Frequency;
            while (Volatile.Read(ref pendingImpulse) != 2 && Stopwatch.GetTimestamp() < deadline) Thread.Sleep(1);
            if (Volatile.Read(ref pendingImpulse) != 2) { lock (errors) errors.Add($"trial {t}: impulse was not written within 1 s"); Volatile.Write(ref pendingImpulse, 0); continue; }
            long writeTick = impulseWriteTick; double queuedMs = impulseQueuedFrames * 1000.0 / format.SampleRateHz;
            Volatile.Write(ref pendingImpulse, 0);
            Thread.Sleep(450);
            Accel[] window; lock (samplesLock) window = Window(samples, writeTick);
            var trial = Detect(t, writeTick, queuedMs, window, errors);
            if (trial is not null) trials.Add(trial);
        }
        cancel.Cancel(); feeder.Join(1000); reader.Join(1000);
        return Report(s, trials, errors, new
        {
            Mode = "virtual", Endpoint = endpoint.FriendlyName, ShareMode = stream.ShareMode.ToString(), Format = format.ToString(), BufferFrames = stream.BufferFrames, TargetAheadFrames = s.TargetAheadFrames,
            Metric = "Host timestamp of the WASAPI write that begins the impulse → first physical accelerometer sample (Bluetooth input report) deviating > max(8σ, 250) from the pre-impulse baseline. Includes Windows audio engine, bridge, Bluetooth output, firmware, actuator rise, Bluetooth input and observer scheduling."
        });
    }

    // ── Straight to the physical pad over Bluetooth (no virtual layer): the floor ───────
    static int RunDirect(Settings s, HidDeviceEnumerator enumerator, IHidDeviceInfo info)
    {
        using var pad = new DualSenseDevice(enumerator.OpenDevice(info.Path), info);
        var samples = new List<Accel>(200000); var samplesLock = new object();
        var errors = new List<string>();
        pad.InputReportReceived += (_, r) => { var a = new Accel(Stopwatch.GetTimestamp(), r.Motion.AccelX, r.Motion.AccelY, r.Motion.AccelZ); lock (samplesLock) samples.Add(a); };

        // Same lane MadSense opens: 0x32 init-prime, then one combined 0x36 report every 512/48000 s.
        var state = new SetStateData
        {
            ValidFlag0 = ValidFlags.AllowSpeakerVolume | ValidFlags.AllowHeadphoneVolume | ValidFlags.AllowAudioControl,
            ValidFlag1 = ValidFlags.AllowAudioControl2,
            SpeakerVolume = 0x50, HeadphoneVolume = 0x3F, AudioControl = AudioControl.OutputPathSpeaker, AudioControl2 = 0x02
        };
        using var pipeline = new DualSenseBtAudioPipeline();
        short[] silence = new short[DualSenseBtAudioPipeline.OpusFrameSamples * 2];
        byte[] opus = new byte[DualSenseBtAudioPipeline.OpusBytes];
        byte[] haptics = new byte[DualSenseBtAudioPipeline.HapticsBytes];
        // Impulse at the haptics lane's native 3 kHz s8 stereo, consumed 64 bytes per report.
        int impulseFrames = 3000 * (s.SustainMs > 0 ? s.SustainMs : s.ImpulseMs) / 1000;
        byte[] impulse = new byte[impulseFrames * 2];
        int fade = Math.Min(impulseFrames / 4, 15);
        for (int f = 0; f < impulseFrames; f++)
        {
            double env = f >= impulseFrames - fade ? 0.5 * (1 + Math.Cos(Math.PI * (f - (impulseFrames - fade)) / fade)) : 1.0;
            sbyte v = (sbyte)Math.Clamp((int)Math.Round(Math.Sin(2 * Math.PI * s.Frequency * f / 3000.0) * s.Amplitude * 127 * env), -128, 127);
            impulse[f * 2] = (byte)v; impulse[f * 2 + 1] = (byte)v;
        }

        using var cancel = new CancellationTokenSource();
        int pendingImpulse = 0; long impulseWriteTick = 0; int impulseOffset = 0;
        bool lane32 = s.Lane == "32";
        TimerResolution.AddRef();
        var pump = new Thread(() =>
        {
            try
            {
                if (s.SessionBytes is { } bytes) pad.BluetoothAudioSessionBufferLengths = bytes;
                else pad.BluetoothAudioSessionBufferLength = s.SessionBuffer;
                double speakerPhase = 0;
                pad.ResetBluetoothAudioStream();
                pad.SetAudioOutput(AudioControl.OutputPathSpeaker, 0x50, 0x3F);
                pad.SendBluetoothAudioPrime(state);
                long period = (long)(Stopwatch.Frequency * 512 / 48000.0);
                long next = Stopwatch.GetTimestamp();
                using var mm = MultimediaThread.Enter("Pro Audio");
                while (!cancel.IsCancellationRequested)
                {
                    haptics.AsSpan().Clear();
                    if (s.SpeakerTone)
                    {
                        for (int i = 0; i < DualSenseBtAudioPipeline.OpusFrameSamples; i++)
                        {
                            short v = (short)(Math.Sin(speakerPhase) * 0.3 * short.MaxValue);
                            silence[i * 2] = v; silence[i * 2 + 1] = v;
                            speakerPhase += 2 * Math.PI * 440 / 48000.0; if (speakerPhase > 2 * Math.PI) speakerPhase -= 2 * Math.PI;
                        }
                    }
                    pipeline.EncodeOpus(silence, opus);
                    bool first = false;
                    if (Volatile.Read(ref pendingImpulse) == 1) { impulseOffset = 0; first = true; Volatile.Write(ref pendingImpulse, 2); }
                    if (Volatile.Read(ref pendingImpulse) == 2)
                    {
                        int n = Math.Min(haptics.Length, impulse.Length - impulseOffset);
                        impulse.AsSpan(impulseOffset, n).CopyTo(haptics);
                        impulseOffset += n;
                        if (impulseOffset >= impulse.Length) Volatile.Write(ref pendingImpulse, 3);
                    }
                    if (first) impulseWriteTick = Stopwatch.GetTimestamp();
                    // --lane 32: haptics-only report 0x32 (no Opus lane) to see whether the firmware
                    // buffers it less than the combined 0x36 that every bridge uses.
                    if (lane32) pad.SendBluetoothHaptics(haptics);
                    else pad.SendBluetoothAudioAndHaptics(state, opus, haptics, BluetoothAudioRoute.Speaker);
                    next = Math.Max(next + period, Stopwatch.GetTimestamp());
                    PrecisionWait.SleepUntil(next);
                }
            }
            catch (Exception e) { lock (errors) errors.Add("pump: " + e.Message); }
        }) { IsBackground = true, Name = "direct-bt-pump" };
        pump.Start();

        Thread.Sleep(1500); // Let the lane open (8 warmup-equivalent packets) and the observer settle.
        var trials = new List<Trial>();
        var continuity = new List<object>();
        for (int t = 0; t < s.Trials; t++)
        {
            Thread.Sleep(s.SpacingMs);
            Volatile.Write(ref pendingImpulse, 1);
            var deadline = Stopwatch.GetTimestamp() + Stopwatch.Frequency;
            while (Volatile.Read(ref pendingImpulse) < 2 && Stopwatch.GetTimestamp() < deadline) Thread.Sleep(1);
            if (Volatile.Read(ref pendingImpulse) < 2) { lock (errors) errors.Add($"trial {t}: impulse was not sent within 1 s"); Volatile.Write(ref pendingImpulse, 0); continue; }
            long writeTick = impulseWriteTick;
            Thread.Sleep(450 + s.SustainMs);
            Volatile.Write(ref pendingImpulse, 0);
            Accel[] window; lock (samplesLock) window = Window(samples, writeTick, 450 + s.SustainMs);
            var trial = Detect(t, writeTick, 0, window, errors);
            if (trial is not null) trials.Add(trial);
            if (s.SustainMs > 0) continuity.Add(Continuity(t, writeTick, s.SustainMs, window));
        }
        cancel.Cancel(); pump.Join(1000);
        try { pad.ResetOutputs(); } catch { }
        TimerResolution.Release();
        return Report(s, trials, errors, new
        {
            Mode = "direct", Lane = lane32 ? "0x32 haptics-only" : "0x36 combined audio+haptics",
            SessionBufferBytes = string.Join(",", pad.BluetoothAudioSessionBufferLengths.Select(b => $"{b:X2}")), SpeakerTone = s.SpeakerTone, SustainMs = s.SustainMs,
            Continuity = s.SustainMs > 0 ? continuity : null,
            Metric = "Host timestamp of the hid_write of the first Bluetooth report carrying the impulse → first physical accelerometer sample deviating > max(8σ, 250) from baseline. No virtual device, no audio engine, no USB/IP: radio, firmware, actuator rise, input report and observer only. Lower bound for any bridge on this PC and pad."
        });
    }

    // ── Shared pieces ───────────────────────────────────────────────────────────────────
    static Accel[] Window(List<Accel> samples, long writeTick, int afterMs = 450) =>
        samples.Where(x => x.Tick >= writeTick - Stopwatch.Frequency * 3 / 10 && x.Tick <= writeTick + Stopwatch.Frequency * afterMs / 1000).ToArray();

    /// <summary>
    /// Continuity of a sustained tone: RMS deviation per 20 ms window (two cycles of a 100 Hz tone)
    /// between 120 ms after the write and 20 ms before the tone ends. A window under 25 % of the
    /// median RMS counts as a dropout.
    /// </summary>
    static object Continuity(int t, long writeTick, int sustainMs, Accel[] window)
    {
        var baseline = window.Where(x => x.Tick < writeTick).ToArray();
        double mx = baseline.Average(x => (double)x.X), my = baseline.Average(x => (double)x.Y), mz = baseline.Average(x => (double)x.Z);
        long start = writeTick + Stopwatch.Frequency * 120 / 1000, end = writeTick + Stopwatch.Frequency * (sustainMs - 20) / 1000;
        long step = Stopwatch.Frequency / 50;
        var rms = new List<double>();
        for (long w = start; w + step <= end; w += step)
        {
            var inWindow = window.Where(x => x.Tick >= w && x.Tick < w + step).Select(x => Dev(x, mx, my, mz)).ToArray();
            if (inWindow.Length > 0) rms.Add(Math.Sqrt(inWindow.Average(d => d * d)));
        }
        var sorted = rms.OrderBy(v => v).ToArray();
        double median = sorted.Length == 0 ? 0 : sorted[sorted.Length / 2];
        int dropouts = rms.Count(v => v < 0.25 * median);
        Console.Error.WriteLine($"trial {t}: continuity windows {rms.Count}, median RMS {median:F0}, min RMS {(sorted.Length == 0 ? 0 : sorted[0]):F0}, dropouts {dropouts}");
        return new { Trial = t, Windows = rms.Count, MedianRms = median, MinRms = sorted.Length == 0 ? 0 : sorted[0], Dropouts = dropouts, DropoutWindowsMs = dropouts * 20, WindowRms = rms.Select(v => Math.Round(v)).ToArray() };
    }

    static Trial? Detect(int t, long writeTick, double queuedMs, Accel[] window, List<string> errors)
    {
        var baseline = window.Where(x => x.Tick < writeTick).ToArray();
        if (baseline.Length < 20) { lock (errors) errors.Add($"trial {t}: only {baseline.Length} baseline samples"); return null; }
        double mx = baseline.Average(x => (double)x.X), my = baseline.Average(x => (double)x.Y), mz = baseline.Average(x => (double)x.Z);
        double sigma = Math.Sqrt(baseline.Average(x => Dev(x, mx, my, mz) * Dev(x, mx, my, mz)));
        double threshold = Math.Max(8 * sigma, 250);
        Accel? spike = window.FirstOrDefault(x => x.Tick >= writeTick && Dev(x, mx, my, mz) > threshold);
        double peak = window.Where(x => x.Tick >= writeTick).Select(x => Dev(x, mx, my, mz)).DefaultIfEmpty(0).Max();
        double? writeToSpike = spike is null ? null : Ms(spike.Tick - writeTick);
        Console.Error.WriteLine($"trial {t}: queued {queuedMs:F1} ms, write→spike {(writeToSpike?.ToString("F1") ?? "none")} ms, peak {peak:F0}, sigma {sigma:F1}");
        return new Trial(t, writeTick, queuedMs, writeToSpike, peak, sigma, baseline.Length);
    }

    static int Report(Settings s, List<Trial> trials, List<string> errors, object mode)
    {
        var hits = trials.Where(r => r.WriteToSpikeMs is not null).ToList();
        var result = new
        {
            s.Label, Timestamp = DateTimeOffset.Now, Details = mode,
            Impulse = new { s.Frequency, s.ImpulseMs, s.Amplitude, Channels = "haptic (rear) pair only" },
            Trials = trials.Count, Detected = hits.Count,
            WriteToSpikeMs = Stats(hits.Select(h => h.WriteToSpikeMs!.Value).ToArray()),
            QueuedAheadMs = Stats(trials.Select(h => h.QueuedAheadMs).ToArray()),
            PeakDeviation = Stats(trials.Select(h => h.Peak).ToArray()),
            PerTrial = trials, Errors = errors,
            Caveats = "Pad must rest untouched. Compare only runs made with this same tool and settings. Not a PS5 measurement."
        };
        Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(s.Output))!);
        File.WriteAllText(s.Output, JsonSerializer.Serialize(result, Json));
        Console.WriteLine(JsonSerializer.Serialize(result, Json));
        return hits.Count > 0 ? 0 : 2;
    }

    static double Dev(Accel x, double mx, double my, double mz) => Math.Sqrt((x.X - mx) * (x.X - mx) + (x.Y - my) * (x.Y - my) + (x.Z - mz) * (x.Z - mz));
    static double Ms(long ticks) => ticks * 1000.0 / Stopwatch.Frequency;

    static object? Stats(double[] a)
    {
        if (a.Length == 0) return null; Array.Sort(a);
        double Q(double q) => a[(int)Math.Round((a.Length - 1) * q)];
        return new { Count = a.Length, Min = a[0], Median = Q(.5), P95 = Q(.95), Max = a[^1], Mean = a.Average() };
    }

    /// <summary><c>IAudioEndpointVolume</c> ({5CDF2C82-841E-4546-9722-0CF74078229A}), vtable order per the Windows SDK.</summary>
    [System.Runtime.InteropServices.ComImport]
    [System.Runtime.InteropServices.Guid("5CDF2C82-841E-4546-9722-0CF74078229A")]
    [System.Runtime.InteropServices.InterfaceType(System.Runtime.InteropServices.ComInterfaceType.InterfaceIsIUnknown)]
    interface IAudioEndpointVolume
    {
        [System.Runtime.InteropServices.PreserveSig] int RegisterControlChangeNotify(IntPtr notify);
        [System.Runtime.InteropServices.PreserveSig] int UnregisterControlChangeNotify(IntPtr notify);
        [System.Runtime.InteropServices.PreserveSig] int GetChannelCount(out uint count);
        [System.Runtime.InteropServices.PreserveSig] int SetMasterVolumeLevel(float levelDb, ref Guid context);
        [System.Runtime.InteropServices.PreserveSig] int SetMasterVolumeLevelScalar(float level, ref Guid context);
        [System.Runtime.InteropServices.PreserveSig] int GetMasterVolumeLevel(out float levelDb);
        [System.Runtime.InteropServices.PreserveSig] int GetMasterVolumeLevelScalar(out float level);
        [System.Runtime.InteropServices.PreserveSig] int SetChannelVolumeLevel(uint channel, float levelDb, ref Guid context);
        [System.Runtime.InteropServices.PreserveSig] int SetChannelVolumeLevelScalar(uint channel, float level, ref Guid context);
        [System.Runtime.InteropServices.PreserveSig] int GetChannelVolumeLevel(uint channel, out float levelDb);
        [System.Runtime.InteropServices.PreserveSig] int GetChannelVolumeLevelScalar(uint channel, out float level);
        [System.Runtime.InteropServices.PreserveSig] int SetMute([System.Runtime.InteropServices.MarshalAs(System.Runtime.InteropServices.UnmanagedType.Bool)] bool mute, ref Guid context);
        [System.Runtime.InteropServices.PreserveSig] int GetMute([System.Runtime.InteropServices.MarshalAs(System.Runtime.InteropServices.UnmanagedType.Bool)] out bool mute);
        [System.Runtime.InteropServices.PreserveSig] int GetVolumeStepInfo(out uint step, out uint stepCount);
        [System.Runtime.InteropServices.PreserveSig] int VolumeStepUp(ref Guid context);
        [System.Runtime.InteropServices.PreserveSig] int VolumeStepDown(ref Guid context);
        [System.Runtime.InteropServices.PreserveSig] int QueryHardwareSupport(out uint mask);
        [System.Runtime.InteropServices.PreserveSig] int GetVolumeRange(out float minDb, out float maxDb, out float incrementDb);
    }

    /// <summary>Sine burst on channels 3 and 4 with a short raised-cosine fade-out; front channels stay silent.</summary>
    static byte[] BuildImpulse(WasapiAudioFormat format, int frames, double frequency, double amplitude)
    {
        byte[] block = new byte[frames * format.BlockAlign];
        int bytesPerSample = format.BitsPerSample / 8;
        int fade = Math.Min(frames / 4, format.SampleRateHz / 200);
        for (int f = 0; f < frames; f++)
        {
            double env = f >= frames - fade ? 0.5 * (1 + Math.Cos(Math.PI * (f - (frames - fade)) / fade)) : 1.0;
            double v = Math.Sin(2 * Math.PI * frequency * f / format.SampleRateHz) * amplitude * env;
            for (int c = 0; c < format.Channels; c++)
            {
                double value = c is 2 or 3 ? v : 0.0;
                int offset = f * format.BlockAlign + c * bytesPerSample;
                if (format.IsFloat) BinaryPrimitives.WriteSingleLittleEndian(block.AsSpan(offset, 4), (float)value);
                else if (format.BitsPerSample == 16) BinaryPrimitives.WriteInt16LittleEndian(block.AsSpan(offset, 2), (short)Math.Clamp((int)Math.Round(value * short.MaxValue), short.MinValue, short.MaxValue));
                else if (format.BitsPerSample == 32) BinaryPrimitives.WriteInt32LittleEndian(block.AsSpan(offset, 4), (int)Math.Round(value * int.MaxValue));
                else if (format.BitsPerSample == 24) { int sv = (int)Math.Round(value * 8388607); block[offset] = (byte)sv; block[offset + 1] = (byte)(sv >> 8); block[offset + 2] = (byte)(sv >> 16); }
            }
        }
        return block;
    }
}
