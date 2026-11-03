# Production core integration over synthetic UHID

This is a separate second stage after the installed broker baseline. It has not
yet run natively. Build and fixture checks on Windows do not validate the Linux
device, permission, USB/IP or audio paths.

The ordinary-user C# executable uses the real `HidDeviceEnumerator`,
`LinuxBrokerClient`, `DualSenseDevice`, `ControllerTracker`, `EmulationService`,
`VirtualControllerFactory`, USB/IP server and Bluetooth writers. A factory
decorator records data without replacing the persona or adapter. No fake broker,
test authorization branch or replacement virtual implementation is used.

The experiment constructs its own settings and lifecycle. It does **not** execute
the desktop application's `SettingsFor`, `Connect`, tray, UI or retry state machine.
Those remain a separate integration target. It also cannot validate radio,
firmware scheduling, physical speaker/actuator latency or real-game support.

`production_fixture.py` creates a generic HID gamepad on the Bluetooth bus via
UHID, with complete 78-byte input and 78/142/334/398-byte output reports. It answers
firmware, pairing and calibration features with explicit synthetic data. The real
physical constructor reads firmware/pairing; calibration is checked via broker
RPC separately. The virtual persona exposes its production calibration feature;
the kernel observer compares those bytes through the virtual hidraw interface.

The owned QEMU root runner temporarily unloads hid-playstation to create this
generic fixture, then loads it again before creating the virtual controller. It
uses a dedicated ordinary test account and an exact fixture udev rule. The rule
and polkit grant exist only in the disposable test VM. Before changing anything,
the runner verifies the QEMU UUID/ownership marker and rejects existing Sony
Bluetooth devices, broker leases and USB/IP imports. It never selects a physical
device or detaches a numeric VHCI port itself.

Planned assertions in the executable runner:

- Real enumeration, isolation, feature RPC and production parser construction.
- Kernel virtual HID descriptor/features and neutral input.
- Kernel RGB output reaches the production Bluetooth writer with valid CRC.
- A nonneutral sticks/triggers/IMU marker survives the complete input path.
- Four-channel ALSA PCM is exact at the production persona ingress; its rear
  channels are exact at haptics ingress; a nonzero haptics report reaches UHID.
- Core disposal removes the virtual device, clears the journal and restores all
  synthetic physical permissions. Client input overflow fails the experiment.

The current audio assertion checks ingress exactly and outgoing framing/CRC plus
nonzero haptics. It does not yet compare resampled haptics or decoded Opus against
the source waveform. `consumer-ready.json` contains the UUID, attributed VHCI USB
parent, local bus id, exact virtual hidraw/evdev paths and numeric VID/PID for the
separate SDL/FF consumer experiments. Those consumers must use a separate ordinary
account with access only to the virtual nodes.

Software timestamps and proxy percentiles include instrumentation and TCG
scheduling. Input is injected at 100 Hz; this is not a throughput benchmark.
Binary capture is bounded to 16 MiB per stream and overflow invalidates completion.
Initialization report replacement counts remain in the final proxy metrics.

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

The platform workflow publishes this actor and invokes `qemu_core.py`. No native
pass is implied here. The launcher reuses the pinned-image `OwnedQemu` lifecycle, runs the existing
broker baseline first in the same guest, and starts this stage only after that
command succeeds. Both result documents are validated before a pass is recorded.
It accepts `--neutral-probe-directory`, `--core-probe-directory`, `--output` and
`--work`; it must run on a disposable GitHub-hosted Linux worker.
Between stages, `repeat_install_guest.py` verifies two idle installer restarts:
the service PID must change, FDSTORE must be empty, and preflight must explicitly
confirm no active lease or pending recovery. The baseline teardown stops the
service, so the stage starts it again before taking the first PID snapshot.
