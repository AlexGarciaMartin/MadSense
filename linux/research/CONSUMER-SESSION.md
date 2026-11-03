# Owned production-core consumer session

Consumer execution is still pending. The first owned runtime attempt,
[41613298887](https://github.com/AlexGarciaMartin/MadSense/actions/runs/41613298887)
at `884f0fd`, passed both builds, the broker baseline and idle install/removal,
then failed with `EACCES` launching the ordinary core actor's copy under `/run`.
It did not execute SDL or Wine. KVM permissions were restored and QEMU stopped.
The retained runtime ZIP SHA256 is
`8b8881d23f0f07d7337d214a7b0df6f31673f13bf2f636f67a85e75f28575ffb`.
Missing mount/mode observations prevent attributing that failure specifically
to `noexec`; subsequent staging records those observations and checks execution.

`linux-consumers.yml` builds the Linux and
Windows observers, then passes artifacts from the same workflow run to a separate
QEMU job. `qemu_consumers.py` verifies the exact source commit and every executable
SHA256 before copying them into the guest. The official signed Ubuntu image,
scoped KVM lifecycle and native broker baseline are shared with platform tests.
No physical device or radio is passed through. Source/build/license artifacts
remain in the two build artifacts; runtime evidence contains their provenance
and digests, the payload manifest, complete guest logs and partial failures.

The first job runs the DualSense persona. The core actor supports Edge and Xbox,
and the session supports Xbox FF/XInput, but their core guest assertions still
need persona-specific wiring before they are claimed as consumer runtime proof.
Native kernel enumeration of all three personas remains separate evidence.

The production fixture creates `consumer-ready.json` only after attributing the
new USB parent under VHCI. Besides the fields in `CONSUMERS.md`, it supplies
`features` (`09`, `20`, `05` hex), `virtualMacHex` (12 lowercase hex digits derived
from reversed feature-09 bytes 1–6, beginning `02d5`), `uhidEventsPath`, and
`coreOutputDirectory`. Its physical fixture has a distinct `02:FA` address.

The optional hook calls:

```python
run_suite(ready_path, binaries, wine_exe, fixture_state_callback,
          evidence_directory, physical_parent)
```

The callback receives `south` or `neutral` while the same fixture and core actor
stay alive. Each SDL observer independently verifies the south button and then
neutral controls, requests rumble and its stop, and sends RGB when supported.
The root orchestrator creates a fresh ordinary account with no supplementary
groups and a private home/runtime directory under a root-owned `/opt` staging
directory. It records mount flags and verifies an ordinary-UID executable there
before granting device access, without remounting `/run` or changing mount policy.
It grants that UID access only to
the attributed virtual evdev/hidraw and ALSA nodes, after requiring simple base
ACLs. A child running as that UID audits every guest HID/input/PCM node; foreign
nodes, including the physical UHID fixture, must remain inaccessible.

Every nonzero rumble marker and later stop must occur within that consumer's
monotonic time window in the captured Bluetooth writer output. Full output
framing and CRC are checked. Motor bytes count only when the report selects
rumble: flag0 bit1 plus legacy flag0 bit0 or V2 flag2 bit2. An RGB-only packet
containing zero motor bytes cannot satisfy the stop assertion. SDL HIDAPI's
firmware-dependent scaling is determined from feature20, rather than accepting
multiple possible marker values. RGB additionally requires its valid flag.
These assertions prove software delivery, not physical actuation.

One known semantic question must remain visible in the first run: both
[SDL2 2.30.0](https://github.com/libsdl-org/SDL/blob/release-2.30.0/src/joystick/hidapi/SDL_hidapi_ps5.c)
and pinned SDL3 clear rumble selector bits when stopping, intending to return to
audio haptics. That is different from a selected zero-amplitude rumble report.
The strict stop assertion may therefore fail while the marker arrives. Preserve
the API, virtual output and Bluetooth packets to distinguish that transition
from an unrelated RGB packet; do not infer a physical motor failure or weaken
the assertion from zero motor bytes alone. SDL2 also treats firmware version
zero as older firmware, while pinned SDL3 assumes an updated version in that case.

Wine runs as the same ordinary account with an ephemeral prefix and private,
cookie-authenticated Xvfb. The prefix retains default winebus behavior; managed
and HTML optional installers are disabled. Runtime package versions are logged.
The Windows program matches the virtual MAC before writes, compares feature
bytes with the core persona, verifies Raw Input input, and correlates RGB output.
XInput lacks a stable USB identity API, so its future Xbox assertion also relies
on the account's exclusive access to the one attributed virtual controller.

WASAPI uses an explicitly configured private PulseAudio session: S16LE/48 kHz,
four channels in front-left/front-right/rear-left/rear-right order, `tsched=0`,
four 25 ms fragments and owned sink volume 100%. It requires the guest's only
ALSA card to belong to this virtual USB device. It never changes global audio
configuration. The endpoint must have the exact nonzero ContainerId of the
MAC-verified HID device. No default endpoint or friendly-name fallback is used.
The deterministic 48,000-frame stream and rear-channel extraction must appear
exactly in the core captures, allowing only surrounding silence.

That configured PulseAudio result cannot establish default desktop behavior or
minimum latency. The next audio step must record installed `alsa-ucm-conf`, the
active Direct/HiFi profile and actual channel volumes, and test distribution
defaults plus PipeWire/WirePlumber in separate fresh sessions. It must preserve
the same USB/container association. A UCM profile/channel mismatch remains a
recorded failure, not a reason to bypass identity or silently remap channels.
The upstream UCM configuration and the installed distribution version may
differ; neither is presumed broken from an older issue report.

Commands and output files are bounded. Independent consumer assertion failures
are retained while later cases continue if the virtual device remains valid.
Teardown terminates only processes of the new UID, verifies the original node
identities and restores exact ACL snapshots. Unexpected ACL changes fail closed
and retain the private account rather than reusing its UID with stale grants.
Before/granted/observed/restored ACLs and cleanup outcomes are saved. The outer
fixture still owns controller disposal, detach and physical permission recovery.

The workflow's first runtime job requires all SDL and Sony/WASAPI cases plus
clean teardown. Partial execution cannot be reported as a pass. An ordinary
Wine result is not Proton evidence: no official Proton runtime has been acquired
or executed. Games, physical audio/haptics, Bluetooth and physical latency remain
outside this experiment.
