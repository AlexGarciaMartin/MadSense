# Owned-VM HID control framing experiment

This independent experiment retains the original broker and nine core functional
gates, including disposal, before a second core actor starts. Only the second
actor is built from an explicitly recorded source copy. The copy adds a bounded
observer at `VirtualHidDevice.OnClassRequest` before its existing dispatch logic;
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

Current validation: the copied managed actor compiles for Linux x64 from the
40e2 source baseline. Evidence/source-transform tests pass locally. The C build
and the actual Linux control sequence are pending CI. No native report-ID bug is
declared fixed or proven by these source-level tests.
