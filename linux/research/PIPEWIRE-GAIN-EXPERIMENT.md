# Private PipeWire rear-volume experiment

This is prepared research, not production policy. Its eighteen portable tests cover identity, channel
binding, PCM alignment/gain evidence, conservative control restoration, staged
imports and the mandatory cleanup boundary before subsequent consumers. The
target/control parsers also accept the retained real dumps from both observations
below. Native run `41632075665` at `4d5cc8` stopped after baseline playback because
the observer incorrectly looked for process identity directly on a Node. The
actual dump contains credentials on Client 50, referenced by Node 51's
`client.id`, and four active matching links to the owned sink. The corrected
join is covered by unmodified reduced fields from that dump and negative peer,
port-owner and inactive-link cases. A new full native measurement is pending.

That failed run made no route-volume request. It restored the pre-stack ALSA
snapshot byte for byte, all five device ACLs, and removed the private account and
processes. Separate post-hoc analysis of its saved WAV against core PCM found one
unique 128-frame sign anchor and compared all 48,000 marked frames. Fitted gains
were approximately 0.9040036 on **all four** channels, with maximum residual below
0.502 LSB and no sign mismatches. This supports the baseline software attenuation
observation; it does not turn the failed runtime gate into a pass. Adjusted and
restored PCM phases did not run.

The first private observations at commits `7d9f906` and `0325da7` used PipeWire
1.0.5 / WirePlumber 0.4.17 on Noble. Both exposed the owned virtual card as
`output:analog-surround-40+input:iec958-stereo`, with an S16LE 48 kHz four-channel
sink in FL/FR/RL/RR order. Neither advertised Direct. Installed UCM 1.2.10 lacked
the Sony DualSense configuration and `common/directm.conf`. Both reported raw
channel volumes of 0.063997 and software volumes of 0.904004 on all four channels;
the ALSA PCM control changed from 80 to 77 during policy initialization. Private
defaults stayed unchanged during observation, and both sessions cleaned up.

Those raw volumes are not a PCM measurement. The production persona's
`UsbAudioFunction` stores UAC volume/mute controls without scaling the audio bytes.
An ACP hardware/software split can therefore produce a different effective gain
here than on an actual USB amplifier. This experiment measures the delivered PCM
and keeps raw, software and ALSA state separate. It does not infer their product
or assume a Sony-wide configuration change is appropriate.

## Scope and invocation

Run only while the existing owned-VM core actor and exact UHID fixture are alive,
after their functional assertions. The caller retains responsibility for their
final broker/USB cleanup. No real controllers, radio or camera may be present.
The existing `ConsumerSession` validates the ready token, virtual USB ancestry,
device descriptors and scoped ACLs, creates a fresh ordinary account, and removes
its processes/account/files and restores grants afterward.

```sh
sudo python3 linux/research/pipewire_gain.py \
  --ready /path/to/consumer-ready.json \
  --physical-parent /sys/devices/virtual/misc/uhid/EXACT_FIXTURE \
  --output /path/to/fresh-gain-evidence \
  --adjust-owned-rear-unity
```

Use the already reviewed Noble guest packages for the private observer, including
`pw-play`, `pw-cli`, `pw-dump`, `wpctl`, `amixer` and private D-Bus. The runner does
not install packages, alter global configuration, start host services or select
another profile. It supports the same version family as `pipewire_defaults.py`.
It must not overlap the Pulse observer, Wine or other PCM playback.

The consumer workflow explicitly supplies `--adjust-owned-rear-unity`; the host,
guest and core entrypoints default it to false. After the existing core functional
assertions, the suite runs gain measurement in a separate fresh account **before**
the read-only PipeWire/Pulse observations and SDL/Wine sessions. It therefore runs
even when a later Wine assertion fails. Guest packages and the staged gain module
and tests use the same source payload hashes as the other consumers. No extra VM
or controller is introduced.

The retained `pipewire-gain/result.json` must match the entire current ready
contract, and prove graph/ALSA/ACL/process/account restoration. Only then may a
failed measurement continue to later consumers. The host independently verifies
that record and its hash before processing a later consumer failure, retaining
its summary in provenance. Collection is independent of compatibility; cleanup
failure still aborts the run. Missing, stale or foreign-device evidence cannot
substitute for the current invocation.

