# Consumer experiments after kernel enumeration

These observers are prepared separately from the kernel baseline. The independent
`consumers-build` job in `linux-consumers.yml` compiles them without executing them or
accessing devices. SDL2, pinned SDL3 and evdev FF compile successfully in
[run 41610372580](https://github.com/AlexGarciaMartin/MadSense/actions/runs/41610372580)
at `6d54ee87b83832d1ff4ec95e845215a06d83dd60`; its native artifact ZIP SHA256 is
`554ff2442c94147b2c18523f0ca6c354e24993fb431bebf57cc52eebc8d932ff`.
Consumer execution is a separate job described in [CONSUMER-SESSION.md](CONSUMER-SESSION.md).
Its first runtime result is pending. It does not change the kernel baseline's proof steps.

`consumer_sdl.c` builds against Ubuntu's SDL2 (at least 2.24) and pinned official
SDL3 3.2.28, commit `7f3ae3d57459e59943a4ecfefc8f6277ec6bf540`. The source archive
SHA256 is `7a8347c770b90b33daac2352858ca03f9c9a2ccc8ce711054870361d1a6b32e5`.
The two binaries select the exact supplied hidraw/evdev path, verify VID/PID,
read neutral controls, report rumble/LED/sensor capabilities and send bounded
virtual-only rumble and LED markers. SDL3 is linked statically; it does not
initialize audio or video. SDL2 uses the distribution runtime.

Build on the disposable Ubuntu host:

```sh
sudo apt-get install -y build-essential cmake pkg-config libsdl2-dev libudev-dev
bash linux/research/build_consumers.sh /absolute/fresh/build-output
```

Copy `bin/` into the owned guest and install its matching `libsdl2-2.0-0` runtime.
The build artifact also includes compiler/package/commit provenance, binary
hashes, complete pinned SDL3 source, observer source/build scripts and notices
for MadSense, SDL3, HIDAPI and the distribution SDL2 package. SDL3 is linked into
its observer; Linux system libraries remain distribution runtime dependencies.
The harness first proves the new virtual device's USB ancestry and writes:

```json
{
  "ownedGuestToken": "the-QEMU-instance-UUID",
  "usbParent": "/sys/devices/platform/vhci_hcd.0/usb1/1-1",
  "localBusId": "1-1",
  "vendorId": 1356,
  "productId": 3302,
  "evdevPath": "/dev/input/eventN",
  "hidrawPath": "/dev/hidrawN"
}
```

These are placeholders, never paths to discover by choosing the first matching
VID/PID. Xbox uses 1118/654 and a null `hidrawPath`. Edge uses 1356/3570.
The guest harness creates an ordinary consumer account with access to **only**
the named virtual nodes and no access to the synthetic physical UHID fixture.
SDL can inspect devices while initializing; running it as root would invalidate
the broker's foreign-reader isolation test. The runner clears supplementary
groups and the inherited environment, and stages root-owned executables under
`/opt` so it does not require traversal of another user's private home or assume
that the guest's `/run` mount permits execution.

```sh
sudo python3 linux/research/run_consumers.py \
  --ready /absolute/consumer-ready.json --binaries /absolute/bin \
  --user madsense-consumer --output /absolute/fresh/consumer-evidence
```

The orchestrator owns ACL setup/restoration and device lifecycle. This runner
does not attach/detach USB, change permissions, start a radio, or alter drivers.
Each binary independently requires an ordinary UID, root-owned VM marker and
exact VHCI ancestry. The root runner also verifies QEMU DMI UUID ownership.

The production-bridge fixture should first inject only its south/cross/A button
and run the same command with `--state south`; then restore neutral and run with
`--state neutral` in another fresh output directory. The former requires that
button pressed, all other buttons released and all axes neutral. It proves a
nonzero input traversed the consumer instead of accepting the initial zero
state before a first report. A neutral-only run does not establish that stronger
input claim. The independent neutral exporter does not synthesize button presses.

For Xbox, `ff_probe` uploads `FF_RUMBLE` through Linux evdev with strong `0x1234`
and weak `0x5678`, starts it for 200 ms, stops and erases it. Linux 6.8
[`xpad_play_effect`](https://github.com/torvalds/linux/blob/v6.8/drivers/input/joystick/xpad.c)
converts these to motor bytes `0x12`, `0x56`. The exporter must observe that
nonzero packet and a later zero packet within the consumer's recorded time
window. Successful ioctls alone do not establish delivery.

Both SDL observers request the same 16-bit rumble values and RGB `12:34:56`.
They pump events because
[`SDL_RumbleGamepad`](https://wiki.libsdl.org/SDL3/SDL_RumbleGamepad) requires
event processing for rumble state. SDL's reported trigger-rumble capability
describes simple trigger vibration, not DualSense adaptive trigger effects.
[`SDL_GetGamepadProperties`](https://wiki.libsdl.org/SDL3/SDL_GetGamepadProperties)
defines those separate capability flags. For the pinned SDL3 PS5 HIDAPI driver,
firmware at least `0x0224` (or zero/assumed current) uses full motor bytes; older
firmware halves them. Correlate the exact expected bytes with feature report
`0x20` and the corresponding driver's
[implementation](https://github.com/libsdl-org/SDL/blob/7f3ae3d57459e59943a4ecfefc8f6277ec6bf540/src/joystick/hidapi/SDL_hidapi_ps5.c),
including enable flags, instead of accepting either value opportunistically.
SDL2's distribution build/version is recorded separately.

### Explicit SDL stop phase

The observer requests rumble for 60000 ms, longer than the owning session's
45-second process timeout. After 200 ms it explicitly requests zero rumble,
pumps events for a separate 300 ms interval, and only then changes the LED.
Closing the controller and quitting SDL are separate later phases. The actual
monotonic timestamps and whether stop preceded automatic expiry are retained;
a stalled or incomplete observation cannot silently claim this separation.
Each boundary is written immediately to stderr and retained in the final JSON.

`consumer_phases.py` correlates those intervals with the core's decoded USB
report `0x02` events and complete Bluetooth fixture output packets (including
CRC validation). It reports selected nonzero/zero rumble separately from zero
motor fields with selectors clear, and excludes later LED/close packets from
the explicit-stop interval. It does not change the existing stop assertion or
turn selector-clear/absent output into verified motor cessation. Complete USB
wire bytes are not available from the current core observer; decoded fields
are labelled accordingly. No exact one-to-one causal mapping or latency follows
from two packets appearing in the same interval.

Run after collecting the full artifact, with paths to that single guest:

```sh
python3 linux/research/consumer_phases.py \
  --suite /capture/core-experiment/consumer-suite/result.json \
  --core-ready /capture/core-experiment/core/ready.json \
  --core-events /capture/core-experiment/core/events.jsonl \
  --uhid-events /capture/core-experiment/uhid-events.jsonl \
  --output /capture/new-sdl-phase-correlation.json
python3 -m unittest discover -s linux/research -p test_consumer_phases.py
```

The parser verifies matching guest tokens, the phase sequence, command bounds,
the minimum stop-only interval, and the core's nanosecond frequency. The Linux
[.NET 10 timestamp implementation](https://github.com/dotnet/runtime/blob/v10.0.0/src/native/minipal/time.c)
uses `CLOCK_MONOTONIC`, as do the C boundaries and the fixture's Python clock.
Another clock/frequency or older artifact without phases is explicitly
unavailable. Eight portable tests cover phase contamination by LED/close,
selector-clear versus selected stop, motor quantization, corrupt CRC, incomplete
decoded selectors, expiry/timing claims and clock mismatches. Native compilation
and execution of this new phase instrumentation are still pending CI.

`result: passed` means the observer's identity, neutral state, capabilities and
API operations passed. Its JSON deliberately retains
`outputDeliveryVerified: false`. The owning production-bridge harness must
compare each observer's time window against actual UHID output capture, verify
rumble cessation, and check the RGB marker. The baseline exporter can instead
use its `events.jsonl` with an explicit clock-frequency conversion or file
cursor window. No successful game, physical output, or latency follows.

## Wine and Proton follow-up

Use one small Windows API executable under separate, fresh prefixes for each
precisely recorded runtime. First record default behavior; any diagnostic
registry override is a separately labeled run, not baseline compatibility.
The executable should enumerate SetupAPI/HID paths and Raw Input collections,
read HID capabilities and Sony feature reports `09/20/05`, and match the unique
virtual `02:D5` identity. Xbox should additionally exercise XInput neutral state
and rumble. All device counts must be checked for duplicate controller exposure.
Wine's
[`winebus` selection logic](https://github.com/wine-mirror/wine/blob/wine-11.0/dlls/winebus.sys/main.c)
includes SDL/udev selection and `EnableHidraw`; apparent Windows controller
identity therefore cannot be inferred solely from Linux enumeration.

Audio requires a real owned ALSA endpoint and a session audio server configured
with front-left/front-right/rear-left/rear-right mapping. The Windows probe
should enumerate MMDevice endpoints, record endpoint/container properties,
negotiate WAVEFORMATEXTENSIBLE S16LE/48 kHz/quad and render the baseline's
deterministic four-channel samples via WASAPI. Correlate them with actual USB/IP
audio ingress and the rear haptics extraction. Valve's
[`winepulse` implementation](https://github.com/ValveSoftware/wine/blob/proton_10.0/dlls/winepulse.drv/pulse.c)
maps four channels to QUAD and rear channels to Windows back-left/back-right;
that source path is feasibility evidence, not runtime proof. Test the actual
default session stack separately from a controlled PulseAudio module-alsa-sink
diagnostic, because automatic profile selection or mixing may change channels.

Valve's [Proton instructions](https://github.com/ValveSoftware/Proton/blob/proton_10.0/README.md)
prefer Steam's distribution and document builds from source in the Proton SDK.
The official GitHub latest release `proton-11.0-2`, inspected on 2026-11-03, had
no binary assets. Do not silently substitute a community Proton build or call
ordinary Wine evidence Proton validation. A pinned official source build or
verified official Steam runtime remains a separate acquisition/build step; no
Steam login or game purchase is required for the current kernel/SDL work.
Wine/Proton probes, actual games and physical Bluetooth latency remain pending.
