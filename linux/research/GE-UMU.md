# GE-Proton and UMU: separate owned-VM experiment

The acquisition path is implemented and verified. Native GE/UMU execution is
**pending**. The consumer workflow exposes an explicit `ge_umu` dispatch input,
default false; ordinary pushes and production components do not execute GE.
This route uses GE-Proton, the UMU launcher and Valve's Steam Linux Runtime;
it is neither distribution Wine nor an execution of official Valve Proton.
It does not install Steam or require a Steam account or credentials.

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
extraction/verifier timing; no claim is made that it fits the total actor budget
until native evidence is available.

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

Still pending: native extraction/pv-verify, UMU/pressure-vessel start, ordinary
container device visibility, the PE assertions, SDL comparison, audio, cleanup
under actual GE child processes and licensing work for any redistribution.
Neither games, physical latency nor official Valve Proton compatibility is
validated by this acquisition or the hook's implementation.

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
