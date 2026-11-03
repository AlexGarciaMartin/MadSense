# Linux kernel integration experiment

The workflow exports a synthetic neutral DualSense, Edge or Xbox 360 on loopback,
then imports that one device through the distribution's `vhci_hcd` module. It
never opens a physical HID, initializes Bluetooth, binds a physical USB device,
changes global driver policy or tests a game. No physical latency claim follows.

The headless exporter references only the portable Emulation project:

```sh
dotnet run --project linux/probe/MadSense.Linux.Probe.csproj -- \
  --persona dualsense --output /absolute/fresh/directory --seconds 120
```

`ready.json` provides its ephemeral loopback port, bus ID, synthetic serial,
descriptor identity and expected feature bytes. `stop` in that directory requests
bounded shutdown. The exporter does not attach itself. Exported controls remain
neutral. Outputs terminate in evidence files; they cannot reach a physical pad.

`.github/workflows/linux-kernel.yml` uses a real Ubuntu kernel with standard
modules inside an owned QEMU guest. `qemu_kernel.py` pins the official Noble
20261017 cloud image and verifies both its signed SHA256 manifest against the
host distribution's Ubuntu cloud-image keyring and the pinned image digest.
The guest has two CPUs, 2 GiB RAM and bounded KVM/TCG execution. It receives no
physical USB, audio or radio passthrough. SSH binds only loopback, uses ephemeral
keys and verifies a preseeded host key. The host kernel is not changed or rebooted.
Only the owned QEMU process is terminated, and private keys/disks are excluded
from uploaded evidence. Ubuntu's
[public-images documentation](https://documentation.ubuntu.com/_/downloads/public-images/en/latest/pdf/)
describes the distribution keyring used for signature verification.

`kernel_probe.py` is restricted to disposable GitHub-hosted runners or a guest
whose explicit owner token, root-created marker and QEMU DMI identity agree. It
requires an empty USB/IP client before starting. It records its newly imported
port, verifies the unique probe serial and USB ancestry, then opens only nodes
below that parent. Cleanup detaches only the recorded port if its identity still
matches, and stops only the owned exporter process group. Native observer calls
have bounded timeouts. Missing modules, incomplete evidence and unexpected device
identity are failures, not skipped success.

The proof checks exact USB descriptors, primary evdev capabilities/neutral state,
and for Sony personas the `hid-playstation` binding, exact HID descriptor, three
feature reports, neutral raw input and a recognizable virtual-only output marker.
ALSA writes a deterministic S16LE/48 kHz/four-channel stream directly to the owned
USB PCM endpoint. The server must receive the complete unchanged stream and
extract the exact rear haptics channels, allowing only surrounding silence.

To test real kernel ISO IN reassembly, a probe-only decorator inserts synthetic
nonzero microphone PCM immediately before calling the production persona's ring
reader. Its normal 192-byte packets can be shorter than the 196-byte capacity.
An ALSA capture must be an exact contiguous sequence of those emitted frames.
This checks short-packet transport; it does not establish a physical microphone
feature. The decorator delegates production descriptor, HID, class, configuration
and audio OUT handling, and logs class requests/interface transitions.

Linux enables `PackIsoInPayload` in the USB/IP server. It packs only each packet's
actual bytes and retains true descriptor lengths, matching Linux's
[`usbip_pad_iso`](https://github.com/torvalds/linux/blob/6628e7d4c6b25c8d18f1345e3a20d21829aae7e4/drivers/usb/usbip/usbip_common.c).
The default remains the existing Windows padded layout. Portable socket tests
cover short, empty and gapped packet regions plus the existing Windows behavior.
No other scheduling behavior is changed: ISO OUT uses elapsed-time catch-up and
the submitted start frame remains an echo. Kernel/audio evidence is needed to
assess these choices; nominal timer periods are not latency measurements.

The workflow retains results, original descriptor/PCM bytes, native observer
JSON, bounded exporter events, signed image provenance, QEMU console,
kernel/module provenance and cleanup status.
`result: passed` means only that this neutral kernel experiment passed. Physical
BT ownership/hiding, hardware output, native games, Wine/Proton and end-to-end
latency remain separate validation work.

The initial direct-host runs [41574486591](https://github.com/AlexGarciaMartin/MadSense/actions/runs/41574486591)
and [41574717124](https://github.com/AlexGarciaMartin/MadSense/actions/runs/41574717124)
passed all 119 portable protocol tests and compiled the C observer, but could not
start kernel integration: the hosted Azure 7.0.0-1017 kernel lacks `snd_usb_audio`
and its exact extra-module package was unavailable in the runner's Ubuntu archive.
That is the reason for the official-image guest, rather than loading modules from
a different kernel. These failed runs do not establish device enumeration.

Run [41601178684](https://github.com/AlexGarciaMartin/MadSense/actions/runs/41601178684),
commit `38238db`, reached real USB enumeration in all three owned guests on
Ubuntu `6.8.0-147-generic`. Each imported device's USB descriptors matched the
exporter exactly. Kernel logs registered DualSense/Edge gamepad, motion and
touchpad input devices through `hid-playstation`; Xbox registered its `xpad`
gamepad. Sony's kernel initialization requested the three expected feature
reports and produced parsed initialization outputs in the exporter capture.
All three tests then failed in the harness's primary-evdev selector, before the
raw HID/evdev and ALSA assertions. Each owned port detached and each exporter
exited cleanly. This is enumeration evidence, not a complete kernel/audio pass.

The selector incorrectly concatenated sysfs capability words, losing the width
of zero words. Linux's
[`input_print_bitmap` / `input_bits_to_string`](https://github.com/torvalds/linux/blob/v6.8/drivers/input/input.c)
prints unpadded native unsigned-long words from high to low (32-bit words for a
compat reader). The corrected parser retains that width, with regression tests
for BTN_SOUTH at bit 304 in both 32- and 64-bit views. Subsequent runs also retain
the owned input/hidraw/sound node inventory before detachment. A rerun is still
required to validate the later assertions.

The independent next-stage SDL2/SDL3, Xbox force-feedback and Wine/Proton plan is
documented in [CONSUMERS.md](CONSUMERS.md). Those consumer probes do not alter
this baseline and have no native execution evidence yet.
