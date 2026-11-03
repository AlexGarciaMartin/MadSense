#!/usr/bin/env python3
"""Run the installed privilege broker's native tests in an owned QEMU guest."""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import shutil
import subprocess

from owned_qemu import OwnedQemu

REQUIRED_CASES = {
    "root_application_is_rejected", "polkit_denies_unapproved_user",
    "foreign_hidraw_reader_refused_and_acl_rolled_back",
    "grab_fdstore_acl_and_input_rpc_then_normal_release", "owner_socket_eof_cleanup",
    "owner_process_exit_cleanup", "permission_tampering_ends_lease", "named_acl_tampering_ends_lease",
    "sigkill_fdstore_import_shutdown_grab_release_and_other_device_preserved",
    "disconnect_does_not_reacquire_replacement",
}


def verify_result(result, token):
    if (result.get("result") != "passed" or result.get("cleanupError")
            or result.get("physicalDevices") is not False or result.get("radioInitialized") is not False
            or result.get("environment") != {"kind": "owned-qemu-guest", "instance": token}
            or type(result.get("retainedGrabSamples")) is not int or result["retainedGrabSamples"] < 1):
        raise RuntimeError("Native result lacks successful owned-guest scope or retained-grab evidence")
    cases = result.get("cases", [])
    if not isinstance(cases, list) or any(type(case) is not dict or case.get("passed") is not True for case in cases):
        raise RuntimeError("Native result contains a failed or malformed case")
    names = [case.get("name") for case in cases]
    if len(names) != len(set(names)) or not REQUIRED_CASES.issubset(names):
        raise RuntimeError("Native result is missing a required assertion or repeats case identities")


def prepare_payload(directory, probe_directory):
    directory.mkdir()
    platform = Path(__file__).resolve().parents[1]
    research = platform.parent / "research"
    shutil.copytree(platform, directory / "linux/platform",
                    ignore=shutil.ignore_patterns("bin", "obj", "__pycache__", "*.pyc"))
    target = directory / "linux/research"
    target.mkdir(parents=True)
    for name in ("ci_environment.py", "kernel_probe.py", "prepare_kernel.sh"):
        shutil.copy2(research / name, target / name)
    shutil.copytree(probe_directory, directory / "probe")
    files = {}
    for path in sorted(directory.rglob("*")):
        if path.is_file():
            files[path.relative_to(directory).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    (directory / "PAYLOAD-SHA256SUMS").write_text("".join(f"{digest}  {name}\n" for name, digest in files.items()))
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe-directory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    args = parser.parse_args()
    guest = OwnedQemu(args.output, args.work)
    provenance = guest.provenance
    try:
        source = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        provenance.update(experiment="linux-platform-broker", sourceCommit=source)
        probe = args.probe_directory.resolve()
        if not (probe / "MadSense.Linux.Probe").is_file():
            raise RuntimeError("Publish a self-contained Linux x64 neutral exporter first")
        payload = guest.work / "payload"
        hashes = prepare_payload(payload, probe)
        (guest.output / "payload-files.json").write_text(json.dumps(hashes, indent=2) + "\n")
        guest.boot()
        guest.upload(payload)
        guest.run("payload-check", "cd /home/ubuntu/madsense && sha256sum --check --strict PAYLOAD-SHA256SUMS")
        guest.run("probe-permissions", "chmod +x /home/ubuntu/madsense/probe/MadSense.Linux.Probe")
        remote = "bash /home/ubuntu/madsense/linux/platform/tests/run_broker_experiment.sh " + shlex.quote(guest.token)
        try:
            result = guest.run("guest-run", remote, timeout=900, required=False)
            provenance["guestExit"] = result.returncode
        except subprocess.TimeoutExpired:
            provenance["guestExit"] = 124
        finally:
            # Include preparation failures and partial native assertions too.
            for name in ("prepare.log", "usbip-binary.txt", "result"):
                guest.collect(name)
        result_path = guest.output / "result/result.json"
        if provenance["guestExit"] != 0 or not result_path.exists():
            raise RuntimeError("Native broker experiment failed; inspect guest-run, prepare and partial result logs")
        native = json.loads(result_path.read_text())
        verify_result(native, guest.token)
        provenance.update(result="passed", nativeCases=len(native["cases"]),
                          nativeResultSha256=hashlib.sha256(result_path.read_bytes()).hexdigest())
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
