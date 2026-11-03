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
modules. `kernel_probe.py` is restricted to disposable GitHub-hosted runners and
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
JSON, bounded exporter events, kernel/module provenance and cleanup status.
`result: passed` means only that this neutral kernel experiment passed. Physical
BT ownership/hiding, hardware output, native games, Wine/Proton and end-to-end
latency remain separate validation work.