The private PipeWire socket peer PID/UID/GID is checked before each dump. Card
sysfs inode, ALSA control-node identity, device and sink IDs **and serials**, exact
profile-device/route index, profile and default metadata must remain stable.
VID/PID, display name and the synthesized `device.serial` string do not identify
the target. Only one ALSA card and one sink on it are allowed.

## Three measurements and restoration

1. Snapshot ALSA controls before starting the private stack. After policy settles,
   retain all active routes, the exact node/route volume properties, profile and
   default. Play a saved 48 kHz S16LE quad WAV containing silence and a one-second
   marked sequence. `pw-play --target` uses the captured object serial. The dump
   must show that exact child's stream linked to that sink on four matching
   channels. The join uses Node `client.id` and the Client's protocol-provided
   PID/UID/GID, not an application name or an assumed Node PID field. The child
   PID/start identity is checked around observations before it is reaped. Its
   exit status, capture offset, binding and captured PCM survive a failed
   playback assertion.
2. Set only the owned device's active output `Route` parameter, with `save=false`
   and rear `channelVolumes` equal to 1.0. Retain front raw requests. Do not force
   `softVolumes`: let ACP expose its decomposition, then record it and the ALSA
   controls. Repeat the same marked PCM and capture the actual core ingress.
   Retaining front raw requests does **not** assert preservation of their effective
   gain; all four channels are measured.
3. Restore the original route volume request and require the entire recorded
   profile/default/route/node state to match. Replay the source and compare its
   aligned samples with baseline. Stop and reap the session's own audio processes,
   then restore only changed writable ALSA controls to the exact pre-stack values.
   Control metadata and device identity must still match; read-only changes or an
   unknown control schema cannot authorize a write.

An error still attempts scoped restoration. A failed player is stopped before
route rollback. No replacement card/node receives a rollback. An unexplained
route change, remaining process, failed control restore or missing ACL/account
cleanup yields `experimentCleanupVerified=false` and a failing CLI exit. A caller
must stop before later consumers in that case. There is no default-sink write.

The source, captured PCM, full dumps, commands, private logs, versions and installed
configuration/notices remain in evidence. PCM analysis uses one unique 64-frame
sign anchor and compares **all** marked frames without fitting local delays.
Per-channel least-squares gain and residuals are descriptive. Rear unity is true
only when both rear channels equal the source sample for sample. Reordered,
missing or duplicate marker data cannot silently become a unity result. No
existing compatibility gate or numerical tolerance is changed.

`result=measured` means collection completed; inspect `rearUnityPcmExact`, every
channel's residual, `restoredMarkedPcmEqualsBaseline` and cleanup separately. It
does not mean the adjustment worked. The alignment is not latency, USB ingress
is not Bluetooth/acoustic output, and the private session is not a real desktop
or a hotplug/default-preservation test.

## Primary contracts and notices

The [WirePlumber 0.4.17 mixer API](https://github.com/PipeWire/wireplumber/blob/0.4.17/modules/module-mixer-api.c)
associates a node's `card.profile.device` with a device Route and sends channel
volume requests there; this experiment uses that same ownership relationship but
does not persist the request. The [PipeWire 1.0.5 command tool](https://github.com/PipeWire/pipewire/blob/1.0.5/src/tools/pw-cli.c)
supports device Route parameters. Its [playback tool](https://github.com/PipeWire/pipewire/blob/1.0.5/src/tools/pw-cat.c)
accepts a target serial and explicit channel map. The
[ALSA ACP device](https://github.com/PipeWire/pipewire/blob/1.0.5/spa/plugins/alsa/alsa-acp-device.c)
publishes raw and software volume properties; neither alone substitutes for the
marked PCM comparison.

PipeWire 1.0.5 [identity keys](https://github.com/PipeWire/pipewire/blob/1.0.5/src/pipewire/keys.h)
define the client credential properties. The
[native protocol](https://github.com/PipeWire/pipewire/blob/1.0.5/src/modules/module-protocol-native.c)
obtains the Linux PID/UID/GID from `SO_PEERCRED`; the observer joins that Client
to its stream rather than trusting a player-supplied display property.

No upstream code or configuration is copied into this implementation. The shared
observer retains installed package notices and configuration in runtime artifacts;
see [PIPEWIRE-DEFAULTS-OBSERVER.md](PIPEWIRE-DEFAULTS-OBSERVER.md) for source/license
details. Native route behavior, link schema and rollback still require the first
owned-VM execution before this can support a production decision.
