#!/usr/bin/env python3
"""Execute same-commit SDL/Wine observers in a separate owned core guest."""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

def verify_build(directory, names, commit):
    provenance = (directory / "provenance.txt").read_text()
    if f"MadSense commit: {commit}" not in provenance.splitlines() or "Compilation: passed" not in provenance.splitlines():
        raise RuntimeError("Consumer artifact is not a successful build from this exact source commit")
    expected = {}
    for line in (directory / "binary-sha256.txt").read_text().splitlines():
        digest, filename = line.split(maxsplit=1)
        name = Path(filename.strip().lstrip("*")).name
        if name in expected or len(digest) != 64:
            raise RuntimeError("Malformed or duplicate consumer artifact digest")
        expected[name] = digest
    if set(expected) != set(names):
        raise RuntimeError("Consumer artifact has an unexpected binary set")
    for name, digest in expected.items():
        if hashlib.sha256((directory / "bin" / name).read_bytes()).hexdigest() != digest:
            raise RuntimeError("Consumer binary differs from its compilation artifact: " + name)
    return expected


def verify_consumer_result(result):
    expected = {f"consumer_sdl{major}-{state}" for major in (2, 3) for state in ("south", "neutral")}
    cases = result.get("cases", [])
    if (result.get("result") != "passed" or result.get("accountProcessesAndFilesRemoved") is not True
            or result.get("cleanupErrors") or result.get("wineRequested") is not True
            or result.get("physicalDevices") is not False or result.get("protonValidated") is not False
            or len(cases) != len(expected) or {case.get("name") for case in cases} != expected
            or any(case.get("passed") is not True for case in cases)):
        raise RuntimeError("Consumer result lacks complete input/output and cleanup assertions")
    wine = result.get("wineSession", {})
    cases = wine.get("cases", [])
    if (wine.get("kind") != "distribution-wine" or wine.get("defaultWinebus") is not True
            or wine.get("protonValidated") is not False or len(cases) != 3
            or {case.get("name") for case in cases} != {"wine-sony-south", "wine-sony-neutral", "wine-wasapi"}
            or any(case.get("passed") is not True for case in cases)):
        raise RuntimeError("Wine result lacks the complete default-runtime Sony/WASAPI assertions")


def main():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "platform/tests"))
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "platform/integration"))
    from owned_qemu import OwnedQemu
    from qemu_broker import prepare_payload, verify_result
    from qemu_core import verify_core_result
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--neutral-probe-directory", type=Path, required=True)
    parser.add_argument("--core-probe-directory", type=Path, required=True)
    parser.add_argument("--native-artifact", type=Path, required=True)
    parser.add_argument("--windows-artifact", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    args = parser.parse_args()
    guest = OwnedQemu(args.output, args.work)
    provenance = guest.provenance
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        provenance.update(experiment="linux-production-core-consumers", sourceCommit=commit,
                          persona="dualsense", protonValidated=False)
        native = verify_build(args.native_artifact, ("consumer_sdl2", "consumer_sdl3", "ff_probe"), commit)
        windows = verify_build(args.windows_artifact, ("wine_consumer.exe",), commit)
        provenance["consumerBuildDigests"] = native | windows
        for name, source in (("native-build", args.native_artifact), ("windows-build", args.windows_artifact)):
            target = guest.output / name
            target.mkdir()
            for file in ("provenance.txt", "binary-sha256.txt"):
                shutil.copy2(source / file, target / file)
        payload = guest.work / "payload"
        prepare_payload(payload, args.neutral_probe_directory.resolve())
        if not (args.core_probe_directory / "MadSense.Linux.CoreProbe").is_file():
            raise RuntimeError("Publish the self-contained production core actor first")
        shutil.copytree(args.core_probe_directory, payload / "core-probe")
        shutil.copytree(args.native_artifact / "bin", payload / "consumer-bin")
        shutil.copy2(args.windows_artifact / "bin/wine_consumer.exe", payload / "consumer-bin")
        research = Path(__file__).resolve().parent
        for name in ("host_probe.c", "run_consumers.py", "consumer_session.py", "run_wine_consumer.py",
                     "test_consumer_session.py", "qemu_consumers.py", "run_consumer_guest.sh"):
            shutil.copy2(research / name, payload / "linux/research" / name)
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
            if baseline.returncode == 0:
                consumer = guest.run("consumer-run", "bash /home/ubuntu/madsense/linux/research/run_consumer_guest.sh "
                                     + shlex.quote(guest.token), timeout=900, required=False)
                provenance["consumerExit"] = consumer.returncode
        finally:
            for name in ("prepare.log", "usbip-binary.txt", "result"):
                guest.collect(name)
        baseline_path = guest.output / "result/result.json"
        if provenance.get("baselineExit") != 0 or not baseline_path.exists():
            raise RuntimeError("Broker baseline failed before any consumer execution")
        verify_result(json.loads(baseline_path.read_text()), guest.token)
        core_path = guest.output / "result/core-experiment/result.json"
        consumer_path = guest.output / "result/core-experiment/consumer-suite/result.json"
        if provenance.get("consumerExit") != 0 or not core_path.exists() or not consumer_path.exists():
            raise RuntimeError("Core/consumer execution failed; inspect retained partial logs and results")
        verify_core_result(json.loads(core_path.read_text()), guest.token)
        verify_consumer_result(json.loads(consumer_path.read_text()))
        provenance.update(result="passed", coreResultSha256=hashlib.sha256(core_path.read_bytes()).hexdigest(),
                          consumerResultSha256=hashlib.sha256(consumer_path.read_bytes()).hexdigest())
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
