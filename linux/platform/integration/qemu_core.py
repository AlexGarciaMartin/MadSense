#!/usr/bin/env python3
"""Broker, production core and application controller in one owned QEMU guest."""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests/app-integration"))
from owned_qemu import OwnedQemu
from qemu_broker import verify_result
from qemu_app import prepare_app_payload, verify_app_result

REQUIRED_CORE_CASES = {
    "preexisting_output_capable_evdev_writer_refused",
    "preexisting_readonly_force_feedback_descriptor_refused",
    "readonly_sensor_observers_allow_acquire_and_all_grabs_are_stored",
    "production_hid_enumeration_features_parser_attach",
    "kernel_virtual_descriptor_features_neutral_input",
    "kernel_rgb_output_to_production_bluetooth_writer",
    "full_bluetooth_input_through_parser_and_virtual_usb",
    "alsa_four_channel_ingress_and_nonzero_bluetooth_haptics",
    "production_core_dispose_detach_reset_and_physical_restore",
}


def verify_core_result(result, token):
    if (result.get("result") != "passed" or result.get("cleanupError")
            or result.get("environment") != {"kind": "owned-qemu-guest", "instance": token}
            or result.get("physicalDevices") is not False or result.get("radioInitialized") is not False
            or result.get("applicationUiExercised") is not False):
        raise RuntimeError("Core result lacks successful owned synthetic scope")
    cases = result.get("cases", [])
    names = [case.get("name") for case in cases]
    if (any(case.get("passed") is not True for case in cases) or len(names) != len(set(names))
            or not REQUIRED_CORE_CASES.issubset(names)):
        raise RuntimeError("Core result is missing a required assertion")


def prepare_combined_payload(payload, neutral_probe, core_probe, app_probe):
    prepare_app_payload(payload, neutral_probe, app_probe)
    if not (core_probe / "MadSense.Linux.CoreProbe").is_file():
        raise RuntimeError("Publish the self-contained Linux core probe first")
    shutil.copytree(core_probe, payload / "core-probe")
    research = Path(__file__).resolve().parents[2] / "research"
    for name in ("host_probe.c", "test_kvm_acceleration.py"):
        shutil.copy2(research / name, payload / "linux/research" / name)
    hashes = {path.relative_to(payload).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in sorted(payload.rglob("*")) if path.is_file() and path.name != "PAYLOAD-SHA256SUMS"}
    (payload / "PAYLOAD-SHA256SUMS").write_text("".join(f"{digest}  {name}\n" for name, digest in hashes.items()))
    return hashes


def run_stages(guest):
    stages = (
        ("baseline", "linux/platform/tests/run_broker_experiment.sh", 900, ""),
        ("core", "linux/platform/integration/run_core_experiment.sh", 300, ""),
        ("app", "linux/tests/app-integration/run_app_experiment.sh", 660, " --require-core"),
    )
    for name, script, timeout, extra in stages:
        result = guest.run(name + "-run", "bash /home/ubuntu/madsense/" + script + " "
                           + shlex.quote(guest.token) + extra, timeout=timeout, required=False)
        guest.provenance[name + "Exit"] = result.returncode
        if result.returncode:
            break


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--neutral-probe-directory", type=Path, required=True)
    parser.add_argument("--core-probe-directory", type=Path, required=True)
    parser.add_argument("--app-probe-directory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    args = parser.parse_args()
    guest = OwnedQemu(args.output, args.work)
    provenance = guest.provenance
    try:
        source = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        provenance.update(experiment="linux-core-and-application-controller", sourceCommit=source)
        payload = guest.work / "payload"
        hashes = prepare_combined_payload(payload, args.neutral_probe_directory.resolve(),
                                          args.core_probe_directory.resolve(), args.app_probe_directory.resolve())
        (guest.output / "payload-files.json").write_text(json.dumps(hashes, indent=2) + "\n")
        guest.boot(); guest.upload(payload)
        guest.run("payload-check", "cd /home/ubuntu/madsense && sha256sum --check --strict PAYLOAD-SHA256SUMS")
        guest.run("probe-permissions", "chmod +x /home/ubuntu/madsense/probe/MadSense.Linux.Probe")
        try:
            run_stages(guest)
        finally:
            for name in ("prepare.log", "usbip-binary.txt", "result"):
                guest.collect(name)
        result_path = guest.output / "result/core-experiment/result.json"
        baseline_path = guest.output / "result/result.json"
        if provenance.get("baselineExit") != 0 or not baseline_path.exists():
            raise RuntimeError("Native broker baseline failed; later experiments were not started")
        baseline = json.loads(baseline_path.read_text())
        verify_result(baseline, guest.token)
        provenance.update(baselineCases=len(baseline["cases"]),
                          baselineResultSha256=hashlib.sha256(baseline_path.read_bytes()).hexdigest())
        upgrade = json.loads((guest.output / "result/idle-upgrade/result.json").read_text())
        if upgrade.get("result") != "passed" or len(upgrade.get("installations", [])) != 2:
            raise RuntimeError("Repeated idle installer check did not pass")
        if provenance.get("coreExit") != 0 or not result_path.exists():
            raise RuntimeError("Production core experiment failed; inspect core-run and partial evidence")
        result = json.loads(result_path.read_text())
        verify_core_result(result, guest.token)
        provenance.update(coreCases=len(result["cases"]),
                          coreResultSha256=hashlib.sha256(result_path.read_bytes()).hexdigest())
        app_path = guest.output / "result/app-experiment/result.json"
        if provenance.get("appExit") != 0 or not app_path.exists():
            raise RuntimeError("Application controller experiment failed; inspect app-run and partial evidence")
        app = json.loads(app_path.read_text())
        verify_app_result(app, guest.token)
        provenance.update(result="passed", appCases=len(app["cases"]),
                          appResultSha256=hashlib.sha256(app_path.read_bytes()).hexdigest())
    except BaseException as error:
        provenance["reason"] = f"{type(error).__name__}: {error}"
    finally:
        try:
            guest.close()
        finally:
            (guest.output / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
            print(json.dumps(provenance), flush=True)
    return 0 if provenance["result"] == "passed" and provenance["ownedQemuStopped"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
