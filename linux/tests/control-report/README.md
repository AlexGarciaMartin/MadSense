# Owned-VM HID control framing experiment

This independent experiment retains the original broker and nine core functional
gates, including disposal, before a second core actor starts. Only the second
actor is built from an explicitly recorded source copy. The copy adds a bounded
observer at the four-argument `VirtualUsbDevice.HandleControlRequest` entry,
before HID report-ID normalization or existing dispatch logic;
it does not change normalization, acknowledgement order or responses. Every
source transform is exact, reversible and hashed against the committed archive.
No latency claim applies to this copy.

The ordinary consumer receives temporary ACL access to the exact attributed
virtual HID node and USBFS node below VHCI. Existing permission on unrelated USB
root hubs is recorded, never expanded; unrelated USBFS write access fails the
guard. The C observer rechecks device identity, the USB parent, bus/device/rdev,
Sony VID/PID and virtual `02:D5` MAC. It never opens a physical device, detaches a
kernel driver, changes a quirk or changes kernel/security policy.

The fixed sequence is:

1. `write(hidraw, report02 + 47 bytes)`, RGB `456789`.
2. `HIDIOCSOUTPUT` with the same shape and a different RGB marker, `56789a`.
3. `USBDEVFS_CONTROL`, recipient interface 3, SET_REPORT output02, all 48 bytes
   including the report ID, RGB `6789ab`. The descriptor first drops unrelated
   USBFS privileges. `EBUSY` is retained without attempting to unbind `usbhid`.
4. `HIDIOCSFEATURE` sends `80 03 00`, then GET_FEATURE81 reads all 64 bytes. A
   status response must have its marker at byte1 and status1 at byte4.

The C observer and evidence checker also support Edge feature65 with a distinct
63-byte echo payload. The first runner deliberately uses the existing DualSense
fixture; Edge execution is not yet wired or claimed.

