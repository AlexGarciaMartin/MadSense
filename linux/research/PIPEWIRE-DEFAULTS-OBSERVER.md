# Private PipeWire and WirePlumber policy observer

`pipewire_defaults.py` is a separate, prepared adapter for the attributed virtual
USB card in the owned QEMU fixture. It has not run natively and no workflow invokes
it. Thirteen portable evidence tests pass; the fourteenth exercises real Linux
`SO_PEERCRED` and is skipped on Windows. These tests establish reporting and
identity checks, not installed audio-stack behavior.

The first supported configuration family is PipeWire 1.0 with WirePlumber 0.4.
Ubuntu's Noble package indexes currently list
[PipeWire 1.0.5](https://packages.ubuntu.com/noble/pipewire) and
[WirePlumber 0.4.17](https://packages.ubuntu.com/noble/wireplumber); installed package
revisions, architecture and both compiled/linked library versions are retained
at runtime. They are evidence, not values assumed from the guest's release name.

## Isolation and execution

The adapter uses the unchanged `ConsumerSession` contract: a new ordinary UID
without supplementary groups, private `/opt` home/runtime, narrowly attributed
virtual-device ACL grants, process ownership checks and verified restoration.
The existing owned-guest/ready token, VHCI USB parent and separate UHID fixture
must pass its guards. Exactly one ALSA card may exist, under that USB parent;
Bluetooth radios and video devices are rejected. The outer harness must keep the
fixture, production actor and lease alive and retain its own final cleanup checks.

Both session and system D-Bus addresses point to the newly owned private daemon.
PipeWire gets a private runtime directory and `pipewire-0` server name. Clients
and WirePlumber use its explicit absolute socket path. Before collecting each
snapshot, the socket owner and kernel peer PID/UID/GID must match the exact child
PipeWire process. The WirePlumber child must remain alive, and its PID must appear
as a client in the private registry before enumeration can be considered ready.
This uses the [1.0.5 native-protocol environment contract](https://github.com/PipeWire/pipewire/blob/1.0.5/src/modules/module-protocol-native.c).

The daemons read the installed distribution files. No active configuration is
copied, replaced or patched. There is no Direct selection, volume/default change,
PCM playback, `pipewire-pulse`, global service command, package installation or
production helper change. Daemon policy may initialize mixer controls on the
owned virtual card; `amixer contents` before and after records that behavior.

This is not a desktop session. It has empty user state and no global logind,
BlueZ, portal or rtkit services. The official
[WirePlumber 0.4.17 ALSA configuration](https://github.com/PipeWire/wireplumber/blob/0.4.17/src/config/main.lua.d/50-alsa-config.lua)
also contains VM-specific period/headroom defaults. Those remain enabled if
present in the distribution and are captured. This experiment cannot establish
desktop scheduling, hardware gain, game compatibility or physical latency.

Run only after the exact virtual device is Ready, in its own evidence directory,
without overlapping Pulse/Wine/ALSA playback or another consumer session:

```sh
sudo python3 linux/research/pipewire_defaults.py \
  --ready /path/to/consumer-ready.json \
  --physical-parent /sys/devices/virtual/misc/uhid/EXACT_FIXTURE \
  --output /path/to/fresh-pipewire-default-evidence
```

Required guest tools are `pipewire`, `wireplumber`, `pw-dump`, `pw-config`, `wpctl`,
`dbus-daemon`, `amixer`, `udevadm`, `dpkg-query` and the existing ConsumerSession
prerequisites. Dependency installation belongs to a separately reviewed owned-VM
payload; do not install or enable a desktop meta-package on the host. A future
hook must close this account before any other consumer starts. A failed
collection may be retained as an independent observation only when the result
proves `accountProcessesAndFilesRemoved=true` and no cleanup errors; otherwise the
outer run must stop. This adapter does not currently add such a hook.

## Evidence and exact attribution

Artifacts contain the shared OS/UCM/Sony Direct inventory, package/tool versions,
copies and hashes of installed `/etc` and `/usr/share` PipeWire/WirePlumber trees,
and distribution copyright notices. The copies are artifacts, never active
configuration. `pw-config paths` and `list` preserve the resolved PipeWire server
and client configuration using the
[1.0.5 config tool](https://github.com/PipeWire/pipewire/blob/1.0.5/src/tools/pw-config.c).
WirePlumber 0.4 has no equivalent effective-Lua export in this adapter: the
installed config/scripts and daemon log are retained, and that limitation is
explicit in the result rather than labeling the whole tree as executed.

Each full `pw-dump` remains available, alongside `wpctl status --name`, exact
device/node `inspect`, node `get-volume` and ALSA/udev evidence. The parser follows
the [1.0.5 dump schema](https://github.com/PipeWire/pipewire/blob/1.0.5/src/tools/pw-dump.c).
No text description, default endpoint, first VID/PID or serial substring selects
the device:

- The device must match both `api.alsa.card` and the complete
  `device.sysfs.path` of the owned card. These come from the
  [ALSA udev monitor](https://github.com/PipeWire/pipewire/blob/1.0.5/spa/plugins/alsa/alsa-udev.c).
- Sink/source nodes must reference that exact registry device ID and agree on
  `api.alsa.pcm.card`. WirePlumber's
  [0.4.17 ALSA monitor](https://github.com/PipeWire/wireplumber/blob/0.4.17/src/scripts/monitors/alsa.lua)
  sets this device relationship. Foreign ALSA nodes or duplicate IDs fail.
- Active and advertised profiles, routes, node formats/properties, channel maps,
  raw SPA per-channel/soft volumes and mute values are retained. A Direct profile
  being advertised is separate from it being active. Missing per-channel data
  remains unknown; a scalar or rounded `wpctl` value never substitutes for it.
- Rear-channel unity means only that the reported `channelVolumes` values for
  `RL` and `RR` equal 1.0. It is not a claim about the full hardware/software gain
  chain. The [SPA property definitions](https://github.com/PipeWire/pipewire/blob/1.0.5/spa/include/spa/param/props-types.h)
  distinguish channel, soft and scalar values; all remain in the evidence.

Defaults come from the unique `default` metadata object, subject zero and
`Spa:String:JSON` values, following
[WirePlumber 0.4.17's default-node API](https://github.com/PipeWire/wireplumber/blob/0.4.17/modules/module-default-nodes-api.c).
The selected name must resolve to one live sink; its ID/serial and configured
default are compared too. Collection waits for two equal policy summaries, then
compares the default around the read-only queries. A changed default fails
collection. If no default exists, preservation stays unknown rather than true.

`result=observed` means successful collection and verified account/process/ACL
cleanup, not a haptics-compatible profile. Stereo, attenuation, absent Direct or
no sink is recorded without intervention. This begins after card creation and
cannot prove hotplug default preservation. That still needs the earlier hook
described in [AUDIO-DEFAULTS-OBSERVER.md](AUDIO-DEFAULTS-OBSERVER.md): start a private
synthetic reference sink first, create the owned USB card, then observe creation
and removal without forcing the default back.

## WirePlumber 0.5 and the next native run

The official [0.4-to-0.5 migration guide](https://pipewire.pages.freedesktop.org/wireplumber/daemon/configuration/migration.html)
states that Lua configuration is no longer supported in 0.5. The component/profile
and SPA-JSON fragment model replaces the 0.4 `main.lua`, `policy.lua` and
`bluetooth.lua` configuration path; conversion is not automatic. The adapter
therefore records versions/configuration, then refuses an unsupported family
before starting PipeWire/WirePlumber. It does not write compatibility fragments
or quietly disable policy features.

The first native run should use the existing Noble fixture after its functional
gates, install distribution tools in that guest's build step and collect this
observer separately. Inspect actual socket/registry identity, profile/route
schemas, UCM contents, default metadata and cleanup before adding a runtime hook.
A separately pinned modern guest should then retain its exact 0.5 config and
version, validate the same read-only object joins against actual dumps, and gain
an explicit supported adapter family. Its UCM/kernel jack-control behavior must
also be identified; passing Noble does not validate that newer combination.

## Sources and licenses

No upstream implementation or configuration is vendored or installed by these
files. Runtime artifact copies retain installed headers and package notices.
WirePlumber's referenced files use its
[MIT license](https://github.com/PipeWire/wireplumber/blob/0.4.17/LICENSE).
PipeWire's [license summary](https://github.com/PipeWire/pipewire/blob/1.0.5/LICENSE)
identifies MIT sources and the LGPL ALSA plugin exception; the relevant package
copyright files are captured rather than calling every component MIT. ALSA UCM
and the Direct change retain the attribution and BSD-3-Clause references already
documented in [AUDIO-DEFAULTS-OBSERVER.md](AUDIO-DEFAULTS-OBSERVER.md).
