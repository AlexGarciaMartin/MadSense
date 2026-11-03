# Native broker experiment

This is a reproducible test harness, **not yet a passing native result**. It is
separate from the portable parser/client tests. Run it only inside the disposable
QEMU guest created by `linux/research/qemu_kernel.py`; its explicit owner UUID,
root-created marker and DMI identity must agree. The host and physical machines
are rejected before fixture installation.

`.github/workflows/linux-platform.yml` runs the portable suites on x64 and ARM64,
then runs the native harness inside an x64 QEMU guest. `qemu_broker.py` uses the
reusable `owned_qemu.py` lifecycle and imports the exact image pins and scope check
from the kernel experiment. Both launchers verify the official signed manifest
and pinned image digest, seed a known SSH host key, bind SSH to loopback and
provide no physical-device passthrough. A slow SSH attempt may time out and retry
within the overall boot deadline. Only the launcher-owned process handle is
terminated during cleanup.

The workflow's host command is:

```sh
python3 linux/platform/tests/qemu_broker.py \
  --probe-directory dist/linux-platform-probe \
  --output dist/linux-platform-guest --work dist/platform-qemu-private
```

`--output` and `--work` must be fresh, separate directories, with neither nested
inside the other. Private keys, cloud-init seed and disk images remain in work.
The uploaded evidence contains image provenance, source revision, exact payload
hashes verified again in the guest, setup logs, native case results and service
logs. A successful result requires all ten native assertions, at least one
retained-grab observation after SIGKILL and successful owned-guest shutdown.
Preparation failures and partial native results are collected too. The guest
setup loads modules only inside the VM; the hosted runner's kernel is unchanged.

After copying the `linux/platform` and `linux/research` trees, a self-contained
neutral exporter and the independent observer into that guest, run:

```sh
bash linux/platform/tests/run_native_broker_guest.sh \
  OWNER_UUID /absolute/path/MadSense.Linux.Probe \
  /usr/lib/linux-tools/KERNEL/usbip /absolute/fresh/broker-evidence
```

The runner installs the unchanged production broker service through its ordinary
installer. The Python harness creates two **UHID fixtures** with Bluetooth bus
number 5 and Sony 054c:0ce6 identity; this does not create a radio or emulate actual
Bluetooth transport. A small generic gamepad descriptor supplies an evdev child
and a 78-byte output report. A temporary VM-only module rule keeps
`hid_playstation` from applying real-controller firmware negotiation to this
minimal fixture. The installed production helper receives no test exception.
A separate VM-only udev rule gives only the two exact fixture parents a known
root/group/mode policy and removes seat tags. Its ordinary assignments precede
the production helper's final root assignments. This makes post-release current
udev policy deterministic without depending on an interactive logind session or
expecting manually added seat ACLs to survive a policy refresh.

Two newly created ordinary test users exercise real polkit. A temporary test rule
authorizes only `madsense-ci` for the existing Acquire action. Root applications
and `madsense-ci-denied` must be rejected. The rule and module policy are removed
only if their contents still match the files created by this run; fixture devices
are destroyed and test users removed. None of these rules belong in a package.

Required assertions are:

- Existing foreign hidraw reader refusal and permission/ACL rollback.
- Every evdev child grabbed; a previously open reader cannot receive new input.
- Node ACL/mode isolation denies new ordinary-user opens, while the second
  controller remains readable and keeps its permissions.
- Raw fixture bytes arrive through the broker input RPC.
- Normal release, owner socket EOF, owner process death, privileged mode changes
  and named-ACL fault injection end the lease and restore the fixture's current
  udev policy and node state.
- A real loopback USB/IP import is stored with systemd. A second independent
  import remains present. The requesting actor itself owns its forwarding
  listener, satisfying the production ownership check without bypasses.
- After SIGKILL of the real service PID, there must be a recorded interval where
  the process is gone, the owned import still exists and every evdev grab remains
  held by the descriptor store. Restart must shut down the owned import, send
  the independently generated neutral-report fixtures, release the stored grabs
  and restore ACLs; the second controller/import must remain unchanged.
- A destroyed fixture followed by a new HID device is never silently reacquired.

Evidence includes native result cases, node/ACL snapshots, the pre-crash journal,
exact UHID output bytes, retained-grab observations, systemd properties/journal,
exporter logs and final VHCI status. A missing capability or incomplete recovery
fails the run. Cleanup never guesses a different port or overwrites changed
production recovery evidence.

The harness has been syntax-checked locally, and launcher guards have portable
tests. Until it is run in CI, systemd
FDSTORE behavior, actual permission restoration, UHID setup, polkit acceptance,
pidfd cleanup and SIGKILL ordering remain unverified. This experiment does not
prove real Bluetooth ownership, firmware reset acceptance, effects on a physical
controller, game compatibility or latency. It does not exercise C# bridge/UI
behavior; those require separate integration coverage.

Primary contracts: [UHID ABI](https://docs.kernel.org/hid/uhid.html),
[packed event structures](https://github.com/torvalds/linux/blob/6628e7d4c6b25c8d18f1345e3a20d21829aae7e4/include/uapi/linux/uhid.h),
[systemd descriptor storage](https://www.freedesktop.org/software/systemd/man/latest/sd_notify.html).
