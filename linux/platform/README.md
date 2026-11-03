# Linux privilege broker

This directory contains the Linux platform boundary. The desktop application and
shared bridge engine remain unprivileged. The Python broker runs as a root-owned
systemd service and exposes one authenticated lease over
`/run/madsense/broker.sock`. This implementation still requires Linux integration
and physical Bluetooth validation; passing portable tests does not establish
shipping readiness or physical latency.

The service requires Linux pidfds, Python 3.10 or newer, systemd 254 or newer,
polkit, udev, the `getfacl`/`setfacl` utilities, and the `vhci_hcd` kernel module.
An administrator explicitly installs it with `sudo sh linux/platform/install.sh`.
The installer installs fixed files owned by root, loads the one named module and
starts the service. It does not grant a user or group general hidraw access.
Run the application normally after installation. A missing broker, missing
dependencies or rejected authorization leaves physical bridging unavailable.

An explicit installation enables and starts the unit as separate checked steps,
then requires it to be active. After confirming that the previous service has no
lease or stored recovery descriptors and installing the new files, it resets
only this unit's previous failure/start-limit counters. The runtime restart limit
is unchanged; this is a single administrator-requested installation attempt.

The socket accepts newline-delimited JSON objects of at most 16,384 bytes. Each
request has an integer `id` and an `op`; responses preserve the id and contain
either `ok:true,result:{...}` or `ok:false,error:{code,message}`. Supported
operations are `preflight`, `acquire`, `write`, `get_feature`, `send_feature`,
`attach`, `detach`, and `release`. HID payloads are base64 encoded and bounded to
4,096 bytes. Input reports arrive as `{"event":"input","data":"..."}` with an
optional `brokerReadMonotonicNs` timestamp captured after the broker's HID read;
isolation failure emits `{"event":"isolation_lost","reason":"..."}` and
ends that connection's lease. The service has no arbitrary command, path, ioctl,
driver-unbind, application-kill or remote-host operation.

`SO_PEERCRED`, a pidfd and the process start time bind the lease to the actual
socket caller. Acquisition invokes the explicit polkit action
`org.madsense.bridge.acquire` for that process identity. The supplied policy
requires administrator authorization in an active local session and denies
inactive or other sessions. Root applications are rejected. The socket's public
connect permission permits preflight and authorization requests, not physical
access. The broker returns input/report RPCs; it never grants the application a
device ACL or raw descriptor. At most two connections per UID and eight total are
accepted. Non-owner connections expire after ten seconds without incoming data;
the active lease is governed by its owner lifetime and isolation checks.

To remove a manually installed helper, pause the app and use
`sudo sh linux/platform/uninstall.sh` from the same source revision/package that
installed it. Removal refuses a pending lease/recovery, modified installed files
or symlinks; it stops the service and verifies an empty descriptor store before
removing its five fixed files. It retains user settings, logs, runtime recovery
state and loaded kernel modules. It never recursively deletes the helper folder.

While the authorization dialog is pending, the helper checks the exact requesting
socket and the caller's pidfd at intervals of at most 100 ms between child waits.
EOF, including a write-half close behind unread request bytes, cancels and reaps
only the helper's own `pkcheck` child. The helper rechecks the connection after
authorization and before recording isolation intent, so late authorization cannot
activate a disconnected attempt. It never signals the desktop, an authorization
agent or unrelated processes. The extended authorization watchdog and the
120-second total deadline still apply.

`preflight.available` reports platform support, including when another lease is
active. It is not a release acknowledgement. The additional `leaseActive` and
`recoveryPending` booleans report ownership and unresolved journal/rule state.
Only explicit `false` values for both fields on a fresh connection confirm an
idle helper after transport loss. A successful `release` response is sent after
ordered cleanup finishes. The desktop remains in recovery and blocks acquisition
until it receives that acknowledgement or the fresh idle status; EOF, an offline
helper, and older helpers without these fields cannot establish release. Status
polls never request authorization. Cancelling initial firmware/pairing/calibration
requests closes only that attempt's broker connection, and follows the same
confirmation requirement.

