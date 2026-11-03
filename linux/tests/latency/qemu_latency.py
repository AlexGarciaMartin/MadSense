#!/usr/bin/env python3
"""Optional timing after real functional gates in one owned QEMU instance."""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

from analyze import summarize
from gates import verify_directory

LINUX = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(LINUX / "platform/integration"))
from qemu_core import OwnedQemu, prepare_combined_payload, run_stages

RESULTS = ("measured", "generator-limit", "transport-limit", "no-samples")


def require(value, message):
    if not value:
        raise RuntimeError(message)


def add_latency_payload(payload):
    """Also usable by an existing live-VM owner, before its one payload upload."""
    shutil.copytree(Path(__file__).resolve().parent, payload / "linux/tests/latency",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    hashes = {path.relative_to(payload).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in sorted(payload.rglob("*")) if path.is_file() and path.name != "PAYLOAD-SHA256SUMS"}
    (payload / "PAYLOAD-SHA256SUMS").write_text("".join(f"{digest}  {name}\n" for name, digest in hashes.items()))
    return hashes


def run_latency_stage(guest):
    """No boot or baseline here: attach this stage to an already gated live VM."""
    require(all(guest.provenance.get(stage + "Exit") == 0 for stage in ("baseline", "core", "app")),
            "All functional stages must finish before optional timing")
    guest.provenance["latencyStarted"] = True
    try:
        answer = guest.run("latency-run", "bash /home/ubuntu/madsense/linux/tests/latency/run_latency_experiment.sh "
                           + shlex.quote(guest.token), timeout=480, required=False)
    except subprocess.TimeoutExpired:
        guest.provenance["latencyExit"] = 124
        raise
    guest.provenance["latencyExit"] = answer.returncode
    return answer.returncode


def verify_measurement(directory, token, functional_gates):
    """Recompute collected CSV summaries, keeping functional and timing outcomes separate."""
    directory = Path(directory)
    result = json.loads((directory / "result.json").read_text())
    require(result.get("schema") == 1 and result.get("result") in RESULTS
            and result.get("environment") == dict(kind="owned-qemu-guest", instance=token)
            and result.get("functionalGate") is False and result.get("physicalDevices") is False
            and result.get("radioInitialized") is False and result.get("physicalLatencyValidated") is False
            and result.get("applicationControllerExercised") is True and result.get("applicationUiExercised") is False,
            "Unexpected timing result scope or classification")
    require(result.get("functionalGates") == functional_gates, "Timing used different functional evidence")
    cases = result.get("cases", [])
    require(len(cases) == 2 and [case.get("rateHz") for case in cases] == [100, 1000], "Missing or repeated smoke rate")
    for case in cases:
        path = directory / f"{case['rateHz']}hz"
        require(case == json.loads((path / "result.json").read_text()), "Top-level/case timing results differ")
        require(case.get("cleanupConfirmed") is True and not case.get("cleanupError") and case.get("result") in RESULTS,
                "Case has invalid evidence or unconfirmed cleanup")
        probe = json.loads((path / "probe.json").read_text())
        with (path / "samples.csv").open(newline="") as stream:
            actual = summarize(stream, probe)
        for name, value in actual.items():
            if name not in ("result", "cleanupConfirmed"):
                require(case.get(name) == value, f"Collected timing summary differs from CSV: {name}")
        overflows = case["metricsAfter"]["metrics"]["InputOverflows"]
        expected = "transport-limit" if overflows > 0 else actual["result"]
        require(case["result"] == expected, "Timing result hides a generator or transport limit")
        require(json.loads((path / "app/stopped.json").read_text()).get("cleanupConfirmed") is True,
                "Application's fresh helper preflight did not confirm cleanup")
        original = json.loads((path / "fixture-before.json").read_text())["nodes"]
        restored = json.loads((path / "fixture-restored.json").read_text())
        require(original and original == restored, "Fixture permissions/identities differ after timing")
    aggregate = next((value for value in ("no-samples", "transport-limit", "generator-limit")
                      if any(case["result"] == value for case in cases)), "measured")
    require(result["result"] == aggregate, "Aggregate result hides a measured limit")
    return result


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
    provenance.update(experiment="optional-synthetic-input-timing", functionalResult="not-validated", benchmarkResult="not-run")
    try:
        provenance["sourceCommit"] = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        payload = guest.work / "payload"
        prepare_combined_payload(payload, args.neutral_probe_directory.resolve(), args.core_probe_directory.resolve(), args.app_probe_directory.resolve())
        hashes = add_latency_payload(payload)
        (guest.output / "payload-files.json").write_text(json.dumps(hashes, indent=2) + "\n")
        guest.boot(); guest.upload(payload)
        guest.run("payload-check", "cd /home/ubuntu/madsense && sha256sum --check --strict PAYLOAD-SHA256SUMS")
        guest.run("neutral-executable", "chmod +x /home/ubuntu/madsense/probe/MadSense.Linux.Probe")
        try:
            run_stages(guest)  # Each existing baseline/core/app stage runs once.
            run_latency_stage(guest)  # Guest verifies actual gate JSON before mutation.
        finally:
            # Collect once: scp into an already-existing result directory would
            # otherwise nest a second result/ tree and obscure the latest files.
            for name in ("prepare.log", "usbip-binary.txt", "result"):
                guest.collect(name)
        gate_records = verify_directory(guest.output / "result", guest.token)
        provenance.update(functionalResult="passed", functionalGates=gate_records)
        require(provenance.get("latencyExit") == 0, "Optional timing failed; functional results remain separate")
        measurement = verify_measurement(guest.output / "result/latency-experiment", guest.token, gate_records)
        provenance.update(result="passed", benchmarkResult=measurement["result"],
            latencyResultSha256=hashlib.sha256((guest.output / "result/latency-experiment/result.json").read_bytes()).hexdigest())
    except BaseException as error:
        provenance["reason"] = f"{type(error).__name__}: {error}"
        if provenance["functionalResult"] == "not-validated":
            # Preserve a valid functional result even if timing/collection threw.
            try:
                require(all(provenance.get(stage + "Exit") == 0 for stage in ("baseline", "core", "app")), "Functional stage failed")
                provenance.update(functionalResult="passed", functionalGates=verify_directory(guest.output / "result", guest.token))
            except BaseException:
                provenance["functionalResult"] = "failed"
        if provenance.get("latencyStarted"):
            provenance["benchmarkResult"] = "invalid"
    finally:
        try:
            guest.close()  # Reap owned QEMU before restoring its temporary KVM ACL.
        except BaseException as error:
            provenance.update(result="failed", cleanupError=f"{type(error).__name__}: {error}")
        (guest.output / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
        print(json.dumps(provenance), flush=True)
    return 0 if provenance["result"] == "passed" and provenance["ownedQemuStopped"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
