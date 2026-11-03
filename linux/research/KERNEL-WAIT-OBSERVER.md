# Owned guest kernel wait observer

The consumer workflow has a separate `observe_kernel_waits` dispatch input,
default `false`. It captures read-only diagnostics inside the already validated
owned VM. It changes no kernel, namespace, AppArmor, sysctl, service or device
policy, and does not turn an unsuccessful consumer run into a successful one.

The observer pins the broker's service PID, start time and root UID, and verifies
its command is the installed broker. It records those identities, not the command
line or environment. It also tracks the core PID and consumer `Popen` instances
created by this harness, plus observed same-UID children. It never walks all
processes or treats a reused PID as the original process. Short-lived/reparented
children can be missed; complete process enumeration is not claimed.

Every 100 ms it checks the exact broker's state. Only a `D` state triggers a
snapshot of owned tasks' `stat`, `comm`, `wchan`, `syscall` and kernel `stack`.
The broker's descriptor links identify a syscall's physical hidraw FD without
reading the device or any other descriptor content. Each field is limited to
8192 bytes, a snapshot to 64 tasks, tracked processes to 64, and the whole
JSONL to 64 snapshots and 4 MiB. Missing/denied reads remain `unavailable`.
Before the runner's final core/fixture cleanup it signals the worker and attempts
a join bounded to three seconds. `stoppedBeforeCleanup=false` and
`captureIncomplete=true` explicitly invalidate a capture if the worker remains
alive. Account cleanup between consumer sessions retains the ordinary process
identity checks; an exited or replaced process is never followed. The outcome is
recorded in `kernel-waits-summary.json`; diagnostics cannot replace the original
execution failure. No ptrace, signal, kernel tracing or timeout increase
is involved. Measurements with this observer are not latency measurements.

The reason for this experiment is [Debian run 41647961138](https://github.com/AlexGarciaMartin/MadSense/actions/runs/41647961138)
at `c002f93`. Its verified runtime ZIP has SHA-256
`313386dff4bd5bbc77f2a7b9290d6ea79fa1656b2a53aecbb5e2b0f7bbb9e0ce`.
The broker baseline and eight core functional stages passed. During the second
real HIDAPI/Wine 10 observation, a complete zero USB report arrived at
125.472105230 s; its decoded callback completed at 130.480499391 s and the
Bluetooth output reached UHID at 130.482943951 s, after the client declared a
five-second broker request timeout. The fixture continued emitting input during
that interval; no feature request was recorded there. This is evidence of a
stalled output path, not proof of its kernel cause. Consumer cleanup then found
the virtual nodes gone and correctly refused subsequent UDEV/GE sessions.

One hypothesis is a synchronous bridge dependency involving Linux's global
[`minors_rwsem`](https://github.com/torvalds/linux/blob/v6.12/drivers/hid/hidraw.c):
hidraw writes/ioctls hold a read lock and open/release need its write lock. The
current USB/IP handler calls `OnOutputReport` before `SendRetSubmit`, and that
controller callback synchronously writes the physical output. At `e7e29c2`, the
next capture was intended to distinguish this hypothesis from UHID GET/SET_REPORT
waits or another operation, without changing production behavior.

Ten portable regressions check PID/UID replacement, tricky process names,
unavailable and truncated fields, byte/snapshot limits, preservation of a broker
snapshot when a consumer exits, child/parent reuse and membership races, and
incomplete diagnostic shutdown and failure to start the worker.

## Native wait capture and interrupt OUT completion

[Run 41651563955](https://github.com/AlexGarciaMartin/MadSense/actions/runs/41651563955)
at `e7e29c2a2d8a48fe966e233c084b4dd33c6ea043` reproduced the five-second stall,
this time during `wine-sony-south`. Its original runtime ZIP SHA-256 is
`916fa906fcd3d0b90bdf833b2575143053dc702a7f66456a7042195cc109e92b`.
The broker and eight pre-consumer core stages passed; consumer/core cleanup did
not. UDEV and GE did not run. These failures remain part of the evidence.

The complete zero-state USB output arrived at 176.537013618 seconds. Forty-seven
snapshots from 176.546997702 through 181.443867322 show the exact broker blocked
in `write(fd=6, count=78)`, with FD 6 pointing to `/dev/hidraw0` and the stack at
`hidraw_write+0x28/0x70`. The fixture continued injecting input. The client
declared the isolation timeout at 181.540199829; the decoded output callback
finished at 181.545547385 and UHID received the Bluetooth report at 181.545728109.
All 50 observer snapshots fit 982,310 bytes, and the observer stopped before
cleanup. A vanished child is explicitly recorded as unavailable.

The matching official Debian package
[`linux-image-6.12.113+deb13-amd64_6.12.113-1`](https://deb.debian.org/debian/pool/main/l/linux-signed-amd64/linux-image-6.12.113+deb13-amd64_6.12.113-1_amd64.deb)
has SHA-256 `a4ad0bf9f084b199dd2b12cb8a2ed918abb442da467f40b772166a267c784a5b`;
its extracted `hid.ko` has SHA-256
`380352fe2a3c9eba4574048820888a31a33125f78d1a39e5526e31a447aceae6`.
Disassembly places the call to `down_read(minors_rwsem)` at offset `0x23`, with
return address `0x28`; `hidraw_send_report` is called later at `0x40`. This
locates the captured wait at lock acquisition in that matching packaged binary,
before UHID report delivery. The loaded guest module was not hashed, and the
reparented Wine process holding or waiting for the other side was not captured.
The complete lock cycle is therefore an inference from the stack, timing and
source, not a fully observed set of participating threads.

The candidate correction enables `AcknowledgeInterruptOutBeforeDispatch` only
for Linux's application and probes. The option defaults to false. After validating
the endpoint and receiving the complete interrupt OUT frame, the server writes
its completion to the socket, releases the socket write lock, then invokes the
physical-output callback. The callback still finishes before processing the next
URB: there is no new worker, queue, delay or report coalescing. Completion means
USB receipt, not confirmed physical delivery; physical RPC failure continues to
invalidate the broker session. Four new loopback tests cover both completion
orders, a partially received frame and an invalid endpoint. All 123 protocol
tests passed locally, as did the application/core builds. Native correction
validation is still pending.

Control, ISO and bulk completion order is unchanged. Control `SET_REPORT` needs
a separate real-kernel byte-level test: Linux's
[`usbhid_set_raw_report`](https://github.com/torvalds/linux/blob/v6.12/drivers/hid/usbhid/hid-core.c)
retains a nonzero report ID in the data stage, while the current virtual HID
output handler prepends the ID from `wValue`. Interrupt OUT results do not prove
that fallback path correct. No control-format or early-control-acknowledgement
change is included in this candidate.
