# Real Windows HIDAPI consumer in the owned guest

This separate PE links unmodified upstream HIDAPI's **Windows implementation**.
It measures its actual feature/read/write behavior under Wine or an explicitly
identified derivative. The existing Win32 observer and its 48-byte assertion stay
unchanged. The consumer workflow now builds it as an independent artifact and
requests two extra cases in each fresh default/UDEV/GE session that starts.
Compilation with MSVC and portable source/evidence/lifecycle regressions have passed;
MinGW compilation and native Wine/GE execution remain pending.

HIDAPI 0.15.0's annotated tag resolves to commit
`d6b2a974608dec3b76fb1e36c189f22b9cf3650c`. The commit-addressed upstream archive is
364135 bytes with SHA-256
`a93a9bfb599831b82c6cef50f42359e0f5f31abfc8f6954862c328a464cbc4d0`.
This pin is an HTTPS retrieval digest, not a signature or a reproducible-build
claim. `hidapi_windows_source.py` verifies the bytes before retention/extraction,
rejects links, traversal and special files, and caps source expansion. The build
retains the complete source archive, source files/hashes, all four upstream license
files, MadSense/MinGW notices, compiler versions, PE imports and executable hash.
Upstream offers GPLv3/BSD/original-license choices; its exact texts are retained.

From Linux with Python 3.12+, MinGW-w64 GCC/G++, binutils and ordinary build tools:

```sh
bash linux/research/build_hidapi_windows_consumer.sh /owned-work/hidapi-build
python3 -m unittest discover -s linux/research -p test_hidapi_windows.py
```

No PE is executed by the build. Its CLI is:

```text
hidapi_windows_consumer.exe OWNED_QEMU_UUID dualsense|edge 02d5MACHEX south|neutral
```

Before loading devices it requires Wine's version export, the explicit guest
UUID/environment marker and QEMU DMI identity. Native Windows execution is refused.
The launcher uses the existing ordinary `ConsumerSession`, granting
only exact owned virtual nodes and proving cleanup. The PE enumerates Sony gamepad
collections, opens candidates through `hid_open_path`, and selects exactly one
whose real `hid_get_feature_report(09)` matches the full virtual `02:D5` MAC.
It reports 09/20/05 feature bytes, optional library ContainerId, a bounded actual
input report, then the RGB-only 48-byte USB report. It never writes a candidate
selected only by VID/PID or friendly name.

The final `observed` result means these observations completed. `hidapi-write`
preserves the actual return value, error string and full-count comparison;
it never asserts delivery or native Windows parity. On a failed write, the process
terminates without unwinding potentially pending upstream I/O storage. The session
orchestrator still owns process/account/ACL cleanup and an outer command timeout.

`hidapi_windows_evidence.verify(ready, command, core_rows, bluetooth_rows, core_ready)` is the
separate functional join. Consumer `ready` includes the exact features/MAC and raw
capture flag. The clock comes from the separate core `ready.json`; its owner UUID,
actor PID, exact virtual path and VID/PID must match before accepting
`timestampFrequency=1000000000`. `command` includes its ordinary
process interval, `fixtureState`, exit status and parsed JSONL rows. The join checks
feature-byte equality, actual input, exact raw USB48 and CRC-valid matching BT47
state in the same interval. It accepts library counts 47 or 48 **only with that
independent full-report evidence**, rejects zero/other short/error counts, and
reports full-count parity separately. The final core capture must still drain with
zero rejected/unwritten observations and no error; the helper explicitly leaves
that final requirement outstanding. It does not infer physical actuation, games
or latency from the synthetic stream.

`run_hidapi_windows.after_api` executes the independent cases after the strict
Win32 cases, including when those cases failed. It preserves and rethrows the
original API error; library success cannot change that result. Each PE keeps a
deliberate 250 ms observation window after its write, restores neutral fixture
input, and retains the independent join under `hidapiWindows`. The host summary
promotes forwarding only after final raw capture drain and account/ACL cleanup.
Default/UDEV/GE identities and failures remain separate. A valid Windows ContainerId can independently authorize the existing
WASAPI case even if the strict Win32 byte-count case fails; current shared logic
already attempts that audio case separately.

