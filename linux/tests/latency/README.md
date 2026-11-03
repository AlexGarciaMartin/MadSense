# Synthetic application input timing

This optional experiment measures an attributed synthetic UHID input travelling
through the real LinuxBridgeController, physical-session broker RPC, parser,
raw converter and USB/IP import to that lease's virtual hidraw reader. It reuses
the existing ordinary AppProbe executable; no production factory, endpoint,
authorization or scheduling policy is replaced. It does not run the GUI.

Native C compilation and all fifteen validation tests passed on Linux x64 and
arm64 at commit `0325da7` in [run 41629387209](https://github.com/AlexGarciaMartin/MadSense/actions/runs/41629387209).
That run also completed the first native timing experiment after all 10 broker,
9 core and 10 application cases, two idle installations and four removal cases
passed in the same owned KVM guest. The collected CSVs were recomputed using the
verifiers from the exact commit; 79 guest source files and four installed helper
files were checked against their recorded hashes.

| Synthetic input | Written / observed unique | Missing written reports | Upper interval p50 / p95 / p99 | Maximum upper interval |
|---|---|---|---|---|
| 100 Hz, 10 s | 1,000 / 1,000 | 0 | 0.126 / 0.254 / 2.338 ms | 4.419 ms |
| 1,000 Hz, 10 s | 9,998 / 9,954 | 44 | 0.095 / 0.164 / 0.720 ms | 3.520 ms |

The 1,000 Hz producer skipped two deadlines before injection, separately from
the 44 written sequences absent at the virtual reader. Its classification is
`transport-limit`, so the overall benchmark retains that classification even
though the workflow successfully validated and collected the experiment. Neither
case overflowed the broker client queue, discarded reports during initialization,
or reordered observed sequences. Repeated reads of an already observed sequence
were counted separately (9,851 at 100 Hz; 9,543 at 1,000 Hz), not counted as new
input samples. All case cleanup, QEMU shutdown and KVM ACL restoration passed.

These percentiles describe only successfully matched synthetic reports in this
one shared-host VM run. They are not physical Bluetooth or game latency, do not
include missing samples as successful measurements, and do not establish the
cause of a lost sequence. No production scheduling, queue or priority change is
justified by this single run. The original runtime artifact SHA-256 is
`e0ec2ba79e1acdbe6e0b8d756979e306b58e636ee70241bd308968b441c8701a`.

Run only after all broker, core and application functional gates passed in the
same owned QEMU guest. The runner checks their case sets and ownership tokens,
then makes a fresh fixture, ordinary account, settings directory and real
application session for each rate: 100 Hz for 10 seconds, then 1000 Hz for 10
seconds. It refuses an existing lease, recovery rule, import or Sony device.
The application publish tree uses the shared actor_staging helper in a fresh
root-owned `/opt` directory, with explicit file modes and an ordinary-UID
execution preflight before fixture/device changes. No mount is remounted.
Temporary polkit/udev test policies name only that case's user and UHID parent.
They never enter a production package. The fixture uses the real hid-playstation
driver and its normal feature handshake.

The separate C process inherits the already owned UHID descriptor and an exact
read-only virtual hidraw descriptor. Before opening the latter, the root guest
coordinator checks the lease journal's physical/import parent identities, the
fixture name/physical path/pairing identity, canonical UHID/VHCI ancestry and
character-device identity. C checks the inherited node types, USB VID/PID and
CRC-valid Bluetooth input template. It never selects or opens a device path.

The producer uses absolute CLOCK_MONOTONIC deadlines and no real-time priority
or affinity. An expired period is counted and skipped; it is never replayed as
a catch-up burst. A per-case nonce and sequence in the sensor timestamp survive
the production raw converter; the reader verifies the entire 64-byte USB report
against its corresponding Bluetooth payload. At most 10,000 sample slots stay
in memory until the producer and reader have stopped. There is no per-report
log in the timed producer, consumer or production application actor. The fixture
continues answering features and recording physical output; its background
100 Hz input and input log are disabled during measurement. A possible final
background report is rejected by nonce.

Each matched sample retains the scheduled deadline, timestamps immediately
before and after UHID write, and immediately after virtual hidraw read. All use
the guest's CLOCK_MONOTONIC. Since the point inside the write when the kernel
accepts the report is unknown, elapsed time is reported as an interval:

- Lower: max(0, observed minus write-return timestamp).
- Upper: observed minus write-entry timestamp.

The CSV preserves each individual slot. JSON reports nearest-rank p50/p95/p99,
maximum, producer scheduling lateness, write duration, deadline skips, attempted
and completed writes, unattempted slots after failure, missing written sequences,
duplicates and reordering. It rejects inconsistent counters, altered tagged
payloads and impossible timestamps. The reader allows one second to drain after
the producer stops. A missing final observation remains a loss, not a latency
sample silently removed from the denominator.

Results distinguish `measured`, `generator-limit`, `transport-limit` and
`no-samples`. Coalescing/latest-state behavior, queues and kernel-reader losses
can all cause missing virtual sequences; this measurement alone cannot attribute
which stage lost one. An overflow or disconnected virtual reader is evidence of
a limit at that workload, and does not turn the prior functional gates red.
`invalid` identifies a broken experiment or unconfirmed cleanup. Empty observed
populations have null percentiles and are never described as a measurement.

CPU evidence includes before/after whole-process ticks for the real application,
broker and coordinator (including its fixture thread), process start identities,
RSS/thread snapshots and the C probe's own user/system time. The C interval
includes startup plus the drain, but excludes CSV/JSON serialization. Back-to-back
clock-read min/max estimates timestamp cost; it is not subtracted from samples.
AppProbe takes diagnostics every 500 ms and checks commands every 25 ms. That
observer overhead, fixture output logging, kernel work, shared hosted CPUs and
KVM/TCG scheduling remain part of this synthetic workload. The trace experiment
checks clock alignment, but no baseline has quantified their total perturbation.

After each case, the application pauses and disposes. A fresh helper preflight
must confirm cleanup, while the root observer independently requires removal of
the lease/rule/import and exact restoration of the fixture node identities,
ownership, modes and ACLs. Only then is `cleanupConfirmed` set true. The fixture
stays alive during helper recovery. Unconfirmed cleanup stops the second rate;
the owned VM can retain the evidence for diagnosis. Test accounts and exact test
policy files are removed only by their owning runner.

This is input pipeline timing inside one synthetic guest, not physical Bluetooth
latency, controller polling, game responsiveness, acoustic output or vibration
latency. It sets no performance threshold and does not justify production tuning
without a repeatable baseline and a measured comparison.

Prepare on Linux (C compiler and Linux userspace headers required):

```sh
python3 -m unittest discover -s linux/tests/latency -p 'test_*.py'
```

After the existing functional runner, as root inside its attributed guest:

```sh
python3 linux/tests/latency/run_latency_guest.py \
  --owned-guest-token UUID \
  --baseline-result /home/ubuntu/madsense/result/result.json \
  --core-result /home/ubuntu/madsense/result/core-experiment/result.json \
  --app-result /home/ubuntu/madsense/result/app-experiment/result.json \
  --upgrade-result /home/ubuntu/madsense/result/idle-upgrade/result.json \
  --probe-directory /home/ubuntu/madsense/app-probe \
  --output /home/ubuntu/madsense/result/latency-experiment
```

The coordinator needs the existing research/platform/app integration modules in
the same payload tree, plus this directory and `cc`. It compiles C with warnings
as errors and records compiler, command and source/binary hashes. The actual
AppProbe assembly provenance appears in each copied actor event log. Keep this
invocation and exit code separate from the functional gate result.

The `linux-latency.yml` workflow always runs validation on push. Runtime is a
separate x64 job enabled only by the `run_native=true` workflow-dispatch input
(default false). It publishes the real neutral/core/AppProbe actors, then
`qemu_latency.py` uses one owned VM for baseline, upgrade/removal, core, app and
timing. The existing stages/verifiers and signed-image/KVM lifecycle are reused;
no kernel matrix or second baseline is run for timing. Every functional result,
including removal/reinstallation, is verified inside the guest before creating
any timing fixture or account. Collected CSVs, gate hashes, fresh cleanup ACKs
and restored fixture snapshots are checked again on the host.

`functionalResult` and `benchmarkResult` remain separate in provenance. Missing
or failed functional gates skip timing. A measured workload limit retains its
classification without failing functional results; invalid timing evidence or
unconfirmed cleanup fails this independent workflow job.

An existing live-VM owner can reuse `add_latency_payload(payload)` before its
single upload and `run_latency_stage(guest)` after its functional stages, without
booting another guest. The current functional launcher is unchanged. A completed
workflow's already stopped VM cannot be reused; the opt-in standalone launcher
necessarily runs its own gates once. It collects evidence only once, then stops
its own QEMU before restoring the temporary KVM ACL.

## Separate internal attribution experiment

The optional dispatch input `trace_stages=true` (with `run_native=true`) builds
two actors from the same commit. The control is the unmodified production actor.
`stage_trace.py` exports committed source into a fresh directory outside the
checkout and inserts exact, uniquely matched hooks only into that copy. It
records original and patched hashes. The normal library, app package, core gates
and Windows sources are unchanged by this experiment.

The experimental actor records tagged reports after USB conversion, while
publishing the raw slot, after pulling an interrupt payload, and after the
USB/IP send method returns. Delivery hooks also retain generation and periodic
versus fresh-worker origin. A preallocated array holds at most 131,072 records;
there are no per-report allocations, JSON, file writes or socket writes added
by the recorder. It dumps after bridge disposal. Overflow, a malformed sequence
or incomplete slot publication invalidates attribution instead of implying loss.

The caller selects the nonce before starting the actor; the C producer echoes
the same nonce. Only matching USB64 reports are recorded. The C hidraw reader
continues validating the entire payload. `Stopwatch` must report a frequency
of one billion ticks/second. Its Linux source uses `CLOCK_MONOTONIC`, matching
the C producer/reader: see [Stopwatch.Unix.cs](https://github.com/dotnet/runtime/blob/v10.0.0/src/libraries/System.Private.CoreLib/src/System/Diagnostics/Stopwatch.Unix.cs),
[SystemNative_GetTimestamp](https://github.com/dotnet/runtime/blob/v10.0.0/src/native/libs/System.Native/pal_time.c)
and [minipal_hires_ticks](https://github.com/dotnet/runtime/blob/v10.0.0/src/native/minipal/time.c).
Before connecting, the recorder also brackets 32 native `clock_gettime` calls
between Stopwatch reads and requires every timestamp to lie inside its bracket.
These internal timestamps describe an instrumented copy, never physical latency.

Each rate gets four fresh sessions: control/trace/trace/control at 100 Hz and
trace/control/control/trace at 1,000 Hz. Reports retain the individual reader
latencies, losses, generator skips, CPU and cleanup for all eight runs. This
allows observer effects and order variation to be inspected; it does not
automatically quantify or subtract instrumentation overhead.

Missing written reports are partitioned by the first unobserved boundary:
before parser, parser-to-slot, slot-to-interrupt, send not confirmed, or after
send returned. A slot-to-interrupt gap is consistent with latest-state
replacement, but the label does not prove why the worker/pacer missed it. A
returned send does not mean the kernel or game consumed the report. The analyzer
rejects impossible stage chains and accepts a hidraw read before the writer's
send returns, since those can race legitimately. The host recomputes attribution
from the collected trace and original C CSV.

### First paired native observation

[Run 41638222617](https://github.com/AlexGarciaMartin/MadSense/actions/runs/41638222617)
at `f6e2d097153ab52942c003e16dcc138d8262b79d` completed the functional gates and
eight fresh sessions. The collected CSVs and traces were independently
recomputed using that commit's verifiers. All 95 payload sources, four installed
helper files, five instrumentation hashes and the exact committed-source tar
were checked. The source tar SHA-256 is
`95043ca081d473e6abe50ee338836789331f6dafd5970541f0e33687bf43d521`;
the runtime artifact SHA-256 is
`01c910cee5989f5708a8124235cb0175c871fa5ac14bae6f2da2f017aaff2179`.

| Case | Observed / written | Skipped deadlines | Upper interval p50 / p95 / p99 |
|---|---|---|---|
| 100 Hz control 1 | 1,000 / 1,000 | 0 | 0.306 / 0.813 / 5.509 ms |
| 100 Hz trace 1 | 1,000 / 1,000 | 0 | 0.313 / 0.749 / 5.804 ms |
| 100 Hz trace 2 | 1,000 / 1,000 | 0 | 0.333 / 0.888 / 5.570 ms |
| 100 Hz control 2 | 1,000 / 1,000 | 0 | 0.364 / 0.873 / 5.711 ms |
| 1,000 Hz trace 1 | 9,799 / 9,989 | 11 | 0.239 / 0.430 / 1.297 ms |
| 1,000 Hz control 1 (ended early) | 180 / 219 | 2 | 0.284 / 1.011 / 1.523 ms |
| 1,000 Hz control 2 | 9,842 / 9,987 | 13 | 0.236 / 0.431 / 1.369 ms |
| 1,000 Hz trace 2 | 9,862 / 9,994 | 6 | 0.243 / 0.395 / 1.164 ms |

The first 1,000 Hz control reader reported `ENODEV` before the requested pause;
9,779 nominal samples were never attempted. Its approximately 0.332-second
producer/reader interval is not a completed ten-second repetition. The retained
events contain one connection and no reconnection. Its before/after metric
snapshots retain the same feature-RPC count and setup-drain count. Those facts
do not identify why the virtual reader ended. This run did not retain the
broker/udev/kernel journals after timing, and the application's saved diagnostic
text did not include the session's first isolation-loss reason. A zero client
overflow counter therefore cannot establish the helper's reason for closure.

For both 1,000 Hz trace sessions, every written sequence reached the parsed USB
report and raw slot. The 190 and 132 missing reader sequences, respectively,
were absent from the interrupt-payload stage. Every sequence observed there was
also recorded after send returned and at the virtual hidraw reader. The two
100 Hz traces lost no written sequence. All trace buffers stayed within their
bounds, and the clock alignment checks passed. This localizes the observed
trace losses to the slot-to-interrupt boundary; it does not establish a scheduling
cause or show that the uninstrumented early closure had the same cause.

All eight case cleanup checks, QEMU shutdown and exact KVM ACL restoration
passed. The benchmark classification is `transport-limit`. Percentiles include
only matched reports, excluding missing or unattempted samples. The truncated
control and limited repetitions prevent a reliable numerical estimate of
instrumentation overhead. Neither this table nor its stage attribution measures
physical Bluetooth latency or justifies a production queue/scheduling change.

### Repeat with closure diagnostics

[Run 41641527528](https://github.com/AlexGarciaMartin/MadSense/actions/runs/41641527528)
at `adccde1b59ffbc08bc85e3cf9cceb9b9cd1387bd` repeated the same eight sessions.
The exact-source audit verified 98 payload files, four installed helper files,
all instrumentation changes and the committed-source tar, then recomputed the
CSV/trace results. The runtime artifact SHA-256 is
`bc5a3bd2d64f8cd6be810db49ef66e90f9ccc809394efb00e895cc40fd48ea81`.

| Case | Observed / written | Skipped deadlines | Upper interval p50 / p95 / p99 |
|---|---|---|---|
| 100 Hz control 1 | 1,000 / 1,000 | 0 | 0.312 / 1.002 / 5.707 ms |
| 100 Hz trace 1 | 1,000 / 1,000 | 0 | 0.339 / 0.683 / 5.648 ms |
| 100 Hz trace 2 | 1,000 / 1,000 | 0 | 0.301 / 0.575 / 5.504 ms |
| 100 Hz control 2 | 1,000 / 1,000 | 0 | 0.337 / 0.827 / 5.685 ms |
| 1,000 Hz trace 1 | 9,816 / 9,987 | 13 | 0.284 / 0.452 / 1.281 ms |
| 1,000 Hz control 1 (ended early) | 3,077 / 3,176 | 10 | 0.297 / 0.540 / 2.018 ms |
| 1,000 Hz control 2 (ended early) | 8,022 / 8,192 | 7 | 0.286 / 0.450 / 1.339 ms |
| 1,000 Hz trace 2 | 9,867 / 9,995 | 5 | 0.239 / 0.439 / 1.276 ms |

Both 1,000 Hz controls ended with `ENODEV`. Their retained first isolation-loss
reason identifies the client's eight-report input queue overflow, with
`InputOverflows=1` and `isolationVerified=false` in each. They entered Releasing
before the runner requested Pause. Their producer/reader intervals lasted about
3.294 and 8.308 seconds, leaving 6,814 and 1,801 nominal samples unattempted.
This is evidence of the current client transport limit at this synthetic load,
not evidence of why its consumer failed to keep up. It also does not establish
the unrecorded cause of the earlier f6 closure.

All four 100 Hz cases delivered every written report. Both 1,000 Hz traces
completed the ten-second injection window without client overflow. Independent
sequence-set checks place their 171 and 128 missing written reports exclusively
between the raw slot and interrupt-payload observation; the parsed and raw-slot
sets equal every written sequence, and interrupt/send-return/reader sets agree.
No stage buffer overflow or clock-alignment failure occurred. The traces and
controls still do not provide a reliable overhead estimate: two controls ended
early, and instrumentation itself can perturb the workload.

All eight cleanup checks and final QEMU/KVM ACL restoration passed. The 32
post-case commands completed without timeout or truncation; all 64 output/error
stream sizes and hashes were independently checked. Journal entries were joined
to each actor's monotonic interval so older baseline recovery exceptions were
not attributed to a timing case. No additional helper failure was logged inside
the two failed control intervals. The overall result remains `transport-limit`,
with successful matched-report percentiles kept separate from missing and
unattempted samples. No production capacity, priority or scheduling change was
made in response to this observation.

### Post-case diagnosis

After stopping/reaping the actor and attempting helper cleanup, the runner calls
`capture_diagnostics.py` before destroying the fixture. The owned-QEMU/root guard
is checked again. It records the newest 400 JSON entries from this boot's broker,
udevd and kernel journals, plus the broker's current systemd state. Monotonic
journal timestamps allow events from a shared service to be matched to a case;
entries can include earlier cases and are not themselves causal proof.

Each static read-only command has a ten-second deadline and separate one-MiB
stdout/stderr bounds. Oversized output stops and reaps only that command's child;
the retained prefix, truncation, timeout, exit status and hashes remain visible.
No journal is cleared, no service is restarted and no guest policy is changed.
Capture failure is retained independently and never replaces the original timing
classification or confirms cleanup. These captures were added after the f6
native run and exercised in the adcc repeat; they cannot retroactively explain
the f6 early closure.

### Client-input tail experiment

`trace_input=true` on the timing workflow selects four fresh 1,000 Hz sessions
in control/input/input/control order. It is mutually exclusive with
`trace_stages`; the existing eight-case full-stage schedule and two-rate
uninstrumented smoke remain available. The same functional gates must pass
first, in the same owned VM. Production queue capacity, RPC behavior, priorities,
and loss handling are unchanged.

`stage_trace.py --kind input` exports the exact committed source into a fresh
external directory and changes only `LinuxPhysicalSession.cs` and AppProbe's
start/dispose calls. The added `InputTrace.cs` is copied from that same archive.
All original/modified hashes, the source commit and the complete archive digest
are recorded. This variant does not compile the full-stage recorder and inserts
no parser, raw-slot or USB/IP hooks. Normal functional actors and timing controls
still use the untouched publish tree.

The copied client records accept, successful enqueue, failed enqueue, and
dequeue-before-`EnsureConnected` events. A dequeue therefore means removal from
the channel, not successful delivery, parsing or virtual output. Each record
contains the synthetic nonce/sequence, monotonic event/receive/helper-read
timestamps, managed thread ID, latest synchronous `Read` after validation and
`BeginReading` (before its wait), and
`Channel.Reader.Count`. That count is an unsynchronized snapshot: a dequeue may
already have changed it after `TryWrite` failed. A successful enqueue hook may
run after the consumer has dequeued the published input. Both races are allowed
by the analyzer.

Only the most recent 4,096 events are retained in roughly 256 KiB of fixed
storage. The ring does no per-report allocation or file I/O and never waits for
a slot. Reservation sequences identify every retained slot. Ordinary wrapping
records total attempts, exact overwritten-prefix length and tail bounds; it is
not silently described as complete history. A busy slot, delayed old writer,
publication hole, malformed tagged report or unfinished writer invalidates
attribution. The CSV is dumped after bridge disposal. Earlier overwritten events
cannot support claims that an input was absent at any stage.

The analyzer checks every retained tag against the C producer's actual writes,
and validates timestamps with the same 32 `clock_gettime(CLOCK_MONOTONIC)` /
Stopwatch bracket checks as the full-stage observer. It reports retained
receive/helper-read/dequeue gaps, helper-to-receive and receive-to-dequeue
intervals, and context around any retained enqueue failure. The original
virtual-reader CSV remains authoritative for losses and timing. Guest and host
recompute the same analysis; stale evidence from the other variant is rejected.

The extra timestamp calls, atomics and channel-count reads can perturb timing.
The copied `InputReport` also carries one additional `long` so its helper-read
timestamp remains available when dequeued. GC collection counts, approximate
allocated bytes and the latest GC index are sampled only before connect and
after disposal, not for each report. These endpoint snapshots include setup and
teardown and cannot identify an individual pause or establish that GC caused an
overflow. Controls do not gain any GC instrumentation. Neither an overflow nor
its absence in this variant alone establishes a scheduling cause or an overhead
estimate.

Local Windows validation: 45 Python tests passed, with the native C test skipped;
the standalone recorder and complete copied AppProbe compile without warnings.
All 46 Python tests and the three native recorder checks (concurrent publication,
repeated tail wrapping, and a deliberately busy slot) passed on Linux x64 and
arm64 in the run below, before the VM measurement.

### First client-input tail observation

[Run 41646555561](https://github.com/AlexGarciaMartin/MadSense/actions/runs/41646555561)
at `3767557ffb61022e8073b7d45e36b08b0554d58a` completed all four ten-second
sessions. The independent audit checked 104 payload sources, four installed
helper files, all three overlay changes and the exact source archive, then
recomputed the original CSV and client-tail results. Runtime artifact SHA-256:
`0d44c562f4d017998d6886b9656e0781c6ff0e591345c5c160a19e2018b62e3a`.

| 1,000 Hz case | Observed / written | Skipped deadlines | Upper interval p50 / p95 / p99 |
|---|---|---|---|
| Control 1 | 9,902 / 9,994 | 6 | 0.182 / 0.301 / 1.062 ms |
| Client tail 1 | 9,907 / 9,996 | 4 | 0.178 / 0.293 / 0.998 ms |
| Client tail 2 | 9,895 / 9,978 | 22 | 0.182 / 0.286 / 1.105 ms |
| Control 2 | 9,891 / 9,989 | 11 | 0.183 / 0.292 / 1.028 ms |

All cases had zero input overflows, no `ENODEV`, and no unattempted samples.
Each actor had one recorded lease acquisition and one transition into Ready;
its setup-drain count remained unchanged. A reconnection therefore did not hide
the previous session's metrics in this run. The prior adcc overflow did not
recur, including in either untouched control. This does not show that it was
fixed. Missing written reports keep the result classified as `transport-limit`.

Each instrumented case retained 4,096 events covering about the final 1.37
seconds, with 25,892 and 25,838 earlier events explicitly overwritten. There
were no collisions, publication holes, malformed tags or clock-check failures.
The maximum retained queue-count snapshot was three. Nine missing virtual-reader
sequences in each tail had been removed from the client channel; those specific
losses occurred after removal. The overwritten prefix cannot support equivalent
attribution. Dequeue itself still does not prove parsing or USB transmission.

The largest retained dequeue intervals were 4.401 and 5.081 ms. Their corresponding
helper-read intervals were already 4.319 and 5.056 ms, despite UHID injection
intervals near one millisecond. The consumer had entered its next `Read` about
2–3 microseconds after the preceding dequeue, and the eventual reports took only
29 and 20 microseconds from client receipt to dequeue. These examples locate the
large gap before the helper-read observation, rather than in the client queue.
Three such gaps per tail were separated by approximately 503–504 ms. The
helper's synchronous `lease.verify()` followed by a 500 ms deadline is a candidate
for further measurement; the capture contains no verification start/end times,
so it does not establish that cause. Other retained gaps include delays after
helper read and before the consumer's next `Read`, without proving what caused
them.

Both GC endpoint snapshots increased by two generation-0 collections and one
generation-1 collection, with no generation-2 collection. These counts cover
setup through disposal and provide no pause timestamps. They neither attribute
the gaps to GC nor explain why this run avoided overflow. No reliable total
observer-overhead estimate follows from these four shared-host sessions.

All four cleanup checks passed, with KVM confirmed by QMP, owned QEMU reaped and
the original KVM ACL restored. Sixteen post-case diagnostic commands and all 32
output/error stream sizes and hashes were checked without timeout or truncation.
The functional gates remained separate and passed. No production timing, queue,
priority or loss-handling change was made from this observation.

## Full-stage repeat with consumer credits

[Run 41662440249](https://github.com/AlexGarciaMartin/MadSense/actions/runs/41662440249)
at `6a953b8f4bee35a727c845e80e93e49cd45ffa31` retained original runtime ZIP SHA-256
`82d05e2e9774e4fa6a155a4b6d0675730432efb6a6112bc175b58eac65800e75`.
The exact-source audit verified 115 payload sources, four installed files, all
five overlay changes and the Git archive. All eight sessions completed without
input overflow, setup discards, transport errno or unattempted samples. Functional
gates, actor cleanup, owned QEMU shutdown and restoration of KVM ACLs passed.

| Session, in execution order | Observed / written | Producer deadlines skipped | Missing after raw-slot publication |
|---|---|---|---|
| 100 Hz control 1 | 1000 / 1000 | 0 | No internal trace |
| 100 Hz trace 1 | 999 / 1000 | 0 | 1 |
| 100 Hz trace 2 | 1000 / 1000 | 0 | 0 |
| 100 Hz control 2 | 1000 / 1000 | 0 | No internal trace |
| 1000 Hz trace 1 | 9850 / 9988 | 12 | 138 |
| 1000 Hz control 1 | 9809 / 9977 | 23 | No internal trace |
| 1000 Hz control 2 | 9774 / 9985 | 15 | No internal trace |
| 1000 Hz trace 2 | 9808 / 9972 | 28 | 164 |

An independent CSV audit, without importing the result verifier, reproduced the
written/observed sets and percentile calculations. In every instrumented session,
each written sequence reached parsing and raw-slot publication exactly once. The
unique interrupt-payload and completed-send sets equal the virtual reader's
observed set. Thus the missing sequences in these captures are confined to the
raw-slot-to-interrupt interval; none is attributed to a malformed Bluetooth
report or a missing client dequeue. The trace has no overflow or invalid sequence,
all writers finished before dumping, and its native clock was bracket-checked.
This establishes the boundary, not whether a pending URB, notification worker,
timer or scheduler delay caused a particular overwrite. No observer-cost estimate
or physical latency conclusion follows. The lost 100 Hz sequence is retained;
prior successful 100 Hz runs do not erase it. The result remains `transport-limit`.
