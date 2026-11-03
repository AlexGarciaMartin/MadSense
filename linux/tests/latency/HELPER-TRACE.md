# Helper verification duration observer

The first native observation completed in run
[41653223658](https://github.com/AlexGarciaMartin/MadSense/actions/runs/41653223658)
at commit `00484dc7316963828b13f6711f0663a4eafda116`. It reproduced input queue
limits; a successful research job does not mean loss-free transport. The original
hypothesis came from run 41646555561: three large broker-read gaps in each retained client
tail were about 503–504 ms apart, while injection remained near one millisecond.
`lease.verify()` runs synchronously before reading hidraw and schedules its next
check 500 ms after returning. This timing resemblance does not prove the cause.

## First native observation

The original helper passed the ten broker, nine core and ten application cases,
two idle upgrades and four removal cases. The observed helper then passed its
separate nine-case core cycle. Both observer architectures passed all 64 Python
tests and the native bounded-recorder tests. The four timing cases retained
277 complete helper scopes, with no overwritten/open scope or observer error.
The service's original files/configuration and idle state were restored; the
temporary source copy was removed, QEMU reaped and its temporary KVM ACL restored.

The collected runtime ZIP has SHA-256
`f7203eeade3c4f93684013d5ef81d1465635bf8c40d7e97211022d9d2725a03a`.
The independent audit recomputed 112 payload source hashes, four installed helper
hashes, both exact source transformations and the complete Git source archive.
Journal chunks and input CSV joins were also decoded independently of the guest
summary.

| 1000 Hz case | Written / observed unique | Termination | Maximum verify / inventory wall time |
|---|---:|---|---:|
| control-1 | 3196 / 3102 | Helper's eight pending reports; ENODEV around 3.2 s | 8.555 / 7.382 ms |
| input-1 | 644 / 610 | Client's eight-report queue; ENODEV around 0.65 s | 7.506 / 6.550 ms |
| input-2 | 9987 / 9799 | Completed ten seconds | 8.019 / 6.904 ms |
| control-2 | 9985 / 9831 | Completed ten seconds | 7.774 / 7.272 ms |

All four retain the `transport-limit` classification and confirmed cleanup.
The early cases leave 6798 and 9354 nominal reports unattempted respectively;
they are not comparable ten-second throughput populations. The control-1 app
snapshot preceded delivery of the helper failure and still had a null isolation
reason; the exact owner's journal establishes the helper queue failure. No
second acquisition/reconnection hid its metrics.

The failed input-1 recorder retained all 1873 events, with no overwritten events,
holes or collisions. A 7.506485 ms synchronous `verify` invocation, including
6.549615 ms in `inventory`, lies inside the 8.516836 ms broker-read gap between
source sequences 618 and 619. The helper then read sequences 619–627 within
0.535805 ms; the client received them within 0.817244 ms. Sequences 619–626 were
enqueued, and 627 failed at the eight-entry limit. The first subsequent dequeue
of 619 occurred 2.155891 ms after its receipt, after the queue failure; dequeue
alone does not prove delivery to the parser. A new `Read` had already started
after the preceding dequeue. This records a synchronous helper pause followed
by a burst that the client did not drain quickly enough. It does not identify
why the waiting reader resumed when it did.

There were zero .NET collection-count increments across this failed case, so a
.NET GC collection does not explain this occurrence. In the retained input-2
tail, the three largest broker-read gaps also contain synchronous verifications
of 4.023, 4.756 and 4.352 ms. Other gaps have no overlapping verification. Full
descriptor audits took up to 45.079 ms on their existing separate thread; the
wall/process/thread CPU clocks do not establish GIL contention or scheduler
causality. All timing cases used the observed helper, so these results do not
quantify its overhead or prove that every historical failure has the same cause.
No production scheduling, queue capacity or isolation rule was changed.

## Experiment and validation contract

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
digests and copied recorder provenance. The observer itself adds no offload,
flow-control protocol, buffer enlargement or relaxed isolation check. Existing
production flow control remains part of the exact recorded source commit; an
instrumented run does not silently remove it. The first observation documented
above predates negotiated consumer credits. No production installation is changed.
