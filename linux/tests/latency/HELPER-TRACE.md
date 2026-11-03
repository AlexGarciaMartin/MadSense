# Helper verification duration observer

Prepared research experiment; no native measurement has run yet. The hypothesis
comes from run 41646555561: three large broker-read gaps in each retained client
tail were about 503–504 ms apart, while injection remained near one millisecond.
`lease.verify()` runs synchronously before reading hidraw and schedules its next
check 500 ms after returning. This timing resemblance does not prove the cause.

Dispatch `run_native=true`, `trace_input=true`, `trace_helper=true`, with
`trace_stages=false`. The original helper first completes the existing broker,
upgrade/removal, production-core and application gates. After those results and
an idle preflight are verified, a temporary unit drop-in changes only ExecStart
to an exact committed research copy under a fresh root-owned `/opt` directory.
The original unit, helper files, socket path, authorization, sandbox and
ExecStopPost remain intact. The drop-in is confined to the owned disposable VM.

The copied helper completes a separate nine-case production-core cycle before
the four fresh 1000 Hz control/input/input/control sessions. Those core results
are saved under `result/helper-observation/core-cycle`; they are not substituted
for the original functional gates. Both timing controls use the observed helper
and an unchanged app executable. The two input cases add the existing small
client-tail recorder. This comparison does not claim a fully uninstrumented
helper baseline or quantify observer overhead.

The copied methods are decorated without rewriting their bodies:

| Scope | Purpose |
|---|---|
| `verify` | Complete synchronous isolation check |
| `inventory` | Sysfs input-child enumeration inside that check |
| `check_audit` | Result/deadline processing and optional thread launch |
| `start_audit` | Thread-creation interval and queued-work accounting |
| `audit_foreign` | Full descriptor scan on its existing Python thread |

Each invocation records start/end monotonic wall, current-thread CPU and
whole-process CPU clocks, native thread ID, active state and return/raise outcome.
Owner PID/start identity, helper PID and lease ID bind the dump to the lease's
saved grab identities and the observer's process snapshots. Clock calls, a short
observer lock and tuple/list recording add cost; no value is subtracted as an
estimated correction. Process CPU includes concurrent threads. Wall minus CPU
does not distinguish scheduling, syscall waits or GIL contention. The standard
library documents these separate clocks in [Python 3.12 time](https://docs.python.org/3.12/library/time.html).

A 256-entry ring bounds memory. There are no per-input-report hooks or writes in
the helper observer. At lease termination, after the original close attempt,
the ring is snapshotted without waiting for an audit thread to finish. A worker
still executing, a queued audit that has not entered its wrapper, overwritten
history or an observer error makes complete-duration attribution invalid. An
error in the observer never replaces a method's original return value or
exception. The original cleanup exception also survives a dump failure.

The dump uses the existing stdout/journal route, not a writable directory in the
service sandbox. Compact JSON is limited to 64 KiB and split into at most 22
chunks of 3000 ASCII bytes with a common SHA-256 and numbered sequence. The
post-case bounded journal capture retains these chunks. The analyzer rejects
missing, repeated, conflicting, corrupted or foreign-process chunks and requires
the exact lease ID, owner PID/start and helper process recorded during timing.

The analyzer compares active verification/audit durations overlapping the
injection interval, and lists scopes intersecting retained client broker-read
gaps of at least two milliseconds. That threshold selects diagnostic examples;
it is not a performance or correctness gate. The original raw virtual-reader
CSV continues to determine timing/loss classifications. A temporal overlap is
evidence to inspect, not an automatic scheduling or GIL-cause conclusion.

Restoration requires a fresh idle preflight, no lease journal, recovery rule or
import, and systemd's `NFileDescriptorStore=0`. The descriptor-store property is
part of the [systemd service D-Bus interface](https://www.freedesktop.org/software/systemd/man/255/org.freedesktop.systemd1.html).
The coordinator refuses to stop the observed helper or remove its override if
those checks fail or original source/override identity changes. On successful
cleanup it stops the idle observed service, removes only its exact drop-in,
reloads systemd, starts the original helper, verifies original files/configuration
and idle state, then removes the unchanged root-owned copy. Cleanup failure is
retained and fails the research stage; it is never reclassified as successful
restoration. The owned VM remains the containment boundary for abnormal exit.

Guest and host verify the original gates, separate core-cycle result, source
overlay, service restoration and raw journal analysis independently. The source
copy retains the commit, complete source-archive digest, original/modified file
digests and copied recorder provenance. There is no offload, credit protocol,
buffer enlargement, relaxed isolation check or production installation change.
