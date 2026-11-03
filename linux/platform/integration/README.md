# Production core integration over synthetic UHID

This is a separate second stage after the installed broker baseline. In run
41604139844 at `1dd841f`, the baseline and two idle upgrades passed, but this stage
stopped during fixture setup before starting C#. Production core behavior over
UHID is therefore still unverified. Build and fixture checks on Windows do not
validate the Linux device, permission, USB/IP or audio paths.

The idle installer stage now also exercises explicit helper removal: it must
refuse a modified installed file before stopping the service, preserve an unknown
file during normal removal, tolerate repeated removal, and reinstall with an
explicit idle preflight. These four assertions have no native pass yet. The
launcher requires them separately from the two already-tested idle upgrades.

The ordinary-user C# executable uses the real `HidDeviceEnumerator`,
`LinuxBrokerClient`, `DualSenseDevice`, `ControllerTracker`, `EmulationService`,
`VirtualControllerFactory`, USB/IP server and Bluetooth writers. A factory
decorator records data without replacing the persona or adapter. No fake broker,
test authorization branch or replacement virtual implementation is used.

The experiment constructs its own settings and lifecycle. It does **not** execute
the desktop application's `SettingsFor`, `Connect`, tray, UI or retry state machine.
The following [application-controller stage](../../tests/app-integration/README.md)
executes those application methods separately. Neither stage validates radio,
firmware scheduling, physical speaker/actuator latency or real-game support.

`production_fixture.py` creates a generic HID gamepad on the Bluetooth bus via
UHID, with complete 78-byte input and 78/142/334/398-byte output reports. It answers
firmware, pairing and calibration features with explicit synthetic data. The real
physical constructor reads firmware/pairing; calibration is checked via broker
RPC separately. The virtual persona exposes its production calibration feature;
the kernel observer compares those bytes through the virtual hidraw interface.

