# Private ignore and client-owned playback experiment

This is preparation for a native experiment, not production audio policy. The
normal application and functional gates retain their original descriptors.
No native result, delivered PCM, Wine ContainerId, physical audio or WP 0.5
compatibility is claimed by this directory yet.

The preceding private WP 0.4 experiment at `4109a34` preserved both default
directions with a preloaded exact-parent rule. Deploying that mechanism into an
already running desktop would need a policy activation contract: ALSA monitor
rules are static configuration, and WirePlumber's SIGHUP handler exits instead
of live-reloading them. This experiment avoids that prerequisite. See
[WirePlumber option types](https://pipewire.pages.freedesktop.org/wireplumber/daemon/configuration/configuration_option_types.html)
and the [0.4 daemon signal handler](https://github.com/PipeWire/wireplumber/blob/0.4.17/src/main.c).

## Isolation and identity

`stage_export.py` archives one committed source tree and changes only the
DualSense persona's descriptor construction in that copy. The copied helper
adds one string and changes only AudioControl interface 0's `iInterface` byte.
Its nonce comes from the owned actor's environment. Device descriptor,
`iSerialNumber`, manufacturer/product strings, HID descriptor and physical
feature implementation remain unchanged. The descriptor executable compares
both Sony personas, with and without an existing serial; the guest also compares
actual USB/HID bytes with the unmodified factory's independently emitted reference.

The kernel creates the `interface` sysfs attribute on USB interface addition
before driver probe; ALSA creates its sound card beneath that control interface.
The ignore rule therefore matches a complete VHCI parent and the control
interface's exact nonce before the sound card's first initialized event.
All `ATTRS` predicates refer to the same interface, rather than mixing USB-device
and interface ancestors. Sources: [USB interface sysfs](https://github.com/torvalds/linux/blob/v6.8/drivers/usb/core/sysfs.c),
[USB bus notification](https://github.com/torvalds/linux/blob/v6.8/drivers/usb/core/usb.c),
[device-add ordering](https://github.com/torvalds/linux/blob/v6.8/drivers/base/core.c),
[ALSA card parent](https://github.com/torvalds/linux/blob/v6.8/sound/usb/card.c),
[udev parent matching](https://www.freedesktop.org/software/systemd/man/latest/udev.html).

Choosing a free VHCI port is not a kernel reservation. A per-import nonce is
needed in addition to the path: a different import winning or reusing that port
must not match a stale rule. This test predicts a port only inside its idle owned
VM, then verifies the actual journal-owned parent before granting audio access.
The helper's existing retained-socket shutdown remains the detach mechanism.
The prototype intentionally keeps one nonce for the two reconnects of each
fresh test actor. A production handshake would issue and journal a fresh nonce
for every import, rather than treating an actor environment variable as authority.

`ACP_IGNORE` is consumed before PipeWire's ALSA monitor emits a card;
`PULSE_IGNORE` separately excludes a card from native PulseAudio's udev discovery.
Both are presence checks. Setting them after node creation is too late, and
setting either to `0` does not mean false. Here a fresh root-owned rule is
atomically installed, its reload acknowledged before graph/attach, and its
bytes/identity journaled. It is removed only after the owned USB parent is gone;
reused or live nodes never receive cleanup writes. No physical node gets audio
ACLs. Sources: [PipeWire ALSA monitor](https://github.com/PipeWire/pipewire/blob/1.0.5/spa/plugins/alsa/alsa-udev.c),
[PulseAudio detection](https://github.com/pulseaudio/pulseaudio/blob/v16.1/src/modules/module-udev-detect.c),
[sound event initialization](https://github.com/systemd/systemd/blob/v255/rules.d/78-sound-card.rules).

## Playback node and cases

An ordinary client calls `pw_core_create_object("adapter", ...)` on the exact
private PipeWire socket. The ALSA adapter executes in that private server; it is
owned by the client resource, with `object.linger=false`. The server assigns
`client.id` and destroys the node when that resource disappears. This is not a
locally exported SPA implementation and does not require changing WirePlumber.
The node starts with four channels, FL/FR/RL/RR, priority 1 and the real sound
card's `device.sysfs.path`, USB VID/PID and bus. The coordinator joins node id,
object serial, client id and process id. A bound callback alone is not success.
Source: [PipeWire adapter ownership](https://github.com/PipeWire/pipewire/blob/1.0.5/src/modules/module-adapter.c).

There are four fresh sessions: automatic defaults, explicit defaults, foreign
parent, and same parent with a wrong interface nonce. Each has two USB
attach/detach cycles. Positive sessions verify an ignored card before starting
the client, then exercise client SIGKILL, recreation, owner-pipe EOF and USB
removal. Negative controls must retain automatic ALSA discovery and its original
default replacement; they never create a second playback node.

The complete graph monitor rejects even transient automatic ALSA cards or
capture nodes in positive sessions. Raw snapshots and metadata streams are
recomputed by the host verifier. The exported node must actually advertise a
four-channel format. That proves format/identity/default/lifetime behavior only;
the first phase deliberately leaves PCM delivery and gain preservation false.
The already measured ACP gain solution is not silently assumed for a node that
bypasses ACP profiles/routes.

The export observer also retains each one-shot `pw-dump` stdout. PipeWire 1.0.5
can emit removal arrays before its final snapshot even without `--monitor`:
`registry_event_global_remove` prints tombstones, then `on_core_done` dumps the
remaining objects and quits. The parser accepts only those exact removal
prefixes followed by one complete snapshot. Extra live snapshots, truncated
JSON and tombstones without a final snapshot remain errors. The independent
audit recomputes the normalized snapshot from raw bytes and verifies both
hashes and raw size. The separate full graph monitor still detects transients.
Source: [pw-dump output callbacks](https://github.com/PipeWire/pipewire/blob/1.0.5/src/tools/pw-dump.c).

The first native attempt, [41651813343](https://github.com/AlexGarciaMartin/MadSense/actions/runs/41651813343)
at `4c80a9b`, passed the unchanged 10/9/10 functional and 2/4 installer/removal
prerequisites. The first positive session reached application Ready, confirmed
the exact interface nonce and both ignore properties, and created a client-owned
node advertising S16LE/48000/FL/FR/RL/RR with priority 1 and the actual USB parent.
Its observer then failed on the removal-prefix format above; no complete export
case passed. Actor, audio-account and rule cleanup records were retained, and
the owned VM stopped with the KVM ACL restored. This remains a failed experiment,
not proof of the four-session lifetime/default contract or delivered PCM.

PipeWire-Pulse maps `device.sysfs.path` to Pulse `sysfs.path`, which Proton's
Winepulse code uses to find the real USB ancestor. This makes a later identity
comparison possible; it does not prove compatibility with a particular Proton
binary, nor repair mainline Wine 9's absent ContainerId implementation. Sources:
[property mapping](https://github.com/PipeWire/pipewire/blob/1.0.5/src/modules/module-protocol-pulse/remap.c),
[Proton Winepulse association](https://github.com/ValveSoftware/wine/blob/proton_10.0/dlls/winepulse.drv/pulse.c).

## Running and limits

The corrected lifecycle run [41653922705](https://github.com/AlexGarciaMartin/MadSense/actions/runs/41653922705)
at `40e2de13` passed all four sessions and the original prerequisites. Both exact
scope cases preserved sink and source defaults across two USB epochs and three
client-node lifetimes, with no automatic ALSA device/capture publication. Both
foreign-parent/foreign-nonce controls retained two automatic sinks/sources and
their original default replacement. Cleanup, QEMU shutdown and KVM ACL restore
passed. Independent audit verified 108 guest sources, four installed files,
source overlay and raw evidence. PCM and ContainerId remained unvalidated.

The separately enabled next stage is described in [PCM.md](PCM.md); its native
measurement is pending and does not change those completed lifecycle results.

`linux-audio-export.yml` runs Python, real descriptor tests and a Linux C build
on push. A manual `run_native=true` dispatch runs the unchanged broker 10/core
9/app 10 cases and installer 2/removal 4 prerequisites, then the four export
sessions in the same signed Ubuntu owned VM. It does not claim the separate
extended-protocol or Xbox FF suites were rerun. Payload hashes, original source
commit, copy transformation hashes, native cleanup and QEMU/KVM restoration are
retained. No host audio session is restarted or configured.

Before production adoption this still needs native results, exact four-channel
PCM and front-gain/rear-unity checks, audio/HID ContainerId comparison in the
supported Proton build, PipeWire restart and broker crash recovery, and a
capability/setup contract. Native PulseAudio-only desktops cannot use this
PipeWire client without a separate supported backend. The private rule is a
laboratory lifetime guard, not yet a production recovery implementation.
