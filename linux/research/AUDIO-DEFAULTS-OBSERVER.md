# Private observation of distribution audio policy

`audio_defaults.py` is a prepared, optional observer for the existing owned QEMU
fixture. It has not run natively. Its eight portable evidence tests pass; these
validate identity joins and reporting, not audio-stack behavior.

The first adapter uses PulseAudio because the consumer VM already supplies it.
It starts a fresh ordinary account through `ConsumerSession`, with that helper's
exact virtual-node ACL grants, UID/process cleanup and ACL restoration. The
virtual USB parent, ALSA card number and sysfs path must all agree. It refuses
additional ALSA cards or Bluetooth radio interfaces. It never attaches USB,
changes the production helper, installs packages or modifies global services.

The daemon reads the distribution's installed configuration. There is no private
quad-channel override, profile selection, volume adjustment, UCM replacement or
PCM playback. The only daemon command-line changes keep the process foreground,
prevent idle exit/PID-file reuse and direct logs to the artifact. HOME, config,
state, cache, runtime and both D-Bus addresses belong to this new account. The
private Pulse socket's SO_PEERCRED must match the exact child PID/UID/GID before
any pactl query. Clients use its explicit socket, never an ambient default server.

This is **not an actual desktop session**. It has no saved user profile/volume
state and its isolated D-Bus has no real logind, BlueZ or rtkit services. Those
conditions can affect availability and scheduling. Distribution configuration
may initialize mixer controls on the one owned virtual card; the observer saves
`amixer contents` before and after enumeration to expose that behavior. It does
not call `alsaucm set` or open a UCM manager merely to enumerate verbs.

Artifacts retain:

- Kernel, package and installed tool versions, effective Pulse daemon settings,
  installed configuration copies/hashes and a complete installed UCM file manifest.
- Sony UCM and Direct macro files if installed, plus relevant distribution
  copyright/license notices. Their absence is recorded.
- Exact ALSA/sysfs/USB attribution, udev properties and original control values.
- Full pactl info/cards/sinks/sources/modules JSON before and after observation.
- Active/available profiles, sink channel order, sample specification, per-channel
  raw/percent/dB volumes, mute/port values and whether rear channels are exactly
  PulseAudio's normal-volume value. No rounded percent string substitutes for it.
- Default sink names before/after the read-only observation, and helper-owned
  process/account/ACL cleanup evidence.

The card join requires its ALSA index and full sysfs path. Sinks join by ALSA
index and the card's owning Pulse module; pactl 16.1 JSON does not provide a sink
`card` field. Descriptions, VID/PID alone and the default sink are never selectors.
A stereo sink, missing Direct profile or attenuated rear channels is retained as
an observation, not silently replaced with a successful quad configuration.

`result=observed` only means collection succeeded and the private default sink
remained unchanged during the queries. It does not mean the observed audio
profile is compatible with native haptics, or that PCM/Wine/game behavior passed.
`ConsumerSession` changes the result to failed if its cleanup cannot be verified.

## Run when the native fixture is Ready

Use a separate evidence directory and invoke after attribution is complete,
while the fixture, core actor and virtual USB device remain alive. Do not overlap
it with the Wine session or ALSA playback that could already own the PCM device.
Required guest tools are PulseAudio/pactl, dbus-daemon, alsa-utils, udev, dpkg-query
and acl; the script installs none of them. ConsumerSession uses the temporary
`/opt` staging helper introduced after the first `/run` execution failure.

```sh
sudo python3 linux/research/audio_defaults.py \
  --ready /path/to/consumer-ready.json \
  --physical-parent /sys/devices/virtual/misc/uhid/EXACT_FIXTURE \
  --output /path/to/fresh-audio-default-evidence
```

The existing ready contract includes the owned guest UUID and exact VHCI/HID/input
identities; it is revalidated by ConsumerSession before/after commands. The outer
harness must retain its own ordered actor stop, virtual detach and physical reset
checks. This observer neither replaces those checks nor counts as a functional
core/application test.

The consumer workflow now requests the optional `--observe-audio-defaults` hook.
`qemu_consumers.py` copies this observer and its tests into the checked payload;
the guest installs its distribution prerequisites, then the core harness calls
`run_suite(..., observe_audio_defaults=True)` while its attributed virtual device
is alive. This observer's account/session closes before the separate SDL/Wine
account starts. Evidence remains in a sibling `audio-defaults` directory, with
its collection result reported separately from consumer compatibility.

