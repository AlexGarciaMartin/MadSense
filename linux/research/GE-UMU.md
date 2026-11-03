# GE-Proton and UMU: separate owned-VM experiment

Acquisition, native extraction and Valve's runtime verifier have completed.
The first prepared guest refused the ordinary-user namespace preflight, so
**no Windows consumer has executed under GE/UMU**. The consumer workflow exposes an explicit `ge_umu` dispatch input,
default false; ordinary pushes and production components do not execute GE.
This route uses GE-Proton, the UMU launcher and Valve's Steam Linux Runtime;
it is neither distribution Wine nor an execution of official Valve Proton.
It does not install Steam or require a Steam account or credentials.

The separate `ge_only` dispatch input (also default false, requiring `ge_umu`)
uses a fresh VM and a new account without first running distribution-Wine or SDL
consumers. It still requires the ten broker cases and a complete nine-case core
actor, including disposal, before starting a second core actor for GE. Its
result explicitly records `distributionWineExecuted=false` and
`priorDistributionResultsReused=false`. Earlier default/UDEV failures remain
separate failed observations; none is reused as a passed prerequisite.
The baseline and second actor share only their owned VM and verified code:
their fixture, controller lease, virtual device, core process and consumer
account are recreated. This mode has not executed natively yet.

`qemu_ge_only.py --run-ge-only` requires the same-commit Windows API/HIDAPI build
artifacts and the explicitly verified private GE cache. Acquisition and payload
copy verification still finish before boot. The guest's ordinary namespace
preflight precedes all broker/core fixtures. The second actor retains the
600-second budget, the same PE feature/input/output/ContainerId/WASAPI assertions,
real upstream HIDAPI checks, final raw-capture drain and exact ACL/process cleanup.
No private runtime/cache/prefix is collected. A GE result cannot replace a failed
core gate, and this remains GE-Proton with UMU, not official Valve Proton.

## Pinned upstream inputs

| Component | Exact input | SHA-256 |
| --- | --- | --- |
| GE-Proton | `GE-Proton11-11-x86_64.tar.gz`, 564789552 bytes | `aa85986cada5709aa8bbfa8add7f3bfb925cbe15710c98995898d308ff2ca3c1` |
| UMU | `umu-launcher-1.4.4-zipapp.tar`, 430080 bytes | `eb590691841f7fad3fc3ad8fd5db4ccb87849fe7948e62b28ece7a4ee48cc851` |
| Steam Linux Runtime 4 | `4.0.20261021.265997/SteamLinuxRuntime_4.tar.xz`, 175077288 bytes | `aa9dd1aab2173e65b631faae34b85e2c17fe323d941013154ddd2e1dce8a0910` |

The complete URLs, sizes and digests are in [ge-umu-lock.json](ge-umu-lock.json).
GE source is pinned to `c0c2ee35e6d4d3bff4d39c6246ac46636434feb8`;
UMU source is pinned to `cf3d1b107147480c447ffbfb3f789dc74335074c`.
The exact GE binary's `toolmanifest.vdf` requires app ID `4183110`.
The pinned UMU mapping resolves that ID to `steamrt4`, not the older sniper runtime.

The GE archive also matches its project's SHA-512 file:
`45721a7b625b2404d8896bb5e2a8b87fc9623a394873a6dd6b900c839681d5c01ec694ca00d003fc9c733c5f6c5648af8171985656f9fa47e20999fbebdbe235`.
Valve's exact `SHA256SUMS` matches the runtime, build ID and retained package/source
manifests. The runtime `VERSIONS.txt` identifies pressure-vessel `0.20261007.0`.
These are HTTPS upstream checksums and GitHub release API digests, not an
independent signature or reproducible-build proof. Source archive and metadata
digests identify the first verified retrieval; a changed response fails the lock.

## Reproduce acquisition without execution

From the repository root, with Python 3.12 or newer:

```sh
python3 linux/research/ge_umu_acquire.py \
  --cache /owned-work/ge-umu-cache --evidence /owned-work/ge-umu-evidence
python3 -m unittest discover -s linux/research -p test_ge_umu.py
```