Acquisition accepts only `/dev/hidrawN` whose opened-device identity and validated
HID ancestor identify a Bluetooth Sony DualSense or DualSense Edge. It inventories
every hidraw and input node beneath that HID ancestor, records each node's boot,
filesystem/device/inode identity, owner, mode and ACL, and writes the recovery
journal before changing permissions. A temporary rule under
`/run/udev/rules.d/72-madsense-lease.rules` matches the exact validated HID kernel
parent. The complete rule is fsynced in an ignored temporary file, then installed
atomically without replacing an existing rule. It applies final root ownership and mode 0600 and removes `uaccess` and
`seat` tags between the standard seat rules and their ACL application. Existing
ACLs are removed, every evdev child is grabbed, and the helper audits all other
processes for open descriptors to any corresponding hidraw or force-feedback
evdev interface, and writable descriptors to children with LED or sound output.
The latter matters because `EVIOCGRAB` does not exclude another already-open evdev
writer. Event capabilities come from each opened node's `EVIOCGBIT(0)` result;
access modes come from bounded `/proc/PID/fdinfo/FD` reads with node identity
rechecked. Read-only input/sensor/touchpad descriptors without force feedback are
allowed. An existing foreign hidraw or force-feedback reader, other output-capable
evdev writer, unavailable descriptor metadata or inaccessible live
process causes acquisition to fail
and roll back. The user can close the application holding the controller and retry;
the broker never closes another application's descriptor or unbinds a driver.

During a lease, node identities, mode/ownership, raw POSIX ACL xattrs, the runtime
rule, evdev disconnection and owner lifetime are checked repeatedly. Full process
descriptor audits run off the report loop; a failed audit or an audit that has not
completed in two seconds ends isolation. The initial audit has the same two-second
deadline and completes before
acquisition succeeds. A disconnected device, new child node, permission change,
foreign reader or output writer, owner exit or stalled client permanently ends that lease. The
service does not silently reacquire a reconnected controller.

