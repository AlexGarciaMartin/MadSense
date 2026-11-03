# Windows API observer for Wine

`wine_consumer.cpp` is a native Windows x64 console program, cross-compiled by
MinGW. Compilation passed in
[run 41610372580](https://github.com/AlexGarciaMartin/MadSense/actions/runs/41610372580)
at `6d54ee87b83832d1ff4ec95e845215a06d83dd60`; the Windows build artifact ZIP SHA256 is
`38164a24b17e4bf9d6d8089f6ed0f37c265ca717b2c0f2254ce493fdb927f99b`.
Its [first Wine runtime result](CONSUMER-RUN-7d9f906.md) failed before feature/MAC
verification; the original failure remains recorded. The build job preserves sources, GPL license, MinGW/GCC runtime notices,
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

The opt-in `--wine-udev-variant` diagnostic runs only after the default session
has closed with exact device identity and verified process/account/ACL cleanup.
It creates another ordinary account and fresh prefix, then writes only that
prefix's `HKLM\System\CurrentControlSet\Services\winebus` value `Enable SDL`
as DWORD zero through Wine's `reg` command. It checks the exact value before
and after restarting that account's prefix-specific wineserver. No host registry,
existing prefix, production configuration or `DisableHidraw` value is changed.
This route is limited to the audited Wine 9.0 version family.

Wine 9.0's
[`IRP_MN_START_DEVICE` implementation](https://github.com/wine-mirror/wine/blob/wine-9.0/dlls/winebus.sys/main.c)
tries the SDL backend by default and starts UDEV when SDL is disabled or fails
initialization. This is why merely relying on the default `DisableHidraw=0`
cannot establish that UDEV was used. A successful registry write also does not
prove the active backend; PE capabilities and actual feature/input/output
evidence must still be checked. The diagnostic keeps those same MAC, feature,
ContainerId, RGB and deterministic PCM assertions.

`wine-udev-variant/result.json` is labeled `configured-distribution-wine-udev`
with `defaultWinebus=false`. Its separate digest/cleanup summary is recorded in
the primary result. A successful configured result cannot turn the original
default result into a pass; a failed default is rethrown after collecting the
diagnostic, provided cleanup allowed the second account. The workflow now
requests this variant. Native execution of this new variant is pending; GE/UMU
and official Valve Proton remain separate experiments.

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

The first native run (`7d9f906`, workflow 41626531215) executed Ubuntu Wine 9.0,
but its Sony candidate failed the feature-capability-length requirement before
MAC/ContainerId verification or WASAPI playback. The observer now emits the
actual HID path and input/output/feature capability lengths before this check;
the native feature requirements remain unchanged. Wine 9's
[driver selection](https://github.com/wine-mirror/wine/blob/wine-9.0/dlls/winebus.sys/main.c#L788)
starts UDEV only if SDL is disabled or fails to initialize. Thus the default SDL
backend is a candidate explanation for the missing native feature surface,
pending the new capability evidence. An explicit UDEV variant needs its own
fresh prefix and result label; it must not replace or relabel this default run.

Run [41632075665](https://github.com/AlexGarciaMartin/MadSense/actions/runs/41632075665)
at `4d5cc8df2466f4697438a4f789f55b9773906c75` executed both prefixes. The runtime
artifact SHA-256 is `991fcd83c1eac0bb8cce99ef99545eed12bb5541a39fe0f757cb8ebb6832ef79`.
Default Wine 9.0 exposed input/output/feature lengths 15/0/0. The explicitly
configured UDEV variant exposed 64/48/64, matched the virtual feature-09 MAC,
returned exact features 09/20/05, and observed south/neutral through Raw Input.
Both sessions completed account/process/ACL cleanup. The variant did not pass:
ContainerId was unavailable, and its PE reported a short HID write without
recording the transferred count. The appended Win32 997 was potentially stale;
the original code already waited on pending writes, so that number alone cannot
establish a missing wait or a bridge failure.

Independent capture shows each configured-prefix invocation delivered the exact
48-byte USB RGB report (`02 00 04`, remaining fields zero except final `12 34 56`)
and one matching CRC-valid 78-byte Bluetooth report inside its command interval.
The raw USB timestamps were 154722045403 and 155089415249 ns; corresponding
Bluetooth timestamps were 154722399077 and 155089824167 ns on the same guest
monotonic clock. Final raw capture had zero rejected/unwritten snapshots and no
error. This establishes synthetic report forwarding; it does not prove the API
reported the correct byte count or that a physical LED changed. The original PE
failure and the missing ContainerId remain failures. WASAPI was not attempted.

The observer now records `hid-write-completion` before evaluating the same
strict 48-byte requirement. It follows Microsoft's
[WriteFile](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-writefile)
and [GetOverlappedResult](https://learn.microsoft.com/en-us/windows/win32/api/ioapiset/nf-ioapiset-getoverlappedresult)
contracts: the latter supplies the count for immediate and pending completion,
and errors are captured only when their API fails. A three-second wait is
followed, if still pending, by
[CancelIoEx](https://learn.microsoft.com/en-us/windows/win32/api/ioapiset/nf-ioapiset-cancelioex)
and a separate three-second drain. If completion remains outstanding, the probe
terminates without unwinding its event, OVERLAPPED or buffer. Timeout remains
failure even if completion races it. Fourteen portable scripted-API regressions
cover immediate/pending success, zero/short/oversized counts, API failures,
cancellation races and undrained I/O; these do not replace native Wine execution.
The artifact retains their source, header, source hashes and test output.

Container association is a separate runtime capability. Stock Wine 9's
[winebus](https://github.com/wine-mirror/wine/blob/wine-9.0/dlls/winebus.sys/main.c)
does not implement `BusQueryContainerID`, and its
[Pulse backend](https://github.com/wine-mirror/wine/blob/wine-9.0/dlls/winepulse.drv/pulse.c)
does not supply the matching ContainerId property. Valve's Wine at
`b8fdff8e1f855b5276ec4ddca0f31b2792554322` implements this
[PnP query](https://github.com/ValveSoftware/wine/blob/b8fdff8e1f855b5276ec4ddca0f31b2792554322/dlls/winebus.sys/main.c),
using matching USB ancestry inputs in its
[UDEV](https://github.com/ValveSoftware/wine/blob/b8fdff8e1f855b5276ec4ddca0f31b2792554322/dlls/winebus.sys/bus_udev.c)
and [Pulse](https://github.com/ValveSoftware/wine/blob/b8fdff8e1f855b5276ec4ddca0f31b2792554322/dlls/winepulse.drv/pulse.c)
backends. This source difference explains why a newer derivative is a useful
separate experiment; it is not proof that any installed GE/UMU or official Valve
Proton runtime has passed this probe. No fallback to an unrelated audio endpoint
is allowed.

The diagnostic rerun
[41634461320](https://github.com/AlexGarciaMartin/MadSense/actions/runs/41634461320)
at `d8f3ca0287fa3c1c091ffe58b2221c4df40b92ea` confirmed both configured-prefix
writes returned pending initially, then completed successfully with **47 bytes
reported for 48 requested**. Wait status and completion error were both zero;
no timeout or cancellation occurred. Both exact 48-byte USB RGB reports and
matching CRC-valid Bluetooth reports were captured. Core capture drained without
loss, and both consumer accounts/processes/ACLs cleaned up. The runtime artifact
SHA-256 is `e9e2d1f1f7bb00eae5e91675ca089be6f5357b362518fa8ae036e356313610b2`;
the PE SHA-256 is `6cfd2620f9c16507a5601994456a8595875f07b954ad1002a2420a14d0fb5834`.
The strict API-count failure remains separate from successful synthetic forwarding.

Wine 9's
[HID class completion](https://github.com/wine-mirror/wine/blob/wine-9.0/dlls/hidclass.sys/device.c#L398)
subtracts one for a nonzero report ID on its write path. Its
[test_write_file regression](https://github.com/wine-mirror/wine/blob/wine-9.0/dlls/dinput/tests/hid.c#L1264)
explicitly expects that subtraction from the minidriver's returned count.
Valve's pinned Wine retains this behavior. This explains the observed 47 without
attributing it to dropped payload or an uncompleted pending request. Microsoft's
[HID client sample](https://github.com/microsoft/Windows-driver-samples/blob/main/hid/hclient/report.c)
compares bytes written to the full output-report length, as does this observer.
This experiment has not resolved the differing count contracts through a native
Windows comparison; it does not silently accept 47 or claim native parity.
ContainerId remained unavailable and WASAPI remained unattempted. SDL's four
independent input/output cases passed; GE/UMU remains a separately labeled test.