The first Ubuntu execution, [run 41643213193](https://github.com/AlexGarciaMartin/MadSense/actions/runs/41643213193)
at `0a9c30d`, retained runtime ZIP SHA-256
`1b193a80ddb36f89d28d32a7a177d7d43db433beb58aa257f118627905a4505b`.
All builds and Linux regressions passed; SDL passed four cases, and core raw
capture/account/ACL cleanup completed. HIDAPI did **not** prove forwarding: one
UDEV PE ran and reported no accepted MAC-verified path, before any write.
Other attempts stopped while repeating an unchanged all-clear request, which
the bridge correctly deduplicated. A verifier also looked for the clock field
in the wrong ready file. Both harness defects are corrected for the next run:
an observed distinct `a1b2c3` RGB preparation precedes the existing verified clear,
and the clock is joined from the core contract. Neither silence nor an old report
can satisfy preparation. Enumeration now records global `hid_error(NULL)` and
all returned VID/PID/usage fields before filtering; usage and full-MAC selection
remain mandatory. New execution is still required; no missing path is relabelled
as a successful HIDAPI write. The original Wine 9 ContainerId and strict 47/48
failures remain intact.

The follow-up [run 41645306210](https://github.com/AlexGarciaMartin/MadSense/actions/runs/41645306210)
at `4109a34` executed all four HIDAPI cases (default/UDEV, south/neutral) after
successful RGB preparation and clear. Each returned `listReturned=false` and
`Failed to get size of HID device interface list`, before feature/read/write.
Runtime ZIP SHA-256 is
`c6ea328a2c4d430fe07e2d850a67afef3da687ac256d6e9ab1853cad27f79337`.
SDL remained 4/4; both accounts/ACLs and the final raw observer were cleaned up.
The clock/reset harness defects no longer mask the actual enumeration failure.

This is specific to the real HIDAPI 0.15 API path. Its pinned
[enumerator](https://github.com/libusb/hidapi/blob/d6b2a974608dec3b76fb1e36c189f22b9cf3650c/windows/hid.c#L836)
first calls `CM_Get_Device_Interface_List_SizeW`. Upstream
[Wine 9](https://github.com/wine-mirror/wine/blob/wine-9.0/dlls/setupapi/stubs.c#L367)
and [Wine 10](https://github.com/wine-mirror/wine/blob/wine-10.0/dlls/setupapi/stubs.c#L367)
return `CR_FAILURE` there. The observed Wine 9 error identifies that same branch.
GE's pinned [Wine implementation](https://github.com/ValveSoftware/wine/blob/c53678ab22a85f0fad3588dd34f0452a463bd015/dlls/cfgmgr32/cfgmgr32.c)
implements interface sizing/list enumeration; its runtime still needs to be tested.
This does not establish failure of older HIDAPI builds, SDL's vendored backend,
other Windows APIs or any game, and it does not change the full-MAC selection gate.

Both PE observers record the actual loaded Wine's `wine_get_build_id` separately
from its version, executable digest and source pin. A missing export produces
JSON null; GE requires a nonempty observed build ID when the PE actually runs.
The build string is not a cryptographic attestation of upstream source or patches.

The distinction matters in real libraries:

- [HIDAPI Windows 0.15.0](https://github.com/libusb/hidapi/blob/d6b2a974608dec3b76fb1e36c189f22b9cf3650c/windows/hid.c#L1072)
  returns the count from `WriteFile`/`GetOverlappedResult` without an equality test.
  Its public contract reports a byte count and uses -1 for errors.
- [SDL 3.2.28's rumble worker](https://github.com/libsdl-org/SDL/blob/release-3.2.28/src/joystick/hidapi/SDL_hidapi_rumble.c)
  queues the report separately and discards the eventual `SDL_hid_write` return.
  The PS5 driver's size check covers enqueueing, not synchronous device delivery.
  This source behavior is motivation for a future real SDL Windows PE, not proof
  that an unexecuted SDL game already works.
- Wine 9's [HID class completion](https://github.com/wine-mirror/wine/blob/wine-9.0/dlls/hidclass.sys/device.c#L398)
  and [test_write_file](https://github.com/wine-mirror/wine/blob/wine-9.0/dlls/dinput/tests/hid.c#L1264)
  account for the numbered report ID differently from this project's strict
  Win32 count gate. Observed 47 plus complete USB48/BT forwarding therefore needs
  separate labels, not a blanket compatibility pass or a payload-loss claim.
