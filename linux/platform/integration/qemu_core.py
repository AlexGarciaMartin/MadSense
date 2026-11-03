#!/usr/bin/env python3
"""Baseline then production-core synthetic integration in one owned QEMU guest."""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from owned_qemu import OwnedQemu
from qemu_broker import prepare_payload, verify_result

REQUIRED_CORE_CASES = {
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--neutral-probe-directory", type=Path, required=True)
    parser.add_argument("--core-probe-directory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    args = parser.parse_args()
    guest = OwnedQemu(args.output, args.work)
    provenance = guest.provenance
    try:
        source = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        provenance.update(experiment="linux-production-core", sourceCommit=source)
        payload = guest.work / "payload"
        prepare_payload(payload, args.neutral_probe_directory.resolve())
        if not (args.core_probe_directory / "MadSense.Linux.CoreProbe").is_file():
            raise RuntimeError("Publish the self-contained Linux core probe first")
        shutil.copytree(args.core_probe_directory, payload / "core-probe")
        shutil.copy2(Path(__file__).resolve().parents[2] / "research/host_probe.c", payload / "linux/research/host_probe.c")
        hashes = {path.relative_to(payload).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                  for path in sorted(payload.rglob("*")) if path.is_file() and path.name != "PAYLOAD-SHA256SUMS"}
        (payload / "PAYLOAD-SHA256SUMS").write_text("".join(f"{digest}  {name}\n" for name, digest in hashes.items()))
        (guest.output / "payload-files.json").write_text(json.dumps(hashes, indent=2) + "\n")
        guest.boot(); guest.upload(payload)
        guest.run("payload-check", "cd /home/ubuntu/madsense && sha256sum --check --strict PAYLOAD-SHA256SUMS")
        guest.run("probe-permissions", "chmod +x /home/ubuntu/madsense/probe/MadSense.Linux.Probe")
        try:
            baseline = guest.run("baseline-run", "bash /home/ubuntu/madsense/linux/platform/tests/run_broker_experiment.sh "
                                 + shlex.quote(guest.token), timeout=900, required=False)
            provenance["baselineExit"] = baseline.returncode
            if baseline.returncode:
                raise RuntimeError("Native broker baseline failed; core experiment was not started")
            core = guest.run("core-run", "bash /home/ubuntu/madsense/linux/platform/integration/run_core_experiment.sh "
                             + shlex.quote(guest.token), timeout=300, required=False)
            provenance["coreExit"] = core.returncode
        finally:
            for name in ("prepare.log", "usbip-binary.txt", "result"):
                guest.collect(name)
        result_path = guest.output / "result/core-experiment/result.json"
        verify_result(json.loads((guest.output / "result/result.json").read_text()), guest.token)
        upgrade = json.loads((guest.output / "result/idle-upgrade/result.json").read_text())
        if upgrade.get("result") != "passed" or len(upgrade.get("installations", [])) != 2:
            raise RuntimeError("Repeated idle installer check did not pass")
        if provenance.get("coreExit") != 0 or not result_path.exists():
            raise RuntimeError("Production core experiment failed; inspect core-run and partial evidence")
        result = json.loads(result_path.read_text())
        verify_core_result(result, guest.token)
        provenance.update(result="passed", coreCases=len(result["cases"]),
                          coreResultSha256=hashlib.sha256(result_path.read_bytes()).hexdigest())
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
