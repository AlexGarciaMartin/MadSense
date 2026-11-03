#!/usr/bin/env python3
"""Separate owned guest: installed broker baseline, then real app controller."""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "platform/tests"))
from owned_qemu import OwnedQemu
from qemu_broker import prepare_payload, verify_result

REQUIRED_CASES = {
    "production_connect_settings_and_ready_require_input",
    "speaker_alto_settings_and_delivered_control", "speaker_bajo_settings_and_delivered_control",
    "speaker_off_settings_and_delivered_control", "mode_xbox_uses_production_settings_and_kernel_persona",
    "mode_auto_uses_production_settings_and_kernel_persona",
    "pause_confirms_virtual_removal_and_physical_permissions", "resume_reacquires_through_real_enumerator_and_broker",
    "synthetic_unplug_replug_automatically_reconnects", "dispose_and_fresh_helper_confirm_cleanup",
}


def verify_app_result(result, token):
    if (result.get("result") != "passed" or result.get("cleanupError")
            or result.get("environment") != {"kind": "owned-qemu-guest", "instance": token}
            or result.get("applicationControllerExercised") is not True or result.get("applicationUiExercised") is not False
            or result.get("physicalDevices") is not False or result.get("radioInitialized") is not False):
        raise RuntimeError("App result lacks successful owned-guest application-controller scope")
    cases = result.get("cases")
    if not isinstance(cases, list) or any(type(case) is not dict or case.get("passed") is not True for case in cases):
        raise RuntimeError("Malformed/failed app cases")
    names = [case.get("name") for case in cases]
    if len(names) != len(set(names)) or not REQUIRED_CASES.issubset(names):
        raise RuntimeError("Application controller cases are incomplete or repeated")


def prepare_app_payload(payload, neutral_probe, app_probe):
    prepare_payload(payload, neutral_probe.resolve())
    if not (app_probe / "MadSense.Linux.AppProbe").is_file():
        raise RuntimeError("Publish the self-contained application controller probe first")
    shutil.copytree(app_probe, payload / "app-probe")
    shutil.copytree(Path(__file__).resolve().parent, payload / "linux/tests/app-integration",
                    ignore=shutil.ignore_patterns("bin", "obj", "__pycache__", "*.pyc"))
    # The guest reuses qemu_broker.verify_result; its OwnedQemu import needs
    # these constants even though no launcher is invoked inside the guest.
    shutil.copy2(Path(__file__).resolve().parents[2] / "research/qemu_kernel.py", payload / "linux/research/qemu_kernel.py")
    shutil.copy2(Path(__file__).resolve().parents[2] / "research/kvm_acceleration.py", payload / "linux/research/kvm_acceleration.py")
    hashes = {path.relative_to(payload).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in sorted(payload.rglob("*")) if path.is_file() and path.name != "PAYLOAD-SHA256SUMS"}
    (payload / "PAYLOAD-SHA256SUMS").write_text("".join(f"{digest}  {name}\n" for name, digest in hashes.items()))
    return hashes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--neutral-probe-directory", type=Path, required=True)
    parser.add_argument("--app-probe-directory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    args = parser.parse_args()
    guest = OwnedQemu(args.output, args.work)
    provenance = guest.provenance
    try:
        provenance.update(experiment="linux-application-controller", sourceCommit=subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True).strip())
        payload = guest.work / "payload"
        hashes = prepare_app_payload(payload, args.neutral_probe_directory, args.app_probe_directory)
        (guest.output / "payload-files.json").write_text(json.dumps(hashes, indent=2) + "\n")
        guest.boot(); guest.upload(payload)
        guest.run("payload-check", "cd /home/ubuntu/madsense && sha256sum --check --strict PAYLOAD-SHA256SUMS")
        guest.run("neutral-executable", "chmod +x /home/ubuntu/madsense/probe/MadSense.Linux.Probe")
        try:
            baseline = guest.run("baseline-run", "bash /home/ubuntu/madsense/linux/platform/tests/run_broker_experiment.sh "
                                 + shlex.quote(guest.token), timeout=900, required=False)
            provenance["baselineExit"] = baseline.returncode
            if baseline.returncode:
                raise RuntimeError("Broker baseline failed; app experiment was not started")
            app = guest.run("app-run", "bash /home/ubuntu/madsense/linux/tests/app-integration/run_app_experiment.sh "
                            + shlex.quote(guest.token), timeout=660, required=False)
            provenance["appExit"] = app.returncode
        finally:
            for name in ("prepare.log", "usbip-binary.txt", "result"):
                guest.collect(name)
        verify_result(json.loads((guest.output / "result/result.json").read_text()), guest.token)
        app_path = guest.output / "result/app-experiment/result.json"
        if provenance.get("appExit") != 0 or not app_path.exists():
            raise RuntimeError("App experiment failed; inspect app-run and partial evidence")
        result = json.loads(app_path.read_text())
        verify_app_result(result, guest.token)
        provenance.update(result="passed", appCases=len(result["cases"]),
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