The owned QEMU root runner loads hid-playstation before creating this fixture.
Firmware, pairing and calibration features carry the Bluetooth A3 CRC required
by [Linux's ps_get_report](https://github.com/torvalds/linux/blob/v6.8/drivers/hid/hid-playstation.c).
The fixture waits for the real driver's gamepad, motion and touchpad evdev nodes.
It uses a dedicated ordinary test account and an exact fixture udev rule. The rule
and polkit grant exist only in the disposable test VM. Before changing anything,
the runner verifies the QEMU UUID/ownership marker and rejects existing Sony
Bluetooth devices, broker leases and USB/IP imports. It never selects a physical
device or detaches a numeric VHCI port itself.

Planned assertions in the executable runner:

- A pre-existing writable evdev descriptor with FF/LED/SND capabilities rejects
  acquisition and restores the fixture. A separate read-only FF descriptor must
  also be rejected because it can own uploaded effects. Read-only sensor/touchpad
  observers remain open during successful core acquisition and verify their grabs
  and release. All three interfaces must have exact identities in the completed
  descriptor-store journal and matching read-only descriptors retained by PID 1;
  the observer does not open the FF node during the lease.
- Real enumeration, isolation, feature RPC and production parser construction.
- Kernel virtual HID descriptor/features and neutral input.
- Kernel RGB output reaches the production Bluetooth writer with valid CRC.
- A nonneutral sticks/triggers/IMU marker survives the complete input path.
- Four-channel ALSA PCM is exact at the production persona ingress; its rear
  channels are exact at haptics ingress; a nonzero haptics report reaches UHID.
- Core disposal removes the virtual device, clears the journal and restores all
  synthetic physical permissions. Its final three output reports must match the
  independently checked neutral fixtures. Client input overflow fails the experiment.

The current audio assertion checks ingress exactly and outgoing framing/CRC plus
nonzero haptics. It does not yet compare resampled haptics or decoded Opus against
the source waveform. `consumer-ready.json` contains the UUID, attributed VHCI USB
parent, local bus id, exact virtual hidraw/evdev paths and numeric VID/PID for the
separate SDL/FF consumer experiments. Those consumers must use a separate ordinary
account with access only to the virtual nodes.

The optional `run_core_guest.py --consumer-binaries DIR [--wine-consumer EXE]`
invocation runs the separate consumer suite after the functional audio assertions
while the same fixture and attributed virtual device are alive. It extends only
that actor invocation to 600 seconds, switches the fixture between south-button
and neutral states on request, and requires consumer cleanup before stopping the
core. `consumer-ready.json` also provides exact virtual features/MAC and the
captured output paths. The ordinary platform workflow does not supply these flags.
Its package therefore need not contain consumer modules or binaries.

Software timestamps and proxy percentiles include instrumentation and TCG
scheduling. Input is injected at 100 Hz; this is not a throughput benchmark.
Binary capture is bounded to 16 MiB per stream and overflow invalidates completion.
Initialization report replacement counts remain in the final proxy metrics.

The first attempt assumed a generic UHID binding would survive loading the Sony
driver. The native kernel rebound it, requested feature09, and rejected its
missing CRC with `-EILSEQ`. The fixture now supports the production driver instead
of suppressing it. In run 41610372588 at `6d54ee8`, the driver successfully requested
09/20/05 and emitted its initialization outputs, but fixture discovery timed out
before starting the C# actor: hid-playstation replaces `HID_UNIQ` with the pairing
MAC. Discovery now matches the exact UHID name/physical path plus its expected
MAC, requires the virtual UHID parent and actual character nodes, and the app
actor uses the same stable identity. Run 41613298915 at `884f0fd` discovered the real Sony
fixture and passed both pre-existing evdev writer and read-only FF refusal cases,
then failed to execute the C# actor with `EACCES` from its `/run` staging path.
The artifact did not preserve staging modes/mount flags, so it cannot distinguish
the exact permission cause. The broker baseline, both idle upgrades and all four
removal/reinstall cases passed in that run.

Core and application actors now use a fresh root-owned UUID directory under
`/opt`, normalize publish directories/data/apphost modes to 755/644/755, and
reject symlinks or special files in the publish tree. Before creating a fixture,
the harness records staging identity, `/proc/self/mountinfo`, filesystem flags
for the selected directory and `/run`, then executes a copied system `true`
binary under the actor UID with no supplementary groups. This catches an
execution-permission failure before controller work without changing any mount
options. The directory is removed only if this invocation created it. The new
staging path passed in run 41615446457 at `477687d`: the ordinary C# actor ran,
and mount evidence confirms `/run` is `noexec` in the pinned guest. Core still
failed before Ready. That run ended its session during the first calibration RPC
with no client input overflow; the separate consumer run 41615446558 parsed
firmware, pairing and neutral input, then counted one active input overflow.
Neither result establishes a successful attached core or application actor.

The next diagnostic preserves the first session-loss reason in C# and the
service journal, including a bounded helper-side reason when its client cannot
consume an event. Both core and app harnesses collect the final broker journal
and unit properties on success or failure. Parsed-input evidence now copies a
bounded typed snapshot on the reader callback and performs JSON/file writes on
a dedicated observer thread. Queue exhaustion or writer failure invalidates the
capture; all accepted snapshots are drained before its final result. The
portable fixture command verifies that a blocked observer cannot block the
producer, and that exhaustion and writer failure remain visible. Production
input queue bounds and failure behavior are unchanged.

Portable preparation:

```sh
python3 linux/platform/integration/production_fixture.py --write-manifest linux/platform/integration/fixtures.json
python3 -m unittest discover -s linux/platform/integration -p 'test_*.py'
dotnet run --project linux/platform/integration/MadSense.Linux.CoreProbe.csproj -c Release -- --verify-fixtures linux/platform/integration/fixtures.json
dotnet publish linux/platform/integration/MadSense.Linux.CoreProbe.csproj -c Release -r linux-x64 --self-contained true -o /tmp/core-probe
```

After the native broker baseline succeeds, inside the same marked disposable
guest with the current helper installed, HIDAPI/ALSA available and `host_probe.c`
built, run:

```sh
sudo python3 linux/platform/integration/run_core_guest.py --owned-guest-token UUID \
  --probe-directory /tmp/core-probe --host-probe /tmp/host_probe --output /tmp/core-evidence
```

The platform workflow publishes this actor and the application-controller actor,
then invokes `qemu_core.py`. No native pass is implied here. The launcher reuses
the pinned-image `OwnedQemu` lifecycle in one guest: broker baseline, two idle
upgrades, this core experiment, then the application-controller experiment. A
failed command prevents the next actor from starting. The application stage also
validates all nine core cases and both upgrades before starting its actor.
Separate result documents under `result/`, `result/idle-upgrade/`,
`result/core-experiment/` and `result/app-experiment/` preserve partial evidence;
all required cases and cleanup gates must pass before overall success is recorded.
The launcher accepts `--neutral-probe-directory`, `--core-probe-directory`,
`--app-probe-directory`, `--output` and `--work`; it must run on a disposable
GitHub-hosted Linux worker. KVM is used only after the owned helper verifies its
temporary permission change and QMP confirms acceleration, with restoration on
teardown; otherwise the recorded fallback is TCG.
Between stages, `repeat_install_guest.py` verifies two idle installer restarts:
the service PID must change, FDSTORE must be empty, and preflight must explicitly
confirm no active lease or pending recovery. The baseline teardown stops the
service, so the stage starts it again before taking the first PID snapshot.
