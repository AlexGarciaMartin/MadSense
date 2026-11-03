# Temporary KVM access in disposable hosted CI

Both QEMU launchers use `kvm_acceleration.py`. This applies only to ordinary
users on explicitly marked disposable GitHub-hosted Linux runners. It is not an
installer feature or a general machine setup script.

An existing root-owned `/dev/kvm` character node must match its registered sysfs
and `/proc/misc` identity. If the runner already obtains API version 12, no ACL
changes occur. If opening it fails with EACCES, a narrowly scoped root invocation
checks API 12 and accepts only a base owner/group/other ACL. It adds read/write
for the invoking `SUDO_UID`, with an explicit mask preserving every original
principal's effective rights. Extended ACLs are rejected. No chmod, group changes,
udev rules, module loading or mitigation changes are performed.

Before mutation, the helper saves identity, mode and ACL under a unique root-owned
0700 directory in `/run`, with a 0600 state file. ACL operations use the exact
opened device through `/proc/self/fd`. Restoration refuses a changed node or an
unexpected third-party ACL. One observed hosted-runner case is accepted: the named
runner entry has already disappeared, leaving only the original three entries
plus a mask exactly equal to the original group permissions. The node identity
and mode must also match the original. This has no named principals and already
has precisely the original effective permissions, so the helper removes the
redundant mask without re-granting the runner. Changed masks, additional named
entries, base permissions, mode or identity are still rejected. It verifies the original owner, group, identity, mode
and full ACL before removing that state. QEMU remains an ordinary user process.

API 12 is only a candidate signal. QEMU starts paused with a private QMP socket;
`query-kvm` must report `enabled: true` before the launcher records KVM and sends
`cont`. Failed KVM initialization is collected, that exact child is stopped and
reaped, and its ACL is restored before a fresh TCG attempt starts. Ordinary
shutdown uses the same ordering. An unconfirmed reap or restoration fails the
experiment rather than claiming cleanup or continuing with TCG.

`hostKernelModified` remains false. Separate `kvmAcceleration` evidence records
the initial API result, optional ACL text/hashes/identity, QMP confirmation, fallback
reason and verified restoration. Per-attempt arguments/stderr are retained as
`qemu-kvm-*`/`qemu-kvm.stderr` and `qemu-tcg-*`/`qemu-tcg.stderr`; the successful
arguments also retain the existing `qemu-arguments.json` name. QMP startup and SSH
readiness timings are software measurements, not controller latency.

Root-operation JSON is retained even on a nonzero exit. A restore mismatch records
the original, granted and observed ACL, opened/path identities and modes before
refusing mutation. This distinguishes a real external change from an ACL text
difference. The narrow redundant-mask case records its observed ACL and
`restoreReason: original-with-redundant-mask`; other unexpected ACLs are not
normalized or overwritten. The first diagnostic run confirmed this exact state
on the same inode/mode after QMP had confirmed KVM:
[hosted smoke 41610372556](https://github.com/AlexGarciaMartin/MadSense/actions/runs/41610372556).
It did not establish which external component removed the named entry.

`python3 linux/research/kvm_smoke.py --output dist/kvm-smoke` isolates this lifecycle
without downloading or booting a guest image. It creates an empty owned QEMU,
records QMP plus ACL/udev snapshots for ten seconds, then reaps QEMU and restores.
The helper still requires the ordinary disposable hosted runner and its existing
registered KVM node. A TCG fallback is `not-applicable`, never proof of usable KVM.

This cannot make a hard-killed launcher execute its cleanup; the root recovery
record remains on that job's ephemeral VM. It does not install persistent rules
or grant any UID other than that runner. No usable KVM result or speedup is claimed
until a native run reports QMP confirmation and successful restoration.

Relevant primary contracts: [KVM API](https://docs.kernel.org/virt/kvm/api.html),
[QMP query-kvm](https://www.qemu.org/docs/master/interop/qemu-qmp-ref.html),
[ACL mask/restoration](https://man7.org/linux/man-pages/man1/setfacl.1.html), and
[GitHub's experimental nested-virtualization support](https://docs.github.com/en/actions/concepts/runners/github-hosted-runners).
