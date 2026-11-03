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
                 "consumer_stop.py", "test_consumer_stop.py", "run_wine_variant.py", "test_wine_variant.py",
                 "consumer_waits.py", "test_consumer_waits.py"):
        shutil.copy2(research / name, target / name)
    for name in ("run_hidapi_windows.py", "hidapi_windows_evidence.py", "test_hidapi_windows_runtime.py"):
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
    parser.add_argument("--hidapi-artifact", type=Path, help="Optional separately built real Windows HIDAPI observer")
    parser.add_argument("--observe-audio-defaults", action="store_true")
    parser.add_argument("--observe-pipewire-defaults", action="store_true")
    parser.add_argument("--adjust-owned-rear-unity", action="store_true")
    parser.add_argument("--wine-udev-variant", action="store_true")
    parser.add_argument("--observe-kernel-waits", action="store_true")
    parser.add_argument("--ge-umu-cache", type=Path, help="Explicit private verified cache; requires Wine/UDEV; no download during actor")
    parser.add_argument("--guest-image", choices=("ubuntu", "debian13"), default="ubuntu",
                        help="Separate opt-in Debian experiment; default Ubuntu remains GPG verified")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    args = parser.parse_args()
    if args.ge_umu_cache is not None and not args.wine_udev_variant:
        parser.error("--ge-umu-cache requires --wine-udev-variant")
    if args.guest_image == "debian13":
        if args.ge_umu_cache is None:
            parser.error("--guest-image debian13 requires the explicit GE/UMU experiment")
        if args.observe_pipewire_defaults or args.adjust_owned_rear_unity:
            parser.error("The WirePlumber 0.4 observers do not cover Debian's WirePlumber 0.5")
        from debian_qemu import OwnedDebianQemu
        guest = OwnedDebianQemu(args.output, args.work)
    else:
        guest = OwnedQemu(args.output, args.work)
    provenance = guest.provenance
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        provenance.update(experiment="linux-production-core-consumers", sourceCommit=commit,
                          persona="dualsense", protonValidated=False,
                          audioDefaultsObservationRequested=args.observe_audio_defaults,
                          pipewireDefaultsObservationRequested=args.observe_pipewire_defaults,
                          pipewireGainExperimentRequested=args.adjust_owned_rear_unity,
                          configuredWineUdevVariantRequested=args.wine_udev_variant,
                          kernelWaitObservationRequested=args.observe_kernel_waits,
                          geUmuExperimentRequested=args.ge_umu_cache is not None)
        provenance["guestImageSelection"] = args.guest_image
        native = verify_build(args.native_artifact, ("consumer_sdl2", "consumer_sdl3", "ff_probe"), commit)
        windows = verify_build(args.windows_artifact, ("wine_consumer.exe",), commit)
        hidapi = verify_build(args.hidapi_artifact, ("hidapi_windows_consumer.exe",), commit) if args.hidapi_artifact else {}
        provenance["consumerBuildDigests"] = native | windows | hidapi
        provenance["realHidapiWindowsRequested"] = args.hidapi_artifact is not None
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
        if args.hidapi_artifact is not None:
            shutil.copy2(args.hidapi_artifact / "bin/hidapi_windows_consumer.exe", payload / "consumer-bin")
            target = guest.output / "hidapi-build"; target.mkdir()
            for name in ("provenance.txt", "binary-sha256.txt", "source-sha256.txt"):
                shutil.copy2(args.hidapi_artifact / name, target / name)
        stage_consumer_research(payload)
        if args.guest_image == "debian13":
            for name in ("prepare_debian_kernel.sh", "run_debian_broker_guest.sh", "ge_namespace_probe.py"):
                shutil.copy2(Path(__file__).parent / name, payload / "linux/research" / name)
        if args.ge_umu_cache is not None:
            from ge_umu_payload import stage as stage_ge
            provenance["geUmuPrivatePayload"] = stage_ge(args.ge_umu_cache, payload, guest.output)
            provenance["geUmuStagedBeforeGuestBoot"] = True
        hashes = {path.relative_to(payload).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                  for path in sorted(payload.rglob("*")) if path.is_file() and path.name != "PAYLOAD-SHA256SUMS"}
        (payload / "PAYLOAD-SHA256SUMS").write_text("".join(f"{digest}  {name}\n" for name, digest in hashes.items()))
        (guest.output / "payload-files.json").write_text(json.dumps(hashes, indent=2) + "\n")
        guest.boot(); guest.upload(payload)
        guest.run("payload-check", "cd /home/ubuntu/madsense && sha256sum --check --strict PAYLOAD-SHA256SUMS")
        if args.guest_image == "debian13":
            namespace = guest.run("namespace-preflight", "python3 /home/ubuntu/madsense/linux/research/ge_namespace_probe.py "
                "--owned-guest-token " + shlex.quote(guest.token), timeout=30, required=False)
            provenance["earlyNamespacePreflightExit"] = namespace.returncode
            if namespace.returncode:
                raise RuntimeError("Debian ordinary-user namespace preflight failed before broker/actor/GE; policy unchanged")
            observed = json.loads((guest.output / "namespace-preflight.stdout").read_text())
            if (observed.get("result") != "passed" or observed.get("ownedGuestToken") != guest.token
                    or observed.get("securityPolicyModified") is not False):
                raise RuntimeError("Missing same-guest namespace preflight evidence")
            provenance["earlyNamespacePreflightVerified"] = True
        guest.run("probe-permissions", "chmod +x /home/ubuntu/madsense/probe/MadSense.Linux.Probe")
        try:
            baseline_script = ("linux/research/run_debian_broker_guest.sh" if args.guest_image == "debian13"
                               else "linux/platform/tests/run_broker_experiment.sh")
            baseline = guest.run("baseline-run", "bash /home/ubuntu/madsense/" + baseline_script + " "
                                 + shlex.quote(guest.token), timeout=900, required=False)
            provenance["baselineExit"] = baseline.returncode
            if baseline.returncode == 0:
                consumer = guest.run("consumer-run", "bash /home/ubuntu/madsense/linux/research/run_consumer_guest.sh "
                                     + shlex.quote(guest.token)
                                     + (" --observe-audio-defaults" if args.observe_audio_defaults else "")
                                     + (" --observe-pipewire-defaults" if args.observe_pipewire_defaults else "")
                                     + (" --adjust-owned-rear-unity" if args.adjust_owned_rear_unity else "")
                                     + (" --wine-udev-variant" if args.wine_udev_variant else "")
                                     + (" --observe-kernel-waits" if args.observe_kernel_waits else "")
                                     + (" --ge-umu" if args.ge_umu_cache is not None else "")
                                     + (" --hidapi-windows" if args.hidapi_artifact is not None else ""),
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
        if args.hidapi_artifact is not None:
            from run_hidapi_windows import final_capture_summary
            stopped_path = guest.output / "result/core-experiment/core/stopped.json"
            stopped = json.loads(stopped_path.read_text()) if stopped_path.exists() else {}
            provenance["realHidapiWindows"] = {}
            for name in ("consumer-suite", "wine-udev-variant", "ge-umu-suite"):
                path = guest.output / "result/core-experiment" / name / "result.json"
                if path.exists():
                    record = json.loads(path.read_text())
                    provenance["realHidapiWindows"][name] = final_capture_summary(record, stopped, guest.token)
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
        ge_path = guest.output / "result/core-experiment/ge-umu-suite/result.json"
        if ge_path.exists():
            ge = json.loads(ge_path.read_text())
            provenance["geUmuExperiment"] = dict(result=ge.get("result"), scope=ge.get("scope"),
                officialValveProton=ge.get("officialValveProton"), runtimeCacheCollected=ge.get("runtimeCacheCollected"),
                resultSha256=hashlib.sha256(ge_path.read_bytes()).hexdigest(),
                cleanupVerified=ge.get("accountProcessesAndFilesRemoved") is True and not ge.get("cleanupErrors"))
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
        if args.ge_umu_cache is not None:
            from ge_umu_consumer import prerequisites
            from run_wine_variant import clean_result
            prerequisites(consumer_path, result["ready"])
            checked_ge = clean_result(ge_path, result["ready"])
            summary = result.get("geUmuExperiment", {})
            if (checked_ge.get("result") != "passed" or checked_ge.get("scope") != "configured-ge-proton-umu-consumer"
                    or checked_ge.get("officialValveProton") is not False
                    or checked_ge.get("runtimeCacheCollected") is not False
                    or checked_ge.get("geUmuSession", {}).get("consumerAssertionsPassed") is not True
                    or summary.get("cleanupVerified") is not True
                    or summary.get("resultSha256") != hashlib.sha256(ge_path.read_bytes()).hexdigest()):
                raise RuntimeError("Requested GE/UMU experiment lacks complete matching assertions and cleanup")
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
