#!/usr/bin/env python3
"""Explicit separate VM: broker/core disposal gates, then a fresh GE-only actor."""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "platform/tests"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "platform/integration"))
from qemu_consumers import stage_consumer_research, verify_build
from qemu_broker import prepare_payload, verify_result as verify_broker
from qemu_core import verify_core_result
from ge_umu_standalone import prior_gates, verify_result, read


def stage_payload(payload, neutral, core, windows, hidapi, cache, output, commit):
    digests = verify_build(windows, ("wine_consumer.exe",), commit)
    digests.update(verify_build(hidapi, ("hidapi_windows_consumer.exe",), commit))
    prepare_payload(payload, neutral)
    if not (core / "MadSense.Linux.CoreProbe").is_file():
        raise RuntimeError("Self-contained production core actor required")
    shutil.copytree(core, payload / "core-probe")
    binaries = payload / "consumer-bin"; binaries.mkdir()
    for directory, name in ((windows, "wine_consumer.exe"), (hidapi, "hidapi_windows_consumer.exe")):
        shutil.copy2(directory / "bin" / name, binaries / name)
    stage_consumer_research(payload)
    research = Path(__file__).resolve().parent
    for name in ("ge_umu_standalone.py", "test_ge_umu_standalone.py", "run_ge_only_guest.sh",
                 "prepare_debian_kernel.sh", "run_debian_broker_guest.sh", "ge_namespace_probe.py"):
        shutil.copy2(research / name, payload / "linux/research" / name)
    from ge_umu_payload import stage
    private = stage(cache, payload, output)
    hashes = {path.relative_to(payload).as_posix():hashlib.sha256(path.read_bytes()).hexdigest()
              for path in sorted(payload.rglob("*")) if path.is_file() and path.name != "PAYLOAD-SHA256SUMS"}
    (payload / "PAYLOAD-SHA256SUMS").write_text("".join(f"{digest}  {name}\n" for name,digest in hashes.items()))
    return dict(consumerBuildDigests=digests, geUmuPrivatePayload=private,
                geUmuStagedBeforeGuestBoot=True), hashes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-ge-only", action="store_true")
    parser.add_argument("--observe-kernel-waits", action="store_true")
    parser.add_argument("--guest-image", choices=("ubuntu", "debian13"), default="debian13")
    for name in ("neutral-probe-directory", "core-probe-directory", "windows-artifact", "hidapi-artifact",
                 "ge-umu-cache", "output", "work"):
        parser.add_argument("--"+name, type=Path, required=True)
    args = parser.parse_args()
    if not args.run_ge_only:
        parser.error("Explicit GE-only experiment required")
    if args.guest_image == "debian13":
        from debian_qemu import OwnedDebianQemu
        guest = OwnedDebianQemu(args.output, args.work)
    else:
        from owned_qemu import OwnedQemu
        guest = OwnedQemu(args.output, args.work)
    p = guest.provenance
    p.update(experiment="standalone-ge-proton-umu", geOnlyExperiment=True, distributionWineExecuted=False,
             priorDistributionResultsReused=False, officialValveProton=False, runtimeCacheCollected=False,
             guestImageSelection=args.guest_image, kernelWaitObservationRequested=args.observe_kernel_waits)
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        p["sourceCommit"] = commit
        payload = guest.work / "payload"
        metadata, hashes = stage_payload(payload, args.neutral_probe_directory.resolve(), args.core_probe_directory.resolve(),
            args.windows_artifact.resolve(), args.hidapi_artifact.resolve(), args.ge_umu_cache.resolve(), guest.output, commit)
        p.update(metadata)
        (guest.output / "payload-files.json").write_text(json.dumps(hashes, indent=2)+"\n")
        for label, directory in (("windows-build", args.windows_artifact), ("hidapi-build", args.hidapi_artifact)):
            target = guest.output / label; target.mkdir()
            for name in ("provenance.txt", "binary-sha256.txt", "source-sha256.txt"):
                if (directory / name).is_file():
                    shutil.copy2(directory / name, target / name)
        guest.boot(); guest.upload(payload)
        guest.run("payload-check", "cd /home/ubuntu/madsense && sha256sum --check --strict PAYLOAD-SHA256SUMS")
        namespace = guest.run("namespace-preflight", "python3 /home/ubuntu/madsense/linux/research/ge_namespace_probe.py "
            "--owned-guest-token "+shlex.quote(guest.token), timeout=30, required=False)
        p["earlyNamespacePreflightExit"] = namespace.returncode
        observed = read(guest.output / "namespace-preflight.stdout")
        if (namespace.returncode != 0 or observed.get("result") != "passed"
                or observed.get("ownedGuestToken") != guest.token or observed.get("securityPolicyModified") is not False
                or observed.get("policyBefore") != observed.get("policyAfter")):
            raise RuntimeError("Ordinary namespace preflight failed before any fixture/GE; policy unchanged")
        p["earlyNamespacePreflightVerified"] = True
        guest.run("probe-permissions", "chmod +x /home/ubuntu/madsense/probe/MadSense.Linux.Probe")
        try:
            baseline_script = ("linux/research/run_debian_broker_guest.sh" if args.guest_image == "debian13"
                               else "linux/platform/tests/run_broker_experiment.sh")
            baseline = guest.run("baseline-run", "bash /home/ubuntu/madsense/"+baseline_script+" "+shlex.quote(guest.token),
                                 timeout=900, required=False)
            p["baselineExit"] = baseline.returncode
            if baseline.returncode == 0:
                response = guest.run("ge-only-run", "bash /home/ubuntu/madsense/linux/research/run_ge_only_guest.sh "
                    +shlex.quote(guest.token)+(" --observe-kernel-waits" if args.observe_kernel_waits else ""),
                    timeout=1000, required=False)
                p["geOnlyExit"] = response.returncode
        finally:
            for name in ("prepare.log", "usbip-binary.txt", "result"):
                guest.collect(name)
        directory = guest.output / "result"
        verify_broker(read(directory / "result.json"), guest.token)
        gates = prior_gates(directory, guest.token)
        p["priorFunctionalGates"] = gates
        experiment = directory / "core-ge-experiment"
        path = experiment / "ge-umu-suite/result.json"
        if path.exists():
            result = read(path)
            p["geUmuExperiment"] = dict(result=result.get("result"), scope=result.get("scope"),
                resultSha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                runtimeExecutionConfirmed=result.get("geUmuSession", {}).get("runtimeExecutionConfirmed", False),
                cleanupVerified=result.get("accountProcessesAndFilesRemoved") is True and not result.get("cleanupErrors"))
            from run_hidapi_windows import final_capture_summary
            stopped = read(experiment / "core/stopped.json") if (experiment / "core/stopped.json").exists() else {}
            p["realHidapiWindows"] = final_capture_summary(result, stopped, guest.token)
        if p.get("geOnlyExit") != 0:
            raise RuntimeError("GE-only execution failed; retained prior functional gates and partial GE evidence")
        verify_core_result(read(experiment / "result.json"), guest.token)
        ready = read(experiment / "consumer-ready.json")
        gates = prior_gates(directory, guest.token, ready["actorPid"])
        verify_result(read(path), ready, gates)
        if not p.get("realHidapiWindows", {}).get("reportForwardingVerified"):
            raise RuntimeError("GE-only real HIDAPI forwarding or final capture cleanup failed")
        p["result"] = "passed"
    except BaseException as error:
        p["reason"] = f"{type(error).__name__}: {error}"
    finally:
        try:
            guest.close()
        except BaseException as error:
            p.update(result="failed", cleanupError=str(error))
        (guest.output / "provenance.json").write_text(json.dumps(p, indent=2)+"\n")
        print(json.dumps(p), flush=True)
    return 0 if p["result"] == "passed" and p["ownedQemuStopped"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
