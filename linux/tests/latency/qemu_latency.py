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
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "bin", "obj"))
    actor_helper = payload / "linux/tests/audio-hotplug"
    actor_helper.mkdir(parents=True, exist_ok=True)
    shutil.copy2(LINUX / "tests/audio-hotplug/actor_files.py", actor_helper / "actor_files.py")
    hashes = {path.relative_to(payload).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in sorted(payload.rglob("*")) if path.is_file() and path.name != "PAYLOAD-SHA256SUMS"}
    (payload / "PAYLOAD-SHA256SUMS").write_text("".join(f"{digest}  {name}\n" for name, digest in hashes.items()))
    return hashes


def run_latency_stage(guest):
    """No boot or baseline here: attach this stage to an already gated live VM."""
    require(all(guest.provenance.get(stage + "Exit") == 0 for stage in ("baseline", "core", "app")),
            "All functional stages must finish before optional timing")
    if guest.provenance.get("helperObservationRequested"):
        require(guest.provenance.get("traceExperimentRequested") is True
                and guest.provenance.get("traceSourceOverlay", {}).get("traceKind") == "input",
                "Helper observation requires the matching input-only comparison")
    guest.provenance["latencyStarted"] = True
    try:
        answer = guest.run("latency-run", "bash /home/ubuntu/madsense/linux/tests/latency/run_latency_experiment.sh "
                           + shlex.quote(guest.token)
                           + (" --helper" if guest.provenance.get("helperObservationRequested") else
                              " --trace" if guest.provenance.get("traceExperimentRequested") else ""),
                           timeout=900 if guest.provenance.get("helperObservationRequested") else 480, required=False)
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
    if "attributionExperiment" in result:
        from trace_schedule import schedule
        from trace_variant import kind
        experiment = result["attributionExperiment"]
        overlay = experiment.get("sourceOverlay", {})
        expected_cases = schedule(kind(overlay))
        require(experiment.get("kind") == "paired-control-and-instrumented-copy"
                and experiment.get("productionTuning") is False
                and experiment.get("cases") == [name for name, _, _ in expected_cases], "Unexpected paired schedule")
        require(overlay.get("schema") == 1 and overlay.get("productionBinary") is False
                and overlay.get("kind") == "instrumented-research-copy", "Invalid source overlay provenance")
        require(len(cases) == len(expected_cases), "Missing paired attribution cases")
    else:
        expected_cases = [(f"{rate}hz", rate, False) for rate in (100, 1000)]
        require(len(cases) == 2, "Missing or repeated smoke rate")
    for case, (name, rate, instrumented) in zip(cases, expected_cases):
        require(case.get("rateHz") == rate, "Rate differs from the declared schedule")
        path = directory / name
        if "attributionExperiment" in result:
            require(case.get("caseName") == name and case.get("instrumented") is instrumented,
                    "Control/trace label differs from schedule")
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
        if instrumented:
            from trace_variant import analyze_directory
            with (path / "samples.csv").open(newline="") as samples:
                attribution = analyze_directory(path / "app", overlay, samples, probe)
            require(attribution == case.get("attribution"), "Attribution differs from collected raw trace/CSV")
        else:
            from trace_variant import require_control
            require("attribution" not in case, "Instrumented evidence labelled as control")
            require_control(path / "app")
    aggregate = next((value for value in ("no-samples", "transport-limit", "generator-limit")
                      if any(case["result"] == value for case in cases)), "measured")
    require(result["result"] == aggregate, "Aggregate result hides a measured limit")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--neutral-probe-directory", type=Path, required=True)
    parser.add_argument("--core-probe-directory", type=Path, required=True)
    parser.add_argument("--app-probe-directory", type=Path, required=True)
    parser.add_argument("--trace-probe-directory", type=Path)
    parser.add_argument("--helper-probe-directory", type=Path)
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
        if args.trace_probe_directory is not None:
            source = args.trace_probe_directory.resolve()
            overlay = json.loads((source / "research-trace-overlay.json").read_text())
            from trace_variant import kind
            kind(overlay)  # Reject an unknown variant before boot or mutation.
            require(overlay.get("sourceCommit") == provenance["sourceCommit"]
                    and overlay.get("productionBinary") is False
                    and overlay.get("kind") == "instrumented-research-copy", "Overlay source differs from normal actors")
            require(source != args.app_probe_directory.resolve(), "Trace cannot replace the production control")
            shutil.copytree(source, payload / "app-probe-trace")
            provenance.update(traceExperimentRequested=True, traceSourceOverlay=overlay)
        if args.helper_probe_directory is not None:
            require(args.trace_probe_directory is not None and provenance["traceSourceOverlay"].get("traceKind") == "input",
                    "Helper observation requires the input-only actor")
            from stage_helper_trace import verify_overlay
            source = args.helper_probe_directory.resolve()
            helper_overlay = verify_overlay(source, provenance["sourceCommit"])
            shutil.copytree(source, payload / "helper-probe")
            provenance.update(helperObservationRequested=True, helperSourceOverlay=helper_overlay)
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
        if args.trace_probe_directory is not None:
            require(measurement.get("attributionExperiment", {}).get("sourceOverlay") == provenance["traceSourceOverlay"],
                    "Guest did not run the requested trace source overlay")
        else:
            require("attributionExperiment" not in measurement, "Unexpected source instrumentation")
        if args.helper_probe_directory is not None:
            from run_helper_latency_guest import verify_observation
            observation = verify_observation(guest.output / "result/helper-observation",
                guest.output / "result/latency-experiment", guest.token, gate_records, provenance["helperSourceOverlay"])
            provenance["helperObservation"] = dict(result=observation["result"], originalHelperRestored=True,
                observedCoreResultSha256=observation["observedCoreResultSha256"],
                resultSha256=hashlib.sha256((guest.output / "result/helper-observation/result.json").read_bytes()).hexdigest())
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
