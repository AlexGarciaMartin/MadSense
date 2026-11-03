#!/usr/bin/env python3
"""Observe a copied helper after original gates, with a separate real core cycle."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from gates import verify_directory, verify_core_result
from helper_override import HelperOverride
from helper_trace_analysis import analyze_case

ROOT = Path(__file__).resolve().parents[3]


def require(value, message):
    if not value:
        raise RuntimeError(message)


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def run_child(output, label, argv, timeout):
    with (output / (label + ".stdout")).open("wb") as stdout, (output / (label + ".stderr")).open("wb") as stderr:
        value = subprocess.run(argv, stdout=stdout, stderr=stderr, timeout=timeout)
    require(value.returncode == 0, label + " failed; original child evidence is retained")


def verify_observation(directory, timing_directory, token, gates, overlay):
    directory, timing_directory = Path(directory), Path(timing_directory)
    result = json.loads((directory / "result.json").read_text())
    require(result.get("schema") == 1 and result.get("result") == "observed"
            and result.get("environment") == dict(kind="owned-qemu-guest", instance=token)
            and result.get("functionalGate") is False and result.get("productionTuning") is False
            and result.get("originalFunctionalGates") == gates and result.get("sourceOverlay") == overlay,
            "Helper observation scope/source/gates differ")
    service = json.loads((directory / "service/service.json").read_text())
    require(result.get("serviceSha256") == hashlib.sha256((directory / "service/service.json").read_bytes()).hexdigest()
            and service.get("sourceOverlay") == overlay and service.get("originalServiceRestored") is True
            and service.get("environment") == result["environment"]
            and service.get("copiedDirectoryRemoved") is True and service.get("cleanupErrors") == []
            and service.get("originalFunctionalGatesUseOriginalHelper") is True
            and service.get("timingUsesObservedHelper") is True
            and service.get("observedHelperCoreCyclePassed") is True, "Observed helper cleanup/core coverage is unconfirmed")
    core = directory / "core-cycle/result.json"
    verify_core_result(json.loads(core.read_text()), token)
    require(result.get("observedCoreResultSha256") == hashlib.sha256(core.read_bytes()).hexdigest(),
            "Observed helper core-cycle bytes differ")
    measurement = json.loads((timing_directory / "result.json").read_text())
    expected_names = [case["caseName"] for case in measurement["cases"]]
    from trace_schedule import schedule
    require(expected_names == [name for name, _, _ in schedule("input")], "Unexpected helper observation schedule")
    require(set(result.get("cases", {})) == set(expected_names), "Missing helper timing case")
    for name in expected_names:
        require(result["cases"][name] == analyze_case(timing_directory / name, service["observedProcess"]),
                "Helper durations differ from collected journal/source input")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--owned-guest-token", required=True)
    parser.add_argument("--helper-probe-directory", type=Path, required=True)
    parser.add_argument("--trace-probe-directory", type=Path, required=True)
    args = parser.parse_args()
    from capture_diagnostics import _owned_guest
    scope = _owned_guest(args.owned_guest_token)
    gates = verify_directory(ROOT / "result", args.owned_guest_token)
    output = ROOT / "result/helper-observation"
    output.mkdir(mode=0o755)
    output.chmod(0o755)
    observation = dict(schema=1, result="invalid", environment=scope, originalFunctionalGates=gates,
                       functionalGate=False, productionTuning=False, cases={})
    context = None
    try:
        context = HelperOverride(args.helper_probe_directory, output / "service", args.owned_guest_token)
        input_overlay = json.loads((args.trace_probe_directory / "research-trace-overlay.json").read_text())
        require(input_overlay.get("sourceCommit") == context.overlay["sourceCommit"]
                and input_overlay.get("traceKind") == "input", "Helper observation requires the matching input-only actor")
        observation["sourceOverlay"] = context.overlay
        context.start()
        # These nine real cases are distinct from the previously completed
        # original-helper gates. No timing threshold or new compatibility claim.
        run_child(output, "observed-core", [sys.executable, str(ROOT / "linux/platform/integration/run_core_guest.py"),
            "--owned-guest-token", args.owned_guest_token, "--probe-directory", str(ROOT / "core-probe"),
            "--host-probe", str(ROOT / "host_probe"), "--output", str(output / "core-cycle")], timeout=300)
        core_path = output / "core-cycle/result.json"
        verify_core_result(json.loads(core_path.read_text()), args.owned_guest_token)
        context.wait_idle()
        context.record["observedHelperCoreCyclePassed"] = True
        context.save()
        observation["observedCoreResultSha256"] = hashlib.sha256(core_path.read_bytes()).hexdigest()
        run_child(output, "observed-timing", [sys.executable, str(ROOT / "linux/tests/latency/run_latency_guest.py"),
            "--owned-guest-token", args.owned_guest_token,
            "--baseline-result", str(ROOT / "result/result.json"),
            "--core-result", str(ROOT / "result/core-experiment/result.json"),
            "--app-result", str(ROOT / "result/app-experiment/result.json"),
            "--upgrade-result", str(ROOT / "result/idle-upgrade/result.json"),
            "--probe-directory", str(ROOT / "app-probe"),
            "--trace-probe-directory", str(args.trace_probe_directory),
            "--output", str(ROOT / "result/latency-experiment")], timeout=480)
        timing = json.loads((ROOT / "result/latency-experiment/result.json").read_text())
        for case in timing["cases"]:
            observation["cases"][case["caseName"]] = analyze_case(ROOT / "result/latency-experiment" / case["caseName"],
                                                                  context.record["observedProcess"])
        observation["result"] = "observed"
    except BaseException as error:
        observation["reason"] = f"{type(error).__name__}: {error}"
    finally:
        if context is not None:
            try:
                context.close()
            except BaseException as error:
                observation.update(result="invalid", cleanupError=f"{type(error).__name__}: {error}")
            observation["serviceSha256"] = hashlib.sha256((output / "service/service.json").read_bytes()).hexdigest()
        save(output / "result.json", observation)
    if observation["result"] == "observed":
        verify_observation(output, ROOT / "result/latency-experiment", args.owned_guest_token, gates, context.overlay)
    return 0 if observation["result"] == "observed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
