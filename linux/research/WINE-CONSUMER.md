# Windows API observer for Wine

`wine_consumer.cpp` is a native Windows x64 console program, cross-compiled by
MinGW. Compilation passed in
[run 41610372580](https://github.com/AlexGarciaMartin/MadSense/actions/runs/41610372580)
at `6d54ee87b83832d1ff4ec95e845215a06d83dd60`; the Windows build artifact ZIP SHA256 is
`38164a24b17e4bf9d6d8089f6ed0f37c265ca717b2c0f2254ce493fdb927f99b`.
Its first Wine runtime result is pending. The build job preserves sources, GPL license, MinGW/GCC runtime notices,
toolchain/package provenance, PE imports and binary digest. Its static GCC
runtime uses the exception reproduced with the distribution copyright notice;
Windows system DLLs and Wine are not bundled.

```sh
sudo apt-get install -y g++-mingw-w64-x86-64
bash linux/research/build_wine_consumer.sh /absolute/fresh/output
```

The code refuses native Windows execution, requires Wine's `wine_get_version`,
checks the explicit QEMU UUID against `Z:\etc\madsense-ci-owner` and checks QEMU
DMI vendor through `Z:`. These checks supplement the owning Linux harness's full
UUID/DMI/USB ancestry proof. The harness must create a **separate ordinary
account**, clear supplementary groups, and grant access only to the exact owned
virtual hidraw/evdev/ALSA nodes. It sets `MADSENSE_CONSUMER_ISOLATED=UUID` only after
those checks. Neither Wine nor this program may run as root. Physical fixture
nodes remain inaccessible to that account. No production permission policy is
changed by this observer.

Use a fresh, private Wine prefix owned by that account. Record Wine's exact
package/source version and all prefix configuration. Initialize it before a
bounded observer process; a headless Raw Input message window may need an owned
Xvfb display. No global or existing user prefix may be modified. The first run
uses the runtime's default `winebus` configuration; any later raw-HID override
must be a separately labeled diagnostic prefix. Runtime orchestration and capture
correlation are implemented in [CONSUMER-SESSION.md](CONSUMER-SESSION.md) and require
the owned core fixture to reach its verified virtual-device state first.

The following arguments are passed after `wine wine_consumer.exe`:

| Mode | Arguments after mode | Assertion |
|---|---|---|
| `sony` | `UUID dualsense\|edge MAC_HEX neutral\|south` | Unique Sony HID feature-09 virtual MAC, capabilities, features09/20/05, exact corresponding Raw Input path/report, RGB output |
| `xinput` | `UUID neutral\|south` | Exactly one active XInput slot, expected controls, capability query and bounded rumble/stop |
| `audio-inventory` | `UUID` | Read active MMDevice endpoint IDs and ContainerIds |
| `audio` | `UUID EXACT_ENDPOINT_ID VERIFIED_HID_CONTAINER_GUID` | Exact endpoint/container match, native quad48kS16LE negotiation and deterministic one-second WASAPI playback |

Sony's MAC argument is twelve lowercase hex digits, starts `02d5` and must equal
the production virtual feature identity. It is checked in reverse feature-09
byte order before any write and again after reopening the selected path. The
program reports container identity from SetupAPI. Raw Input must expose exactly
one gamepad collection matching the MAC-verified HID path. The harness should
inject only cross/A for `south`, then return to `neutral`; this verifies actual
input rather than accepting an uninitialized neutral value. Feature buffers use
[`HIDP_CAPS.FeatureReportByteLength`](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/hidsdi/nf-hidsdi-hidd_getfeature),
and evidence records both buffer and meaningful report lengths.

XInput does not expose a stable USB path or virtual MAC through the standard
APIs used here. Consequently its output explicitly retains
`usbIdentityAssociationVerified: false`. Its slot attribution relies on the
external harness granting this account access to one virtual Xbox only and
proving that lifecycle. It never chooses the first of multiple connected slots.
The marker is left `0x1234` / right `0x5678` followed by zero; API status is
checked directly, as
[`XInputSetState`](https://learn.microsoft.com/en-us/windows/win32/api/xinput/nf-xinput-xinputsetstate)
does not report errors through GetLastError. Exporter/fixture capture must still
prove delivery and cessation.

Audio never selects the default endpoint or a friendly-name match. The owning
harness must compare inventory with the exact MAC-verified virtual HID's
ContainerId and supply that endpoint and GUID. Missing container association is
a failure, not permission to render elsewhere. The program requires `S_OK` from
[`IsFormatSupported`](https://learn.microsoft.com/en-us/windows/win32/api/audioclient/nf-audioclient-iaudioclient-isformatsupported)
for WAVEFORMATEXTENSIBLE, S16LE, 48kHz, four channels and QUAD mask. It renders the
same frame sequence as the kernel baseline, using
[WASAPI buffer/padding semantics](https://learn.microsoft.com/en-us/windows/win32/coreaudio/rendering-a-stream).
No source resampler, default-device fallback or silent format substitution is
enabled by the probe. Wine/session mixing may still modify samples: only actual
USB/IP PCM capture and rear-haptics comparison can establish delivery.

Output is JSONL with a final `kind: result`. `passed` covers only the performed
API assertions. `outputDeliveryVerified` and `pcmDeliveryVerified` remain false
until external capture is checked. Feature-byte equality with the exported
persona, software timing and output CRC/Opus verification also belong in that
correlation layer. No physical radio, game or physical latency is validated.

Official Proton remains separate. An upstream Wine run cannot establish Proton
behavior. No official Proton runtime has been acquired or executed for this
experiment; the observer is suitable for a future explicitly identified runtime
without changing those evidence labels.