A collection failure with verified cleanup is retained while consumer testing
continues. Missing cleanup evidence, retained processes/account or ACL restoration
failure aborts before consumers start. A stereo profile or absent Direct profile
is never silently changed to force a successful result. The default hook remains
off when the option is omitted. Native execution of this observer is still
pending: previous consumer attempts failed before reaching the virtual Ready
state or this hook.

## Next experiments and boundaries

1. Run the observer against the current pinned guest and retain the actual UCM,
   kernel and Pulse versions. Repeat in a separately pinned modern guest with
   the Sony jack controls needed by newer UCM. Compare observed configurations;
   do not modify the original distribution files to force parity.
2. Run a separate, explicitly labeled intervention only if the observation shows
   Direct is advertised but not selected. Save the exact card profile and all
   channel values, select Direct only on the attributed card, inspect PCM/Wine
   results, then restore that card's exact prior state. This intervention is not
   implemented in the current read-only observer.
3. Test default-sink preservation across **creation** with an earlier lifecycle
   hook. ConsumerSession currently requires the virtual device to exist, so this
   observer cannot prove hotplug behavior. The next harness must start its private
   audio session with a synthetic reference sink, record that selected default,
   create the owned virtual USB device, observe the default without forcing it
   back, then remove the virtual device and compare again. A profile off/on cycle
   is not a substitute for this test. No unrelated user's default is touched.
4. Add a separate PipeWire/WirePlumber adapter with exact versioned configuration
   inventory and raw pw-dump/wpctl evidence. Version 0.4 and 0.5 configuration must
   not be treated as interchangeable. Preserve the same exact USB attribution and
   private lifecycle, and report its separate session limits. No result from this
   Pulse adapter is labeled as PipeWire, Proton or desktop validation.

## Primary sources and licenses

ALSA's [31df87b Direct change](https://github.com/alsa-project/alsa-ucm-conf/commit/31df87b5d77f3e02b6bdf61b4974654fdaaf9597)
adds a four-channel playback/two-channel capture use case first for Wine. The
[Direct macro at that revision](https://github.com/alsa-project/alsa-ucm-conf/blob/31df87b5d77f3e02b6bdf61b4974654fdaaf9597/ucm2/common/directm.conf)
chooses the hardware PCM without channel splitting; it does not itself establish
what a particular installed desktop selected. The configuration project uses
[BSD-3-Clause](https://github.com/alsa-project/alsa-ucm-conf/blob/31df87b5d77f3e02b6bdf61b4974654fdaaf9597/LICENSE).
No upstream configuration is installed or vendored by this observer.

PulseAudio's [default startup script](https://github.com/pulseaudio/pulseaudio/blob/v17.0/src/daemon/default.pa.in)
shows profile/volume restore and udev detection as distribution startup concerns.
The observer preserves the installed script instead of copying this reference
version. The [16.1 pactl source](https://github.com/pulseaudio/pulseaudio/blob/v16.1/src/utils/pactl.c)
defines the JSON fields used here; [udev attribution](https://github.com/pulseaudio/pulseaudio/blob/v16.1/src/modules/udev-util.c)
and [ALSA card properties](https://github.com/pulseaudio/pulseaudio/blob/v16.1/src/modules/alsa/alsa-util.c)
support the identity join. [PA_VOLUME_NORM](https://github.com/pulseaudio/pulseaudio/blob/v16.1/src/pulse/volume.h)
is 0x10000, distinct from a rounded displayed percentage. These sources carry
their stated LGPL notices (the referenced C sources are LGPL-2.1-or-later; the
default script states LGPL-2.0-or-later). No upstream C implementation is copied.
Runtime artifacts retain installed file headers and distribution copyright files.

For the later adapter, official [WirePlumber ALSA policy](https://pipewire.pages.freedesktop.org/wireplumber/daemon/configuration/alsa.html),
[configuration search order](https://pipewire.pages.freedesktop.org/wireplumber/daemon/locations.html)
and [PipeWire configuration](https://pipewire.pages.freedesktop.org/pipewire/page_man_pipewire_conf_5.html)
describe the monitor/profile and configuration layers that must be identified.
They inform the plan only; no PipeWire/WirePlumber source is incorporated.