Use a new evidence directory on every attempt. `--verify-only` rechecks a
previous cache without networking. It verifies **every** locked file and both
upstream checksum formats, inventories each tar, saves exact notice bytes by
hash, and compares UMU's bundled `umu_run.py`, `umu_runtime.py`, `umu_util.py`
against the pinned source archive. The original archives stay in the cache.
HTTP redirects are checked against an explicit upstream HTTPS host list; no
cookies, account tokens, credential store or environment proxy is used. Each
asset has an exact byte limit, a 20-second read timeout and a 300-second overall
budget checked between bounded reads. The entire lock is limited to 1.1 GB.
Failed partial downloads remain separate and cannot be resumed or promoted
silently. Inventory expansion is also bounded and never follows archive links.

Actual local validation on 2026-11-03 downloaded the three principal payloads,
GE and UMU source archives, GE git tree and Valve source/package manifests.
The full acquisition verifier passed and retained 234 distinct notice/metadata
objects. It inventoried 8916 GE binary entries, 8021 runtime entries, 5046 GE
source entries and 172 UMU source entries. Twelve portable tests pass, including
same-size corruption, wrong/duplicate checksum entries, redirected credentials
or hosts, overflow/truncation/deadline, archive traversal/devices/escaping links,
and preservation of notice bytes. Two additional Linux-only regressions cover
exclusive marker creation and a pre-existing symlink without changing its
target. Both executed successfully on Ubuntu in
[run 41628578603](https://github.com/AlexGarciaMartin/MadSense/actions/runs/41628578603)
at `0325da760148b9988315c6b2172c3a55bf9a1b01` (70/70 evidence tests, no skips).
They remain skipped on the Windows development host. No downloaded
code was executed on Windows.

## Sources, licenses and distribution scope

UMU's pinned source includes GPLv3; its bundled Python dependencies retain
their individual metadata/licenses. GE's aggregate license identifies its
BSD-licensed Proton wrapper and components including Wine/LGPL 2.1, DXVK/zlib,
vkd3d-proton/LGPL 2.1, fonts and other separately licensed projects. The script
preserves the actual archive notices rather than assigning one license to the
entire payload. The Valve runtime includes package copyright notices, and its
source-required list and Debian source manifest are retained and hash checked.

GE's source archive contains the top-level sources/patches; its tree has **43
gitlinks**, whose exact commit IDs are retained. It does not contain all
submodule source. Valve's source manifest identifies further source packages;
those package archives are not all downloaded here. Consequently this is a
private research cache, **not a complete corresponding-source bundle or a new
redistributable MadSense package**. Do not upload the runtime cache as a product
artifact. An eventual redistribution needs the complete applicable sources and
notices; this work does not assert that obligation has been fulfilled.

## Optional consumer hook and explicit dispatch

The `Linux consumer compilation and owned runtime` workflow accepts boolean
`ge_umu=true` on manual dispatch. Acquisition, checksum/source/notice verification
and private cache staging finish before QEMU boot and before its core actor starts.
Only inventories, notices and JSON provenance enter the artifact;
`dist/ge-umu-private-cache` and `dist/consumer-qemu-private` are excluded.
The acquisition step is bounded to 15 minutes. This opt-in increases the CI job
budget from 50 to 65 minutes; it does not increase the actor's 600-second lifetime.
Ordinary pushes keep the default Wine/UDEV path and never download this cache.

For an already verified private cache, the equivalent launcher option is
`qemu_consumers.py --ge-umu-cache /owned-work/ge-umu-cache --wine-udev-variant`
alongside its normal same-commit binary, output and private-work arguments.
The guest accepts only the staged `ge-umu-private-cache` location and forwards
it to `run_core_guest.py --ge-umu-cache`. Supplying GE without the preceding
Wine/UDEV experiment fails before starting consumers. Preparation records actual
extraction/verifier timing. The first native preparation took 37.15 seconds;
the total budget for actual Windows consumers remains unmeasured.

`ge_umu_consumer.run_after_experiments(primary, ready_path, executable,
fixture_state_callback, evidence, physical_parent, cache)` is implemented for
the opt-in guest integration. `primary` runs the normal suite with its
separate UDEV diagnostic; both must leave same-guest, same-virtual-device cleanup
records, including an exact digest join to the UDEV result. API failures in those
earlier experiments remain recorded and are rethrown after GE; cleanup failures
prevent GE from starting at all. This specifically preserves the failed default
Wine result instead of letting another runtime replace it.

GE uses a fresh `ConsumerSession`, prepares the pinned runtime before launching
the private display, and calls the exact same `run_windows_cases` assertions
as distribution Wine through its own bounded UMU invocation callback. Those
assertions require MAC/features, Raw Input, RGB delivery and the exact
ContainerId/WASAPI/PCM association. Launcher output must still parse as native
observer evidence; absent or contaminated output fails. Version/inventory alone
cannot set `runtimeExecutionConfirmed`; an actual PE runtime record is required.
`consumerAssertionsPassed` remains false until the entire shared suite completes.

The separate `ge-umu-suite/result.json` has scope
`configured-ge-proton-umu-consumer`, `officialValveProton=false`, and a cleanup
summary/digest joined to the primary result. The launcher keeps the verified cache
outside collected `result/` and transfers it only to the owned guest's private
payload/work space. The hook never copies archives into evidence. Six new portable tests verify dependency
cleanup/identity/digests, retention of default failures, stale-result rejection
and unchanged feature-byte assertions even when the PE returns exit zero.

`ge_umu_payload.stage(cache, payload, collected_output)` is the prepared launcher
seam: it runs only after the acquisition/inventory command has finished and before
booting the guest or starting its core actor. It rejects evidence/cache/payload
tree overlaps, copies the locked archives only to `payload/ge-umu-private-cache`,
verifies the copied bytes again, and adds the GE modules to the private guest
payload. Three regressions cover output-tree exclusion, copied-byte corruption
and refusal to reuse a partial destination, plus imports from the staged tree.
The launcher records `geUmuStagedBeforeGuestBoot` after this call and before boot;
the helper alone does not prove execution order.
Downloads therefore consume no part of the actor's 600-second lifetime. Native
extraction and verifier/namespace execution remain inside the later fresh UID.

[ge_umu_session.py](ge_umu_session.py) exposes `prepare(session, cache)` and
`run_consumer(session, prepared, staged_pe, arguments, label=..., timeout=...)`.
The caller must already hold an entered `ConsumerSession` in the owned x86-64
VM, after exact virtual USB attribution. Preparation verifies the entire cache,
extracts only binary payloads into fresh session directories with Python's data
filter, and runs Valve's `pv-verify --minimized-runtime` as the ordinary UID.
Only a successful mtree check permits UMU's installed marker, created by the
ordinary UID using `O_CREAT|O_EXCL|O_NOFOLLOW`. Existing files or links fail;
root never writes or changes ownership through the user-controlled marker path.
Runtime version metadata is read before transferring ownership or executing
payload code. GE stays in the
root-owned executable staging tree; the UMU runtime, fresh prefix, caches and
state live under the disposable user's home. No global package installation is
needed for the self-contained UMU zipapp.

The environment selects explicit `PROTONPATH`, `GAMEID=umu-default`, `STORE=none`,
disables runtime updates and game-specific protonfixes, and bounds UMU HTTP
timeouts/retries. It does not disable pressure-vessel, AppArmor or other security
mechanisms. Each consumer invocation uses an additional ordinary-user network
namespace with the current UID mapped to itself and no network route. This
keeps upstream auto-downloads from escaping the pinned offline experiment.
The normal `unshare` capability is tested first. If the guest denies unprivileged
namespaces, or pressure-vessel cannot run nested in that namespace, record the
failure and stop; do not change sysctls, profiles, permissions or sandbox flags.
This extra offline namespace is an experimental condition, not a desktop default.

The launcher `--version` output and runtime version files are inventory evidence.
They do not prove that a Windows consumer ran. `run_consumer` preserves exit
status and raw output and leaves `runtimeExecutionConfirmed` and
`consumerAssertionsPassed` false for the independent consumer suite to establish.
Before reporting success that suite must still verify the `02:D5` virtual MAC,
HID/RawInput path and features, identity inside the container, matching Windows
ContainerId/audio endpoint, controlled input states, actual output markers and
PCM delivery. It must preserve the existing stop-selector assertions. Close the
session even on failure: its process, account, private-file and exact ACL cleanup
remains mandatory. Do not overlap this session with distribution Wine or the
default-audio observers. The caller supplies a private authenticated display and
an explicitly labelled audio stack if needed.

Still pending: successful PE scope validation, ordinary container device
visibility, the PE assertions, SDL comparison under GE, audio and licensing work
for any redistribution. The latest Debian attempt below reached the PE scope
check and retained its explicit failure in a Proton log.
Neither games, physical latency nor official Valve Proton compatibility is
validated by this acquisition or the hook's implementation.

## First native preparation and namespace refusal

[Run 41637839035](https://github.com/AlexGarciaMartin/MadSense/actions/runs/41637839035)
at `3a7f8a88f5964e1e91f58d36a7167053886b15b4` retained runtime artifact SHA-256
`940e9dea31ba04c7c140b502b14b75fa07ecc03bc9cef1bf085c014856a78b6a`.
All pinned assets were reverified in the owned Ubuntu guest. Extraction,
`pv-verify`, the ordinary-UID install marker and UMU `--version` succeeded.
Preparation took 37.15 seconds, excluding downloads. The subsequent
`unshare --user --map-current-user --net` preflight returned exit 1 with
`write failed /proc/self/uid_map: Operation not permitted`.

The failed operation occurred before Xvfb, pressure-vessel or the Windows PE
started. `runtimeExecutionConfirmed` and `consumerAssertionsPassed` remain false.
The GE account, files and processes were removed and all five exact virtual
device ACLs were restored. No runtime cache was included in the artifact.
SDL's four cases passed separately; distribution Wine and its explicit UDEV
variant retained their ContainerId failures. The gain experiment refused its
incoherent initial Route/Node state before mutation and verified cleanup.

The artifact records AppArmor initialization but no denial at the time of this
namespace operation, and no relevant sysctl or current process profile snapshot.
Ubuntu documents restricted unprivileged namespaces, but this capture alone
does not establish which policy caused this refusal. No protection was disabled,
and the preflight failure is not a GE compatibility result. The opt-in
[Debian adapter](DEBIAN-CONSUMERS.md) retains its own unchanged policy and requires
ordinary-UID preflight evidence before any broker or core actor starts. Its
official cloud image is verified through HTTPS and pinned SHA-512, explicitly
without a GPG signature. It does not replace the signed Ubuntu default.

## First Debian launch and retained failures

[Run 41653912192](https://github.com/AlexGarciaMartin/MadSense/actions/runs/41653912192)
at `40e2de13b77482a1da3585cf23e6daeda39d2e86` retained runtime artifact SHA-256
`c7abac46d936dc4348baec09796a13e0f64b2bbf9e6e9b3626625ec8a2613555`.
The unchanged Debian guest passed its ordinary-UID namespace preflight. GE
preparation, including the mtree verifier and UMU 1.4.4 inventory, took 27.73
seconds. Four native SDL cases passed separately. Distribution Wine 10 and its
configured UDEV variant retained their ContainerId and HIDAPI failures.

The GE Win32 cases stopped before launching their PE: a repeated clear of an
already cleared synthetic state produced no new output, so the observer could
not confirm the clear. The GE path now uses the existing HIDAPI priming helper
before each Sony case: write a distinct RGB marker, require its CRC-valid
Bluetooth observation, then require the clear. Silence still fails. The default
distribution Wine path is unchanged.

The separately primed HIDAPI cases did invoke UMU, pressure-vessel and Proton
twice. Both returned exit 1 with empty PE stdout; neither reported a PE build ID
or completed assertion result. Stderr included an i386 loader probe failure and
an empty game-drive parent warning. These messages do not establish the cause
of exit. `runtimeExecutionConfirmed` and `consumerAssertionsPassed` remain false.
All three consumer accounts and their processes/files were removed, each set of
five exact device ACLs was restored, and the core actor confirmed cleanup with
zero rejected/unwritten input and raw-output snapshots.

Each later GE invocation now attempts a bounded Proton log snapshot before
cleanup, including when the launcher fails. The reader runs as the disposable
ordinary UID, opens HOME and each log with `O_NOFOLLOW`/directory-relative file
descriptors, and requires stable regular files owned by that UID with one hard
link. It retains at most four logs, one MiB per log and two MiB in total, with
SHA-256 and explicit truncation metadata. Root writes only fixed evidence names;
prefixes and runtime caches remain excluded. Unavailable diagnostics cannot
replace the original consumer failure. Linux-native tests of these new snapshot
guards remain pending; portable lifecycle and error-preservation tests pass.

The separate GE-only [run 41653917398](https://github.com/AlexGarciaMartin/MadSense/actions/runs/41653917398)
retained artifact SHA-256
`0442df8ee90a128909b6d6ad908d2a1a86e7029fea22de879bc75d556c727661`.
Its first core actor reported eight physical input reports pending and failed
the disposal assertion. The second actor and GE session never started. This is
a distinct bridge/core failure, not a GE runtime result; distribution Wine was
not executed in that mode.

## Console output and the standalone credit-flow run

[Run 41658726882](https://github.com/AlexGarciaMartin/MadSense/actions/runs/41658726882)
at `16e857ec9bf3f1da0e0b130a1904914e5df874f3` retained runtime artifact SHA-256
`78d2bd14d1d3fbb3671027545e86643df08fa26db596aefc2aed913a9074d506`.
Its first core actor passed all nine prerequisites. The fresh GE actor retained
eight completed cases and confirmed disposal, with no input overflow or isolation
failure; its overall result still failed because the consumers supplied no final
JSON. Neither runtime identity nor game compatibility is inferred from that exit.

The four bounded Proton logs do show the two observer PEs being loaded. Their
stdout remained empty and all four launches returned exit 1. This is more precise
than saying that the PE never ran. The Win32 observer's log also contains a C++
exception; obtaining its own final diagnostic remains necessary. The logs contain
other warnings, including i386 loader probing and xalia/Mono, which are not by
themselves evidence that missing packages caused the consumer failure.

The pinned [Proton launcher](https://github.com/GloriousEggroll/proton-ge-custom/blob/c0c2ee35e6d4d3bff4d39c6246ac46636434feb8/proton)
routes Unix executable paths through `umu.exe`. Its
[helper implementation](https://github.com/GloriousEggroll/proton-ge-custom/blob/c0c2ee35e6d4d3bff4d39c6246ac46636434feb8/umu_helper/umu.c)
selects `CREATE_NEW_CONSOLE` for console PEs and calls `CreateProcessW` with handle
inheritance disabled. That path does not preserve the redirected output contract
required by these observers. The subsequent experiment passed the same resolved,
session-owned PE as a canonical Windows `Z:` path, using Proton's existing direct
Wine-launch branch. UMU, the pinned pressure-vessel runtime, ordinary UID,
offline namespace and all consumer assertions remain in place. No runtime is
patched or bypassed. The launch intent is recorded separately; a native rerun
must establish the full observer output contract. This change alone is not a
compatibility result.

## Windows-path launch and the original VM marker

[Run 41661637630](https://github.com/AlexGarciaMartin/MadSense/actions/runs/41661637630)
at `032e1cc382b80d4f1c9088adc9c8a0ed4b0b4954` retained runtime artifact SHA-256
`9863352a64677629fb2b754ec823eca644e81cff105b03afffc3eab683d673e4`
(3118937 bytes). The independent audit matched 99 payload sources and four
installed files to that commit, 28 retained build sources, both observer PEs and
all 519 guest payload checks. Published core/probe binaries are represented by
the checked guest manifest; their bytes were not included in the runtime artifact.
The baseline passed ten broker and nine core cases. The second actor retained
eight cases, confirmed cleanup and delivered the final three neutralization
reports byte for byte. It read all 4519 reports received, with zero queue entries,
overflow or lost-isolation reason. All five consumer ACLs, the disposable account,
QEMU and the temporary KVM ACL were restored. The failed GE result stays failed.

All four Proton commands used the intended `Z:` path and loaded the exact PE.
The launcher pipe remained empty and each process returned exit 1. Unlike the
preceding run, their complete bounded Proton logs contain the observer's own
final failure JSON: the Win32 cases report `Open QEMU scope marker (Win32=2)`;
the HIDAPI cases report `Cannot read owned VM marker`. This occurs before runtime
identity, HID capabilities or ContainerId enumeration. No device compatibility
or audio result follows from those diagnostics. The existing parser correctly
did not promote mixed Proton log text into functional stdout evidence.

Two independent harness assumptions need correction. The pinned Proton
launcher's `run_proc` sends stdout and stderr to `self.log_file` when
`PROTON_LOG=1`. Functional invocations now explicitly use `PROTON_LOG=0` while
retaining the Windows path, ordinary account, network namespace and runtime.
The bounded log reader remains diagnostic-only; the historical logs are retained,
and a fresh functional invocation need not create a Proton log file.

Pressure-vessel also supplies its own `/etc`. In the exact source version
`0.20261007.0`, `pressure-vessel/wrap.c` lines 432–434 already mount the original
host `/etc` read-only at `/run/host/etc`; `pressure-vessel/bwrap.c` lines 394–409
provide `/sys` read-only for this non-development invocation. Here “host” means
the owned QEMU guest outside the nested runtime, not the CI host. User-requested
exports cannot override `/etc`: it is reserved in `utils.c` and checked in
`wrap-context.c`. Adding `PRESSURE_VESSEL_FILESYSTEMS_RO` would not fix this path.

Only GE supplies `MADSENSE_CONSUMER_SCOPE_LAYOUT=pressure-vessel`. The shared PE
selector maps that exact value to `Z:\run\host\etc\madsense-ci-owner`; an absent
variable retains distribution Wine's `Z:\etc\madsense-ci-owner`. Empty, unknown
or path-like values fail, and a failed read never falls back. Both paths still
require the original UUID and `Z:\sys\class\dmi\id\sys_vendor` equal to `QEMU`,
the orchestrator's isolated-account token and Wine's runtime entry point. No
marker is copied or fabricated, no extra mount is requested, and no guard is
disabled. Ten portable selector cases are wired into the Windows API build before
cross-compiling the PEs. Their Linux execution and real execution of the corrected
layout/stdout contract remain pending; no local C++ compiler was available.

The [exact Valve source archive](https://repo.steampowered.com/steamrt4/images/4.0.20261021.265997/sources/steam-runtime-tools_0.20261007.0.tar.xz)
is 1324028 bytes with SHA-256
`2779b4a978c565e5262e27615884293e77d53b3a87387a844d1e30806b849175`, verified
against the fixed runtime's SHA256SUMS and source manifest. The source retains its
copyright notices; these harness changes do not copy pressure-vessel code or
redistribute its runtime. Existing GE/UMU/runtime licensing limitations still apply.

## Raw Input wait after the marker correction

[Run 41665176300](https://github.com/AlexGarciaMartin/MadSense/actions/runs/41665176300)
used commit `a97ae985c5d6e58e9377fb8dbe1aad6adde5aa5f` and remains failed. Its
artifact SHA-256 values are: runtime
`b7c442840f15f470fdc43e0ceab29719f7a23e2fc242cf13baa7ca9069d08427`, Linux
consumers build `93bf778cbfaf5de1ddfd069cf2f92f603ec59a2b60e7fb909443103d649dc5f1`,
Windows API consumer build
`de2666133eb66cc117298900529cf71f7dfba48bba2e0dd9ae77efb7fcbe1158` and Windows
HIDAPI consumer build
`ede37f72ef60bdbf08e7b88e36bde074cd263ce1aedb6e8621ceef697b56f38a`. The
independent audit (`work/audit_ge_41665176300.py`; JSON SHA-256
`0a6abf03fc24a46f026b0153faa2427814668ffacbd1b9dc6bc2fd2937b7b177`) verified 99
committed payload sources, four installed files, 31 build sources and both PE
digests against that exact commit, plus the guest's complete payload checksum.

The baseline passed ten broker and nine core cases. The GE actor retained eight
cases, 25 raw USB output reports with zero rejected or unwritten snapshots, the
final three neutralization reports byte for byte, no lost isolation and no input
overflow. All five consumer ACLs, the disposable account, QEMU and the temporary
KVM ACL were restored. The CI log lines "10 passed" for
`owned-marker-layout-selection` and "14 overlapped completion policy regressions
passed" come from the Windows API build job's host unit tests
(`deviceAccess=false`, `wineExecuted=false`); they are not guest evidence.

| Case | Exit | Last stage reached inside GE-Proton 11-11 (Wine 11.0 Staging) | Recorded result |
| --- | --- | --- | --- |
| ge-umu-sony-south | 1 | HID capabilities 64/48/64 bytes, `macMatched`, ContainerId `{0CE6054C-0001-0005-6549-180900000000}`, one Raw Input gamepad collection matching the selected path, features `09` (20), `20` (64) and `05` (41) | `No matching Raw Input report within five seconds (Win32=2)` |
| ge-umu-sony-neutral | 1 | Identical enumeration | Identical failure |
| ge-hidapi-windows-south | 0 | HIDAPI 0.15.0 enumeration, the same three features, the same ContainerId, one `hid_read`, `hid_write` returned 47 of 48 bytes without a library error | `observed`; output delivery and native Windows parity unverified |
| ge-hidapi-windows-neutral | 0 | Identical | Identical |
| ge-umu-wasapi | — | `pactl` sink list `[]` | Failed before any audio case: PulseAudio did not expose the owned ALSA sink |

The scope-marker failure of the two previous runs is gone: the exact PE read the
owned VM marker through pressure-vessel's read-only host `/etc`
(`scopeMarkerLayout=pressure-vessel-readonly-host-etc`, `scopeMarkerCopied=false`)
with the Windows `Z:` path, the ordinary account, the private network namespace
and the verified runtime. Functional JSON now arrives on the launcher stdout
pipe; `PROTON_LOG=0` left all four Proton log snapshots empty by design.

The Win32 observer registers usage page 1 / usage 5 with `RIDEV_INPUTSINK` on a
message-only window and pumps `PeekMessage` for five seconds; no `WM_INPUT` for
the selected device arrived, while the HIDAPI observer read an input report from
the same virtual device. This run does not establish whether Wine delivers Raw
Input to message-only windows under Xvfb, whether a state change was issued
during the wait, or whether the virtual device's report pipe was idle; no cause
is claimed and no harness timeout or window policy is changed from this run. The
empty PulseAudio sink list means the owned card was not visible to the ordinary
account's PulseAudio; whether its module or ACL/udev exclusion is responsible is
not established. The kernel wait capture retained two snapshots with transient
`/proc` child misses and no `hidraw_write` cycle. No game, physical device,
radio or latency result follows from this run.

## Primary references

- [GE release](https://github.com/GloriousEggroll/proton-ge-custom/releases/tag/GE-Proton11-11),
  [pinned GE source](https://github.com/GloriousEggroll/proton-ge-custom/tree/c0c2ee35e6d4d3bff4d39c6246ac46636434feb8),
  [GE license](https://github.com/GloriousEggroll/proton-ge-custom/blob/c0c2ee35e6d4d3bff4d39c6246ac46636434feb8/LICENSE).
- [UMU release](https://github.com/Open-Wine-Components/umu-launcher/releases/tag/1.4.4),
  [UMU documentation](https://github.com/Open-Wine-Components/umu-launcher/blob/cf3d1b107147480c447ffbfb3f789dc74335074c/README.md),
  [runtime installer and mapping](https://github.com/Open-Wine-Components/umu-launcher/blob/cf3d1b107147480c447ffbfb3f789dc74335074c/umu/umu_runtime.py),
  [UMU license](https://github.com/Open-Wine-Components/umu-launcher/blob/cf3d1b107147480c447ffbfb3f789dc74335074c/LICENSE).
- [Valve's fixed runtime build and source index](https://repo.steampowered.com/steamrt4/images/4.0.20261021.265997/),
  [its SHA256SUMS](https://repo.steampowered.com/steamrt4/images/4.0.20261021.265997/SHA256SUMS).
