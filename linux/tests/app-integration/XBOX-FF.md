# Optional application Xbox force-feedback forwarding

This extension is prepared for native execution; no pass is claimed yet. It
checks Linux evdev force feedback through the real app's Xbox mode to synthetic
Bluetooth output. It does not establish physical vibration, game support or latency.

`qemu_core.py --xbox-force-feedback` runs a fresh application/fixture session
after the original broker, core and application gates (and after the protocol
extension when also requested). Evidence stays in `result/xbox-ff-app/`.
The original nine core and ten application cases remain required and unchanged.
No production factory, settings, attach strategy or controller class is replaced.

The app's normal mode command selects Xbox. Once its attributed USB 045e:028e
device is Ready, one exact gamepad evdev node is passed to a fresh
`ConsumerSession`. That session creates an ordinary account, checks that the
physical UHID remains inaccessible and grants access only to attributed virtual
nodes. It stages the independent `ff_probe` executable on an executable filesystem.
No foreign physical descriptor is opened or exempted from the broker audit.

The consumer uploads FF_RUMBLE with strong 0x1234 and weak 0x5678. Linux's Xbox
packet carries motor bytes 0x12 and 0x56. The 60-second effect is explicitly
stopped after 200 ms, then kept open for a separate 300 ms observation before
erase and close. CLOCK_MONOTONIC phases bound both observations. The verifier
requires CRC-valid Bluetooth state with selected motor bytes right=0x56,
left=0x12 before stop, then selected zero motors after the stop call and before
erase/close or automatic expiry. API success, unselected zero bytes, prior
consumer output and output appearing only after close do not satisfy delivery.
Actual marker/stop packet hex and phase timestamps are retained.

Consumer process, account, directory and ACL cleanup must finish before the app
switches back to Auto. The application then completes its original pause/resume,
unplug/replug and disposal checks, including fresh helper idle confirmation and
physical-node restoration. `xboxForceFeedback` carries the one extra required
case separately from those ten application cases. Cleanup failure prevents pass.
The extension records the SHA-256 of its exact readiness document. Both guest
and host validators compare that file to the consumer's nested ready identity,
including the owned guest token and Xbox persona; another session's result is
not accepted as evidence for this invocation.

Portable tests exercise CRC, motor selectors/order, explicit-stop windows,
expiry/erase/close confusion, missing phases, failed calls, incomplete base
results and cleanup. The payload import smoke checks the staged module without
falling back to the source tree. Native compilation and runtime remain separate
evidence; the phased FF source is shared with the consumer experiments.
