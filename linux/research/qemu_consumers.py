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


def stage_consumer_research(payload):
    research = Path(__file__).resolve().parent
    target = Path(payload) / "linux/research"
    target.mkdir(parents=True, exist_ok=True)
    for name in ("host_probe.c", "run_consumers.py", "consumer_session.py", "run_wine_consumer.py",
                 "test_consumer_session.py", "qemu_consumers.py", "run_consumer_guest.sh",
                 "audio_defaults.py", "test_audio_defaults.py", "pipewire_defaults.py", "test_pipewire_defaults.py",
                 "pipewire_gain.py", "test_pipewire_gain.py",
                 "consumer_phases.py", "test_consumer_phases.py", "consumer_files.py", "test_consumer_files.py",
                 "consumer_stop.py", "test_consumer_stop.py", "run_wine_variant.py", "test_wine_variant.py"):
        shutil.copy2(research / name, target / name)


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
    parser.add_argument("--observe-audio-defaults", action="store_true")
    parser.add_argument("--observe-pipewire-defaults", action="store_true")
    parser.add_argument("--adjust-owned-rear-unity", action="store_true")
    parser.add_argument("--wine-udev-variant", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    args = parser.parse_args()
    guest = OwnedQemu(args.output, args.work)
    provenance = guest.provenance
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        provenance.update(experiment="linux-production-core-consumers", sourceCommit=commit,
                          persona="dualsense", protonValidated=False,
                          audioDefaultsObservationRequested=args.observe_audio_defaults,
                          pipewireDefaultsObservationRequested=args.observe_pipewire_defaults,
                          pipewireGainExperimentRequested=args.adjust_owned_rear_unity,
                          configuredWineUdevVariantRequested=args.wine_udev_variant)
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
        stage_consumer_research(payload)
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
                                     + shlex.quote(guest.token)
                                     + (" --observe-audio-defaults" if args.observe_audio_defaults else "")
                                     + (" --observe-pipewire-defaults" if args.observe_pipewire_defaults else "")
                                     + (" --adjust-owned-rear-unity" if args.adjust_owned_rear_unity else "")
                                     + (" --wine-udev-variant" if args.wine_udev_variant else ""),
                                     timeout=900, required=False)
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
        observation_path = guest.output / "result/core-experiment/audio-defaults/result.json"
        if observation_path.exists():
            observation = json.loads(observation_path.read_text())
            provenance["audioDefaultsObservationResult"] = observation.get("result")
            provenance["audioDefaultsCleanupVerified"] = (observation.get("accountProcessesAndFilesRemoved") is True
                                                          and not observation.get("cleanupErrors"))
        pipewire_path = guest.output / "result/core-experiment/pipewire-defaults/result.json"
        variant_path = guest.output / "result/core-experiment/wine-udev-variant/result.json"
        if variant_path.exists():
            variant = json.loads(variant_path.read_text())
            provenance["configuredWineUdevVariant"] = dict(result=variant.get("result"),
                scope=variant.get("scope"), defaultWinebus=variant.get("defaultWinebus"),
                resultSha256=hashlib.sha256(variant_path.read_bytes()).hexdigest(),
                cleanupVerified=variant.get("accountProcessesAndFilesRemoved") is True and not variant.get("cleanupErrors"))
        if pipewire_path.exists():
            pipewire_observation = json.loads(pipewire_path.read_text())
            provenance["pipewireDefaultsObservationResult"] = pipewire_observation.get("result")
            provenance["pipewireDefaultsCleanupVerified"] = (pipewire_observation.get("accountProcessesAndFilesRemoved") is True
                                                             and not pipewire_observation.get("cleanupErrors"))
        gain_summary = None
        if args.adjust_owned_rear_unity:
            # Preserve independently verified gain evidence even if a later
            # SDL/Wine assertion fails. No success is inferred from a filename.
            from pipewire_gain import optional_result as gain_result
            ready_path = guest.output / "result/core-experiment/consumer-ready.json"
            ready = json.loads(ready_path.read_text())
            if ready.get("ownedGuestToken") != guest.token:
                raise RuntimeError("Gain experiment contract has a different guest owner")
            gain_summary = gain_result(guest.output / "result/core-experiment/pipewire-gain/result.json", ready)
            provenance["pipewireGainExperiment"] = gain_summary
        if provenance.get("consumerExit") != 0 or not core_path.exists() or not consumer_path.exists():
            raise RuntimeError("Core/consumer execution failed; inspect retained partial logs and results")
        verify_core_result(json.loads(core_path.read_text()), guest.token)
        result = json.loads(consumer_path.read_text())
        verify_consumer_result(result)
        if gain_summary is not None:
            retained = result.get("pipewireGainExperiment", {})
            if any(retained.get(key) != value for key, value in gain_summary.items()):
                raise RuntimeError("Requested gain experiment differs from its retained cleanup evidence")
        if args.wine_udev_variant:
            from run_wine_variant import clean_result
            checked_variant = clean_result(variant_path, result["ready"])
            summary = result.get("wineUdevVariant", {})
            if (checked_variant.get("result") != "passed" or checked_variant.get("scope") != "configured-distribution-wine-udev"
                    or checked_variant.get("defaultWinebus") is not False or summary.get("cleanupVerified") is not True
                    or summary.get("resultSha256") != hashlib.sha256(variant_path.read_bytes()).hexdigest()):
                raise RuntimeError("Requested configured Wine diagnostic lacks matching success and cleanup evidence")
        if args.observe_audio_defaults:
            observation = result.get("audioDefaultsObservation")
            if not observation or observation.get("cleanupVerified") is not True:
                raise RuntimeError("Requested audio-default observation lacks verified cleanup")
            provenance["audioDefaultsObservationResult"] = observation.get("result")
        if args.observe_pipewire_defaults:
            from pipewire_defaults import optional_result
            ready = result.get("ready", {})
            if ready.get("ownedGuestToken") != guest.token:
                raise RuntimeError("PipeWire observer contract has a different guest owner")
            checked = optional_result(pipewire_path, ready)
            observation = result.get("pipewireDefaultsObservation", {})
            if any(observation.get(key) != checked[key] for key in
                   ("result", "cleanupVerified", "resultSha256", "gatesConsumerCompatibility", "defaultDesktopStackValidated")):
                raise RuntimeError("Requested PipeWire observation differs from its retained cleanup evidence")
            provenance["pipewireDefaultsObservationResult"] = checked["result"]
            provenance["pipewireDefaultsResultSha256"] = checked["resultSha256"]
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
