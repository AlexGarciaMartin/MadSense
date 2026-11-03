# Independent audio payload inspection

This is offline software evidence. It does not exercise a Linux device, the
application controller, Bluetooth radio, a headset, speaker or actuator. Native
core/application captures are a separate integration stage.

`audio_capture.py` reads the synthetic UHID JSONL trace. It validates audio report
lengths, session/route headers and A2 CRC, extracts each 200-byte Opus packet, then
decodes in order through native libopus. Every packet must decode to 480 stereo
frames at 48 kHz. The decoder is reset only for a valid stream-prime report, not
for each packet. Counter discontinuities remain visible; successful decoding
alone is not a stream-integrity result. Decoder version and available library hash
are retained. The native library is discovered locally or provided explicitly;
no library binary is bundled here.

An optional rear S16LE reference is independently averaged in consecutive
16-frame stereo windows and rounded to S8 with nearest/ties-even saturation.
Captured 64-byte haptic blocks are associated with exact reference positions.
Unmatched, ambiguous and repeated/reordered nonzero blocks remain separate
counts. Silence and ambiguous markers cannot establish complete sample delivery.
This diagnostic currently assumes unity haptic strength and no source-phase reset;
it cannot silently compensate for a stale-source queue reset.

The controlled `audio-probe` executable invokes the production
`DualSenseBtAudioPipeline`, `NativeHapticsQueue` and
`BluetoothAudioReportBuilder` directly. It emits 112 combined packets: eight
silent blocks, 96 blocks with distinct stereo chirps and independent signed
haptic markers, then eight silent blocks. Rear PCM is fed in 48-frame fragments
so production decimation crosses chunk boundaries. This is not the forwarder's
thread, buffering, warmup policy or application lifecycle. Its timestamps record
synthetic order only.

`verify_audio_probe.py` compares that controlled capture with an independent
rational 512-to-480 resampling reference, allowing at most one S16 LSB for the
production floating-point arithmetic. One bounded global codec alignment is
estimated; it is not physical latency. Each active 480-frame/channel window must
have correlation at least 0.90 and normalized RMS error at most 0.45. Complete
haptic output must equal the independently calculated reference byte for byte.
These declared bounds are checked with negative cases, not adjusted per capture.
The ordered speaker comparison is deliberately not applied to a live capture
whose forwarder may discard source blocks.

```sh
dotnet run --project linux/research/audio-probe/MadSense.AudioPayloadProbe.csproj -c Release -- /tmp/audio-capture
python3 -m unittest discover -s linux/research -p test_audio_capture.py
python3 linux/research/verify_audio_probe.py --capture /tmp/audio-capture --output /tmp/audio-analysis --negative-checks
```

All output directories must be fresh. On Linux the decoder requires `libopus0`;
`--opus-library` can select an exact independently installed library. For an actual
synthetic guest trace, use `audio_capture.py --trace PATH --output FRESH
--haptics-source PATH` to inspect decoding and rear-reference associations without
claiming the controlled offline speaker comparison applies.

Local Windows preparation used libopus 1.6.1 and passed all seven tests, including
real native encode/decode. The actual production pipeline capture decoded 53,760
stereo frames. Haptics matched exactly; rational resampling differed by at most
one LSB; global correlation was 0.99969, minimum active-window correlation 0.99720
and maximum normalized RMS error 0.07901. The fitted 312-frame codec alignment is
not a radio or acoustic measurement. Five deliberate corruptions were rejected:
repeated Opus, reordered Opus, corrupted haptics, swapped speaker channels and bad
CRC. The first four retain valid CRCs and packet counters, so framing alone cannot
make those negative cases pass. Linux execution of this offline probe and native
core/application audio captures remain pending.

The independent `linux-audio.yml` workflow runs the seven tests, production
capture and five corruption checks on Ubuntu x64 and ARM64 with the distribution's
libopus. It records source hashes, package/kernel/SDK details, input and decoder
digests and keeps all positive/negative results. Its jobs need neither a VM nor
controller device permissions and do not gate or replace native integration.

Primary API reference: [libopus decoder](https://opus-codec.org/docs/opus_api-1.5/group__opus__decoder.html).