`HIDIOCSOUTPUT` is not assumed to use the control endpoint. Linux v6.12
[`hidraw_send_report`](https://github.com/torvalds/linux/blob/v6.12/drivers/hid/hidraw.c)
tries the hardware output-report method before the raw-report fallback.
[`usbhid_set_raw_report`](https://github.com/torvalds/linux/blob/v6.12/drivers/hid/usbhid/hid-core.c)
preserves a nonzero report ID in the buffer and submits the complete count in
SET_REPORT. The actual class observer decides which route occurred.
[`devio.c`](https://github.com/torvalds/linux/blob/v6.12/drivers/usb/core/devio.c)
checks/claims the recipient interface for USBFS control; refusal while the kernel
owns it is an experimental limit, not a reason to bypass that ownership.

Each case retains its API count/error, monotonic operation/window boundaries,
actual setup fields and unmodified OUT data, accepted complete raw USB output,
and CRC-checked synthetic Bluetooth output. Output windows are isolated and
include 500 ms for dispatch after USB acknowledgement. The marker differs from
the preceding core gate and every prior case, avoiding no-change deduplication.
The verifier never removes a duplicated report ID or repairs shifted feature
bytes. USBFS refusal, observed protocol mismatch and capture/cleanup failure are
separate fields. A complete observation is not a compatibility pass. The outer
job remains failed when any protocol case is unverified or mismatched.

The class recorder admits at most 256 requests with at most 256 OUT bytes each,
using a 128-entry channel and a separate writer. It cannot silently discard data:
all rejected, unwritten or failed snapshots invalidate final capture. Existing
raw-output capture must also drain exactly. The host independently replays the
retained JSONL and requires all account/process/file/ACL cleanup before judging
the result. Images, SSH keys and private VM state are excluded from artifacts.

Build on Linux, from a commit containing these files:

```sh
bash linux/tests/control-report/build_control.sh /tmp/madsense-control-source dist/control-build
PYTHONPATH=linux/tests/control-report:linux/research python3 -m unittest discover -s linux/tests/control-report -p 'test_*.py' -v
```

The independent `linux-control.yml` workflow compiles on relevant pushes. A VM
runs only with explicit `workflow_dispatch` input `run_native=true`. Debian 13
uses the existing official image pinned by HTTPS and SHA-512, without a GPG
signature; Ubuntu uses its existing signature-verified image. Both inherit the
same QMP/SSH/owned-guest checks, bounded KVM ACL lifecycle and exact cleanup.

## First native result

[Run 41659561451](https://github.com/AlexGarciaMartin/MadSense/actions/runs/41659561451)
used commit `add05054ac2999eeb57c6d72a833ffbac5ba7cdd`. All eleven evidence/payload
tests, the C observer and copied managed actor compiled/passed on Linux. Retained
artifact SHA-256 values are:

- Build: `a616b33f39432675ddad647bd1a97dcca06c10b1a35cfe856114ed614af1bfff`.
- Runtime: `af59e5474e55f247ea1a68851fe6febb0945ee4820b6cf7b152318dee11f8ec4`.

The owned Debian guest ran kernel `6.12.113+deb13-amd64`, package version
`6.12.113-1`. The original ten broker and nine core gates passed, followed by all
nine disposal/functional gates in a fresh instrumented actor (PIDs 3273 and
3604). The fixed observations were:

| Case | Actual API result | Retained evidence |
| --- | --- | --- |
| hidraw write | 48 | Exact raw USB48, then matching CRC-valid Bluetooth RGB `456789`; no SET_REPORT |
| HIDIOCSOUTPUT | 48 | Exact raw USB48, then matching CRC-valid Bluetooth RGB `56789a`; no SET_REPORT |
| USBDEVFS_CONTROL | -1, errno16 (`EBUSY`) | No class request reached the actor; no detach attempted |
| FEATURE80 → 81 | SET returned3, GET returned64 | SET_REPORT setup `21 09 0380 0003 0003`, actual OUT data `80 03 00`; response starts `81 01 00 00 00`, expected status byte4 is `01` |

The feature observation demonstrates a real mismatch in the tested virtual
persona: Linux includes the nonzero report ID in the SET_REPORT data, whereas
the existing feature decoder consumes its first byte as the command payload.
The copied observer retained these bytes before dispatch; it did not alter them.
Output SET_REPORT duplication remains unproven natively because USBFS refused
that request. The successful HIDIOCSOUTPUT case used the interrupt route and
does not establish control-output compatibility. That run contains no production
fix; its source archive and original observer transform remain unchanged.

The class recorder retained all 15 requests with zero rejection/error; the raw
output recorder drained with zero rejected/unwritten snapshots. Both actor
cleanup checks passed. All six consumer ACL snapshots, including USBFS, were
restored exactly, the disposable account/processes/files were removed, QEMU
exited and the host's temporary KVM ACL was restored. The observed core read
all 570 received reports, ended with no queued report or proxy overflow, and
acknowledged sequence570. Kernel input backlog remains unmeasured.

An independent audit verified both ZIP hashes, every build member, the source
transforms, original ACL text and the four cases without importing the runtime
correlator. Its SHA-256 is
`4f8c3794268add66267f55e70de23961784e6c2dd6eaebe079698b47eb97ffd2`.
The job correctly remains failed: a complete, cleaned-up observation contains
both an untested control-output route and a feature-response mismatch.

## Linux report-ID correction

The subsequent candidate introduces `HidSetReportIncludesReportId`, disabled by
default in both server option types. Only the Linux app, core actor and neutral
probe enable it. The option travels as an immutable call parameter; existing
three-argument callers retain the original data convention. The neutral wrapper
forwards that parameter to its production persona.

For an output or feature SET_REPORT addressed to a HID interface with a nonzero
report ID, the selected convention requires a matching first byte and the full
declared transfer. It removes exactly that one ID before dispatch. ID zero is
unnumbered and remains untouched. Empty, mismatched or truncated numbered wire
reports stall before callbacks; an ID-only numbered report delivers an empty
body to the existing handler. Bytes beyond `wLength` remain ignored. GET_REPORT,
audio class routing, interrupt OUT and Windows/macOS defaults are unchanged.

The current observer transform records setup and original data at the base
entry, before this normalization. Its retained source hashes distinguish it from
the older `add0505` transform at `VirtualHidDevice.OnClassRequest`. Local tests
cover the recorded `80 03 00` command, Edge65's complete echo, output shape,
original USB/IP completion count and negative/size boundaries. The second native
run below verifies the corrected feature behavior. Output control remains natively unvalidated;
`EBUSY` will continue to fail that case and the outer compatibility job even if
the feature case succeeds. No driver detach is added.

## Corrected native feature result

[Run 41663090548](https://github.com/AlexGarciaMartin/MadSense/actions/runs/41663090548)
used commit `727e8b99245496276bef9cb3e811872e137228ee`. Build/tests passed and
both original baselines passed again: ten broker cases and nine core cases,
then nine cases in the fresh instrumented actor. Artifact SHA-256 values are:

- Build: `47667006df07c02bc78642ac63caf263a55838ecb71c0cbd8126349771cf0d08`.
- Runtime: `069185280b090aef4d8d45cf33fca9bc87f07fcd3a577c92bea49bdc0fb5e21d`.

The original setup remains `21 09 0380 0003 0003` and the original OUT data
remains `80 03 00` in the observer before normalization. SET returned 3 and GET
returned 64. Comparing all 64 response bytes with the first run, **only byte 4
changes, from `00` to `01`**: the new response starts `81 01 00 00 01`. This
validates the Linux feature framing correction through the real guest kernel.
Edge65 still has unit coverage only; this fixture did not execute an Edge case.

| Case | Native observation after correction | Compatibility evidence |
| --- | --- | --- |
| hidraw write | Returned 48; exact USB48 then CRC-valid Bluetooth RGB `456789` | Verified synthetic forwarding |
| HIDIOCSOUTPUT | Returned 48; exact USB48 then CRC-valid Bluetooth RGB `56789a`; no SET_REPORT | Verified interrupt route |
| USBDEVFS_CONTROL | Returned -1, errno16 (`EBUSY`); no class request or output observed | Control-output payload remains untested |
| FEATURE80 to 81 | Original wire request unchanged; exact expected 64-byte response, status byte 4=`01` | Verified feature correction |

The recorder retained all 15 class requests with zero rejected/error snapshots;
the raw-output recorder drained exactly. The core read and acknowledged all 584
received reports, with no queued report or overflow on exit. All six consumer
ACLs were restored byte-for-byte, the account/processes/files were removed, and
QEMU exit plus restoration of the temporary host KVM ACL were verified. No
physical actuation, games or latency result is claimed by this experiment.

The independent audit imports no repository verifier. It checks artifact and
build-member hashes, the exact source-copy transform, original setup/data,
complete response comparison against the retained `add0505` audit, raw USB and
Bluetooth CRC/markers, final recorder counts and cleanup. Its SHA-256 is
`0a5dc555b38bb43f3d87bdd56f083e5e842806dd23f29e528362ca9824a35629`.

The workflow's failure is retained deliberately: `observationComplete=true`
and `protocolCompatibilityPassed=false`. There is no remaining observed feature
mismatch in this run; the incomplete compatibility case is the kernel's USBFS
interface refusal. The refusal does not test the control-output decoder, and
successful interrupt output cannot substitute for that missing evidence. A
useful report therefore lists **observation complete**, **feature correction
verified**, and **control-output untested (`EBUSY`)** separately. It must not
rename the overall result to a compatibility pass or suppress the failed case.
