# Separate Debian 13 consumer experiment

The consumer workflow has an opt-in `guest_image: debian13` selection which
requires `ge_umu: true`. Ubuntu remains the default and still uses its existing
GPG-verified image. Debian results are a separate distribution observation.
The pinned Debian image now boots in CI, passes the ordinary-user namespace
preflight and the ten native broker cases. Core consumers and GE remain pending;
the observed attempts and their precise stopping points are recorded below.

The exact image is
[`debian-13-generic-amd64-20261026-2643.qcow2`](https://cloud.debian.org/images/cloud/trixie/20261026-2643/debian-13-generic-amd64-20261026-2643.qcow2),
438894592 bytes. Its SHA-512 is
`5bd891c6e62d01639f52a5587b72a4607421e445732214be787dba6043cf9797a6ff90bed982aada6a6c7e619f1f956d961c4fad76757171abd607920aac098a`.
The retained official `SHA512SUMS` has SHA-256
`f9e3b6d25a73706755c83d34edf9ad52e990b543169d695a8fd95037b3f6155f`;
the matching image JSON has SHA-256
`53d4966f917a68aa71dfd5c546099620cce4b11751fd7565d0cfc72a521ec382`.
The adapter rechecks all three before inspecting or booting the image, rejects
external backing chains and uses only a private qcow2 overlay.

These cloud artifacts are **not GPG signed**. Debian's
[cloud-image publication policy](https://cloud.debian.org/images/cloud/)
specifies HTTPS and checksums for current images and distinguishes the signed
legacy OpenStack images. The adapter records `imageSignatureVerified=false`;
HTTPS authentication and pinned digests do not become an independent signature.
`generic` includes the normal driver set; `genericcloud` omits drivers and is not
used. The pinned metadata identifies Linux `6.12.113+deb13-amd64`, AppArmor
`4.1.0-1`, systemd `257.13-1~deb13u1` and Python `3.13.5`.

The dedicated Debian setup installs distribution `usbip`, `usbutils`,
`alsa-utils` and the native dependencies of the self-contained .NET actor,
including `libicu76`, then requires the running kernel's `vhci_hcd`, `hid_playstation`,
`snd_usb_audio`, `xpad` and `uhid` modules. It uses `/usr/sbin/usbip`, as listed in
the [Debian package](https://packages.debian.org/trixie/amd64/usbip/filelist).
It does not install another kernel or reboot the host. The existing synthetic
broker baseline must pass before the production core and consumers run.

Before that baseline, an ordinary guest UID must pass the same user/network
namespace operation used by GE, with its current UID mapped to itself. The preflight records
UID/GID, capabilities, seccomp, current AppArmor profile, relevant sysctls, the
exact error and recent kernel journal. It checks the UID/GID stay unchanged and
the private network has no route. Refusal stops before any core actor or GE
launcher. No sysctl, profile, permissions or sandbox flags are changed.
Debian documents user namespaces enabled by default in recent kernels, but that
is a reason to test this image, not a substitute for the actual preflight.
After that check, the existing broker baseline still creates and restores its
explicitly labelled synthetic-device udev/polkit test fixtures. Those scoped
fixtures do not change the namespace policy or authorize the GE runtime.

The VM still has two virtual CPUs, 2 GiB RAM, bounded KVM/TCG startup, ephemeral
SSH keys, loopback-only SSH forwarding, no host devices/radios, and mandatory
owned QMP shutdown and KVM ACL restoration. Its `ubuntu` account name exists only
to preserve the existing harness path convention; provenance identifies Debian.
Image disks, private keys, GE archives and prefix caches never enter artifacts.

Debian's Wine 10.0 is recorded separately from Ubuntu's Wine 9.0. The explicit
fresh-prefix `Enable SDL=0` experiment is restricted to these two audited release
families, with registry readback before and after owned-wineserver restart.
Requested, applied and backend-observed status remain distinct. Debian's
WirePlumber 0.5 is outside the existing 0.4 observer contract, so the workflow
omits those optional PipeWire default/gain experiments for this image; it does
not claim to validate its desktop defaults. The private Pulse observation and
the controlled four-channel Wine audio setup retain their existing labels.

HIDAPI, Win32 count parity, ContainerId/WASAPI, GE execution and final capture
cleanup remain separate evidence. No game, native Windows parity, physical
actuation or physical latency claim follows from a successful synthetic run.

The first [Debian run 41643211748](https://github.com/AlexGarciaMartin/MadSense/actions/runs/41643211748)
at `0a9c30d` verified the image and all private runtime inputs, and QMP confirmed
KVM. QEMU then exited before SSH readiness; serial output ended at GRUB's
`Booting Debian GNU/Linux`. Runtime ZIP SHA-256 is
`63776e8c40d0b5e9eb62dfb06a413e5647eb6ac1fdb27f7751c8917752352368`.
No namespace preflight, broker fixture, consumer or GE execution occurred.
QEMU was reaped and the host KVM ACL was restored.

[Debian bug #1111240](https://bugs.debian.org/cgi-bin/bugreport.cgi?bug=1111240)
documents a matching trixie GRUB `gfxterm serial` symptom with `-nodefaults`;
the maintainer reproduced successful boot using the default emulated hardware.
The next attempt adds only an emulated VGA device while keeping `-display none`,
the image bytes, serial console and all namespace policies unchanged. This is
a boot hypothesis, not a measured root cause. Failure now also retains QEMU's
exit code before readiness; the independent namespace preflight still must pass
before any GE experiment.

The follow-up [run 41645341758](https://github.com/AlexGarciaMartin/MadSense/actions/runs/41645341758)
at `4109a34` booted the same image with emulated VGA and reached SSH in 14.36 s.
The ordinary UID/GID 1000 namespace preflight passed with no network route;
AppArmor remained enabled, and all recorded policy/process fields were identical
before and after. Runtime ZIP SHA-256 is
`758fe00807d1b29205851e6b8d3b6495c4d37e29501b316b57c8193c2685b9fa`.
Preparation then stopped at `modinfo: command not found`: `kmod` was installed,
but the ordinary account's PATH omitted its `/usr/sbin` directory. The adapter
now uses the package's explicit `modinfo`/`modprobe` paths. No broker fixture,
Windows consumer or GE runtime ran in this attempt; boot and namespace success
do not imply their compatibility. QEMU and temporary host KVM ACL cleanup passed.

[Run 41646559869](https://github.com/AlexGarciaMartin/MadSense/actions/runs/41646559869)
at `3767557` passed preparation, the namespace preflight and all ten native broker
cases, including descriptor-store crash recovery and preservation of the unrelated
virtual device. Its runtime ZIP SHA-256 is
`e666e1f727ee326700c4df48cec8eb4422ad4b239a1f5609dbd1e33f8fc2ecd9`.
Core setup then stopped before the actor started: the broad fixture-parent udev
trigger also reached its `power_supply` child, whose `change` event returned
`EAGAIN`. The fixture rule only changes `hidraw` and `input` nodes. The trigger
now selects those two subsystems under the same exact fixture parent and records
the selected paths; it still requires successful triggering and settlement.
[systemd 257 documents](https://www.freedesktop.org/software/systemd/man/257/udevadm.html)
that repeated subsystem matches are ORed. No battery event is required to apply
this ACL rule, and no error is ignored. No SDL, Windows consumer or GE execution
occurred in this attempt. Owned QEMU shutdown and exact host KVM ACL restoration
passed again.
