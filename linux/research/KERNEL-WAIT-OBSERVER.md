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
controller callback synchronously writes the physical output. The next capture
must distinguish this hypothesis from UHID GET/SET_REPORT waits or another
operation. No production behavior is changed on the basis of the hypothesis.

Ten portable regressions check PID/UID replacement, tricky process names,
unavailable and truncated fields, byte/snapshot limits, preservation of a broker
snapshot when a consumer exits, child/parent reuse and membership races, and
incomplete diagnostic shutdown and failure to start the worker. Native stack capture
remains pending until an explicitly enabled workflow run.
