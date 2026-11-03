# Synthetic application input timing

This optional experiment measures an attributed synthetic UHID input travelling
through the real LinuxBridgeController, physical-session broker RPC, parser,
raw converter and USB/IP import to that lease's virtual hidraw reader. It reuses
the existing ordinary AppProbe executable; no production factory, endpoint,
authorization or scheduling policy is replaced. It does not run the GUI.

Native C compilation and all eight validation tests passed on Linux x64 and
arm64 at commit `477687d` in [run 41615446664](https://github.com/AlexGarciaMartin/MadSense/actions/runs/41615446664).
Both source-tagged artifacts were downloaded and their archive hashes verified.
Timing execution is still pending; this experiment has produced no latency
numbers yet.

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
KVM/TCG scheduling remain part of this synthetic workload. No clock calibration
or no-observer baseline has yet quantified their total perturbation.

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
unconfirmed cleanup fails this independent workflow job. Runtime invocation has
not yet occurred.

An existing live-VM owner can reuse `add_latency_payload(payload)` before its
single upload and `run_latency_stage(guest)` after its functional stages, without
booting another guest. The current functional launcher is unchanged. A completed
workflow's already stopped VM cannot be reused; the opt-in standalone launcher
necessarily runs its own gates once. It collects evidence only once, then stops
its own QEMU before restoring the temporary KVM ACL.
