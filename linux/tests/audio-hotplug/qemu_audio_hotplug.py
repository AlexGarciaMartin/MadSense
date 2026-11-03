#!/usr/bin/env python3
"""Opt-in wrapper, or payload/stage helpers for a previously gated owned VM."""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

from run_guest import verify_gates, verify_result, LINUX, require


def add_payload(payload):
    payload = Path(payload)
    shutil.copytree(Path(__file__).resolve().parent, payload / "linux/tests/audio-hotplug",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "bin", "obj"))
    target = payload / "linux/research"; target.mkdir(parents=True, exist_ok=True)
    for name in ("audio_defaults.py", "pipewire_defaults.py", "consumer_session.py", "consumer_files.py", "run_consumers.py"):
        shutil.copy2(LINUX / "research" / name, target / name)
    target = payload / "linux/platform"; target.mkdir(parents=True, exist_ok=True)
    for name in ("broker.py", "broker_core.py"):
        shutil.copy2(LINUX / "platform" / name, target / name)
    hashes = {path.relative_to(payload).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in sorted(payload.rglob("*")) if path.is_file() and path.name != "PAYLOAD-SHA256SUMS"}
    (payload / "PAYLOAD-SHA256SUMS").write_text("".join(f"{digest}  {name}\n" for name, digest in hashes.items()))
    return hashes


def run_stage(guest):
    require(all(guest.provenance.get(name+"Exit") == 0 for name in ("baseline", "core", "app")),
            "Existing functional stages must pass before optional audio arrival")
    guest.provenance["audioHotplugStarted"] = True
    response = guest.run("audio-hotplug", "bash /home/ubuntu/madsense/linux/tests/audio-hotplug/run_experiment.sh "
        + shlex.quote(guest.token), timeout=660, required=False)
    guest.provenance["audioHotplugExit"] = response.returncode


def run_policy_stage(guest):
    require(all(guest.provenance.get(name+"Exit") == 0 for name in ("baseline", "core", "app", "audioHotplug")),
            "Policy sessions require the complete original hotplug baseline")
    guest.provenance["audioPolicyStarted"] = True
    response = guest.run("audio-hotplug-policy", "sudo -n python3 /home/ubuntu/madsense/linux/tests/audio-hotplug/run_policy_guest.py "
        "--owned-guest-token " + shlex.quote(guest.token)
        + " --functional-results /home/ubuntu/madsense/result"
        " --baseline-results /home/ubuntu/madsense/result/audio-hotplug"
        " --probe-directory /home/ubuntu/madsense/app-probe"
        " --output /home/ubuntu/madsense/result/audio-hotplug-policy", timeout=660, required=False)
    guest.provenance["audioPolicyExit"] = response.returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-private-audio-hotplug", action="store_true")
    for name in ("neutral-probe-directory", "core-probe-directory", "app-probe-directory", "output", "work"):
        parser.add_argument("--"+name, type=Path, required=True)
    args = parser.parse_args()
    if not args.run_private_audio_hotplug: parser.error("Explicit --run-private-audio-hotplug is required")
    from qemu_core import OwnedQemu, prepare_combined_payload, run_stages
    guest = OwnedQemu(args.output, args.work); provenance = guest.provenance
    provenance.update(experiment="private-audio-hotplug", functionalResult="not-validated", audioHotplugResult="not-run",
                      audioPolicyResult="not-run")
    try:
        provenance["sourceCommit"] = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        payload = guest.work / "payload"
        prepare_combined_payload(payload, args.neutral_probe_directory.resolve(), args.core_probe_directory.resolve(), args.app_probe_directory.resolve())
        (guest.output / "payload-files.json").write_text(json.dumps(add_payload(payload), indent=2)+"\n")
        guest.boot(); guest.upload(payload)
        guest.run("payload-check", "cd /home/ubuntu/madsense && sha256sum --check --strict PAYLOAD-SHA256SUMS")
        guest.run("neutral-executable", "chmod +x /home/ubuntu/madsense/probe/MadSense.Linux.Probe")
        try:
            run_stages(guest)
            run_stage(guest)
            if provenance.get("audioHotplugExit") == 0:
                run_policy_stage(guest)
        finally:
            for name in ("prepare.log", "usbip-binary.txt", "result"): guest.collect(name)
        gates = verify_gates(guest.output / "result", guest.token)
        provenance.update(functionalResult="passed", functionalGates=gates)
        require(provenance.get("audioHotplugExit") == 0, "Audio observation invalid; functional evidence is separate")
        result = verify_result(guest.output / "result/audio-hotplug", guest.token, gates)
        provenance.update(audioHotplugResult=result["result"],
            defaultPreservationByCase={row["selection"]:row["defaultPreservationObserved"] for row in result["cases"]},
            audioHotplugResultSha256=hashlib.sha256((guest.output / "result/audio-hotplug/result.json").read_bytes()).hexdigest())
        from run_policy_guest import verify_result as verify_policy
        policy = verify_policy(guest.output / "result/audio-hotplug-policy", guest.token, gates, guest.output / "result/audio-hotplug")
        provenance.update(audioPolicyResult=policy["result"], audioPolicyExpectationsMet=policy["expectationsMet"],
            audioPolicyResultSha256=hashlib.sha256((guest.output / "result/audio-hotplug-policy/result.json").read_bytes()).hexdigest())
        require(provenance.get("audioPolicyExit") == 0 and policy["expectationsMet"], "Private policy did not meet its scoped expectations")
        provenance["result"] = "passed"
    except BaseException as error:
        provenance["reason"] = f"{type(error).__name__}: {error}"
        if provenance.get("audioHotplugStarted") and provenance["audioHotplugResult"] != "observed":
            provenance["audioHotplugResult"] = "invalid"
        if provenance.get("audioPolicyStarted") and provenance["audioPolicyResult"] != "observed":
            provenance["audioPolicyResult"] = "invalid"
        try:
            provenance.update(functionalResult="passed", functionalGates=verify_gates(guest.output / "result", guest.token))
        except BaseException: pass
    finally:
        try: guest.close()
        except BaseException as error: provenance.update(result="failed", cleanupError=str(error))
        (guest.output / "provenance.json").write_text(json.dumps(provenance, indent=2)+"\n")
        print(json.dumps(provenance), flush=True)
    return 0 if provenance["result"] == "passed" and provenance["ownedQemuStopped"] else 1


if __name__ == "__main__": raise SystemExit(main())