These checks cover ordinary applications, including evdev users and hidraw users.
They are not a security boundary against root, kernel code, or cooperating
processes passing previously open descriptors between one another during a
process-table scan. Mode and ACL changes do not revoke an existing descriptor.
`EVIOCGRAB` controls input routing and does not seize hidraw or revoke other evdev
writers. This follows from Linux's
[`evdev_write`](https://github.com/torvalds/linux/blob/6628e7d4c6b25c8d18f1345e3a20d21829aae7e4/drivers/input/evdev.c)
and [`input_inject_event`](https://github.com/torvalds/linux/blob/6628e7d4c6b25c8d18f1345e3a20d21829aae7e4/drivers/input/input.c):
evdev clients share the input handle used for the grab check. Read-only descriptors
can own force-feedback effects played through a separate writable descriptor.
Closing that writer does not necessarily cancel the reader's effects; a delayed
effect may still play and `EVIOCSFF` can change it. This follows from
[`input_ff_event`, effect ownership and flush](https://github.com/torvalds/linux/blob/v6.8/drivers/input/ff-core.c)
and [`ml_ff_upload`/`ml_ff_playback`](https://github.com/torvalds/linux/blob/v6.8/drivers/input/ff-memless.c).
Therefore every foreign descriptor to a force-feedback node blocks acquisition.
Recent kernels offer
`HIDIOCREVOKE`, but it acts on the calling open-file description rather than every
reader of a device; the broker does not pretend it supplies a device-wide revoke.
Already delivered or queued input cannot be taken back. A monitor also cannot
undo access obtained before it detected an external privileged permission change.
These limitations are why acquisition fails when an existing reader is found.

USB/IP attach is limited to the lease owner's IPv4 loopback listener, an
unprivileged port, a numeric bus id of the form `1-1`, and supported persona
identities. The helper performs the import handshake and gives the retained TCP
socket to a free kernel VHCI port. Kernel allocation decides races with other
importers. Connection, handshake, descriptor-store acknowledgement and enumeration
yield to the main loop, which continues HID reads, permission checks and owner
monitoring. The handshake has a five-second total deadline, including partial
replies. Owner loss cancels a pending import before physical cleanup.

Administrator authorization trusts the requesting application as a USB device
emulator. The broker checks the advertised import VID/PID, but does not authenticate
the application binary or validate the subsequent USB descriptor stream; the
advertised identity is not a security boundary against an authorized malicious
emulator. Detach calls `shutdown(SHUT_RDWR)` on that exact retained socket and
waits for its virtual device to disappear. It never detaches a numeric port from
a later status snapshot, which could target a reused port.

The journal records the expected USB sysfs path before the kernel attach write.
After enumeration it also records that node's filesystem/inode identity. Recovery
therefore waits for node disappearance even if a crash happened before enumeration
completed; a NULL port status alone does not prove removal. Before an inode was
recorded, reuse of that exact path by another importer can conservatively prevent
cleanup. The broker leaves the other importer untouched and retains recovery state.

Before acquisition becomes active, every evdev grab's open-file description is
stored with systemd using `FDSTORE`, `FDPOLL=0`, unique names and a synchronization
barrier. The import socket is stored before attachment. The store has 33 slots:
at most 32 grabbed descriptors and one import. PID 1 retains these descriptors
across helper SIGKILL, so closing the helper's descriptor alone does not release
an active grab. Recovery explicitly releases each grab only after owned transport
shutdown, disappearance checks and the physical reset attempt. The selected
hidraw descriptor is not stored; crash recovery reopens only a matching saved
node and rechecks its Bluetooth HID identity.

After the store barrier, ongoing descriptor audits exempt only PID 1's read-only
descriptors matching the stored grabs' exact device/inode/rdev identities and
the recorded PID 1 process start. The initial audit has no such exception. This
relies on systemd/PID 1 within the stated root-trusted boundary; it is not a claim
to distinguish malicious root descriptor substitution. Other processes and
writable PID 1 descriptors remain conflicts.

The root-only journal and descriptor store survive service failure and an
explicit stop until cleanup succeeds. Normal release shuts down the exact owned
USB/IP socket first. The restarted main service receives stored descriptors and
performs the same ordered cleanup. Missing descriptors for an active lease,
changed descriptor identities or failed teardown preserve the journal and prevent
new leases. An incomplete acquisition before activation may roll back without a
complete descriptor store, because no virtual import exists yet. `ExecStopPost`
can restore an acquisition with no stored grabs or pending import; it does not
claim to recover descriptors it did not inherit. `NotifyAccess=main` reserves
descriptor-store removal for the main service. If an explicit stop encounters a
cleanup error, run `sudo systemctl restart madsense-broker.service` to invoke
descriptor recovery, and inspect `journalctl -u madsense-broker.service` if it
fails. Do not manually remove its journal, rule or descriptor store.

Restoration only modifies physical nodes whose saved boot/device/inode identity
still matches. Missing or replaced nodes are skipped. It restores the saved base
owner, mode and ACL, removes only its unchanged runtime rule, then triggers
current udev policy on the same verified HID subtree. Recovery reloads udev rules
even if the rule file was already removed before a crash, so udev cannot reuse a
cached isolation rule. Current login-session ACLs
may therefore differ from the original snapshot. A restoration failure retains
the journal for recovery.

The neutral reset sends three zero-only Bluetooth reports: legacy rumble zero,
V2 rumble zero, then trigger effects off. All 48 sequence-tagged encodings match
independently generated shared-protocol fixtures. A separate fixed helper child
limits this attempt to 1.5 seconds; failure keeps isolation pending recovery.
Normal release continues the last observed output sequence. Crash recovery uses
the sequence saved at attachment, which may be older. These reports do not add
undocumented audio-mute commands, prove actuator response or prove audio-buffer
drain. Physical hardware validation is still required.

A 15-second systemd watchdog observes progress in the main service loop. Only
the pre-isolation polkit prompt extends it to 130 seconds for its 120-second
authorization timeout. Cleanup failures terminate the service for restart instead
of leaving an apparently available broker. A stalled ioctl stops heartbeats and
triggers recovery; uninterruptible kernel I/O can delay process termination, so
this is not an unconditional bound on physical recovery time.

Both helper and client input queues hold at most eight reports. The helper and
the active client reader end the lease on a ninth pending report. Before the
client's first nonempty `Read`/`ReadAsync`, the shared controller constructor must
read firmware and pairing features. During this setup phase only, the client
retains the latest eight input reports and counts every superseded report in
`InitializationDiscardedReports`. Once reading begins, that behavior cannot
resume; an active queue overflow always ends isolation. Every client discard is counted.
The helper counts a report until its complete JSON frame has been passed to the
socket and requests a 4,096-byte socket send buffer. Kernel socket and hidraw
buffers are separate and can still hold earlier input. This limit is not an
end-to-end latency measurement.

At the end of helper acquisition, after the durable journal and FDSTORE barrier
and before ACTIVE or the acquire reply, the helper drains the nonblocking hidraw
descriptor to `EAGAIN`. This discards history accumulated while permissions,
udev and process audits were being established. It reads at most 128 reports and
must observe `EAGAIN` within 50 ms; reaching either bound, EOF or a device error
fails acquisition and uses the normal ordered cleanup. The report bound exceeds
the 64-slot hidraw queue of the tested Linux 6.8 kernel, leaving room for arrivals
during the drain; it does not relax either active eight-report queue. The acquire
reply and structured `lease-acquired` journal event expose `setupReportsDrained`.
A failed drain logs the observed count too. This is the number actually read and
discarded from that descriptor, not total setup input: hidraw can silently drop
reports when its per-open queue is full before the drain starts. No total kernel
loss count or lossless initialization is claimed; see the kernel's
[per-open hidraw queue](https://github.com/torvalds/linux/blob/v6.8/drivers/hid/hidraw.c).
Native evidence of this change
is still pending; the portable checks cover backlog, deadlines, EOF and failure
before ACTIVE, with a real nonblocking packet descriptor check on Linux.

The IPC reader has its own blocking thread. Synchronous HID and legacy USB/IP
attachment calls wait directly on the reply completed by that reader; synchronous
input reads use a producer signal instead of blocking on an async continuation.
This prevents those callers from occupying the same ThreadPool workers needed
to drain their socket or complete their own operation. Async acquisition and
preflight remain async. Request deadlines, cancellation and ordered lease cleanup
are retained, without changing ThreadPool limits or the eight-report queues.
The isolated [starvation probe](tests/starvation/README.md) verifies all six
synchronous operations with a single occupied worker and with an ordinary pool.
Its two-second completion bound is a liveness assertion, not a latency result.

`session.Metrics` exposes `ReceivedReports`, `ReadReports`,
`InitializationDiscardedReports`, `InputOverflows`,
`QueuedReports`, `OldestQueuedAgeMs`, and client `Stopwatch` timestamps/frequency.
`ClientQueueAge` measures client message arrival to `IHidDevice.Read`, including
reports buffered during acquisition. `HidRpcRoundTrip` covers write/get-feature/
send-feature from waiting for the request slot through response receipt.
Distributions report p50/p95/p99/max over the latest 1,024 samples with total
`Count`. `LastBrokerReadMonotonicNs` preserves the broker timestamp as a separate
raw value; the client does not subtract clocks. These metrics omit kernel hidraw
and socket queues before receipt, radio, firmware and actuator latency.

Portable checks run with:

```sh
python3 -m unittest discover -s linux/platform/tests -v
dotnet test linux/platform/tests/MadSense.Linux.Platform.Tests.csproj
```

The client presents transport failures as the shared SDK's `HidException` so its
feature fallback and output-disconnect paths remain effective. Acquisition
authorization denial is a separate `UnauthorizedAccessException`; callers must
wait for a new explicit user action before retrying authorization. Output failure
ends readiness. Optional feature-read failure remains visible to the SDK while
preserving an otherwise valid lease.

Native integration must additionally prove acquisition rollback, every evdev
grab, existing hidraw-reader refusal, permission-change loss, reconnect refusal,
EOF/process-exit cleanup, systemd SIGKILL recovery with an attached import, and
preservation of another controller and another importer. Physical tests must
verify Bluetooth report forwarding and effect reset before normal access is
restored. Native fault tests must prove stored grabs survive a killed main
process until owned import shutdown and that failed reset retains isolation.

Primary contracts: [kernel hidraw implementation](https://raw.githubusercontent.com/torvalds/linux/master/drivers/hid/hidraw.c),
[input grab semantics](https://docs.kernel.org/driver-api/input.html),
[udev rule ordering](https://www.freedesktop.org/software/systemd/man/latest/udev.html),
[polkit process subjects](https://www.freedesktop.org/software/polkit/docs/latest/eggdbus-interface-org.freedesktop.PolicyKit1.Authority.html),
[kernel VHCI attach/detach](https://raw.githubusercontent.com/torvalds/linux/master/drivers/usb/usbip/vhci_sysfs.c),
and [systemd descriptor storage](https://www.freedesktop.org/software/systemd/man/latest/sd_notify.html).
