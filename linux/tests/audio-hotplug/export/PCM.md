# Private export PCM experiment

`measure_pcm=true` is a second opt-in on `linux-audio-export.yml`, together with
`run_native=true`. The same owned VM first completes the unchanged functional
10/9/10 and installer 2/4 gates, then all four ignore/export lifecycle cases.
Only then does it start one fresh actor, nonce, audio account and USB lease for
PCM. A PCM failure preserves the successful lifecycle result separately.

The PCM actor is another committed source copy. Its verified transform adds the
same AC0 interface nonce and a bounded ingress recorder, plus start/bookmark/
shutdown calls in the research AppProbe. The production application controller,
settings, queues, broker and audio forwarding logic are unchanged. Both the
ordinary lifecycle overlay and the separate PCM overlay are attributed to the
same source commit; the host verifies the exact four changed/copied sources.

The recorder observes accepted USB quad bytes after the persona copies the ISO
packet, before its existing callbacks. It preallocates 16 MiB and 65,536 packet
records before bridge startup. A callback performs a bounded copy with atomic
publication; it never waits, allocates, flushes or writes to disk. Capacity,
concurrent-writer and malformed-frame rejections invalidate capture. Bookmarks
identify immutable complete prefixes. The real app must stop first; only then
does its ordinary UID write hashed chunks of at most 4 MiB and the packet ledger.
Root collects those files through the existing ordinary-UID, no-follow traversal.

One deterministic S16LE/48 kHz FL/FR/RL/RR source contains 48,000 marked frames,
with a quarter second of silence on either side. Samples are multiples of four.
The exact same WAV is used for four phases:

1. `aplay` opens the exact virtual PCM before any export adapter exists.
2. `pw-play` targets the exported node's exact object serial with all gains at one.
3. Only that node's channel gains become FL=1/2, FR=1/4, RL=1, RR=1.
4. Original node Props are restored, then the WAV is played again.

Before the three marked PipeWire phases, a separate one-second four-channel
silence stream negotiates the new adapter's format. This performs no Props
write. Its own PID/start, peer credentials, four active links, default identities
and all-zero captured PCM are required. Silence establishes observed activity;
it cannot establish sample continuity or gain. The initial raw volume array is
retained at its actual length. Only the initial read permits two raw entries;
all soft entries and every post-negotiation read must have four. No missing
values are filled in. The gain/restoration baseline is taken after negotiation.

Player PID/start/UID/GID, PipeWire peer credentials, stream identity and exactly
four active channel-preserving links attribute each PipeWire phase. Each phase
uses one common alignment for all channels inside its actor bookmarks. Every
marked sample must match the expected integer sequence; there is no per-channel
alignment, gain fitting, lossy tolerance or omission of a damaged middle/tail.
Missing/repeated/reordered frames, swapped channels and recorder losses fail.

Raw and software volume arrays are read separately through the WirePlumber 0.4
POD API with exact float32 values, while original `pw-dump` and normalized
snapshots retain their hashes. The first baseline must actually be unity; another
baseline is retained as a failed experiment rather than normalized for success.
Only Node Props are written, never an ACP Device/Route. PipeWire's audioconvert
selects its channel-volume branch when channelVolumes is set without softVolumes;
the PCM comparison verifies the actual consequence. ALSA controls are also saved
before/after and must remain identical. Source:
[PipeWire 1.0.5 audioconvert apply_props/set_volume](https://github.com/PipeWire/pipewire/blob/1.0.5/spa/plugins/audioconvert/audioconvert.c).

Default sink/source identity, initial playback priority, no automatic capture
publication, exact USB parent, rule/ACL retirement and actor cleanup remain
required. The independent host audit reconstructs source WAV, packet/chunk
coverage, each bookmarked PCM comparison, graph attribution, exact node reads
and restoration from the retained evidence.

Run `41659159847` at `86287d8` passed the original functional gates and all four
export lifecycle cases. The direct ALSA phase delivered all 48,000 marked frames
exactly in FL/FR/RL/RR: 1,495 captured packets, 574,080 bytes and zero recorder
rejections. The PCM experiment then stopped before PipeWire playback because the
suspended adapter exposed two raw volume values but four software values. Its
cleanup passed; the failed experiment is not promoted to a four-phase pass.
The corrected negotiation phase preserves that initial state rather than
rewriting it. PipeWire 1.0.5 `set_volume` invokes `remap_volumes` only once the
direction has a format; mismatched array lengths are then mapped to that format.
The next native run must demonstrate the expected transition. Source:
[audioconvert volume remapping](https://github.com/PipeWire/pipewire/blob/1.0.5/spa/plugins/audioconvert/audioconvert.c#L1415).

This stage proves only software PCM ingress if it passes. Native haptics and
Opus transmission require separate comparisons against the samples actually
selected by the forwarding queues, whose preroll/backlog policy can discard
input. A future Opus waveform phase needs a band-limited stimulus, not the white
noise marker used here. Radio transport, DAC gain, acoustics, actuator force and
physical latency remain unmeasured. This experiment does not install production
audio policy or restart another user's audio session.
