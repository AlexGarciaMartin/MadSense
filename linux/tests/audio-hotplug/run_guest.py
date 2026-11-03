#!/usr/bin/env python3
"""Opt-in private PipeWire before production app attach; no production policy changes."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time

from actor import AppActor, LINUX
from desktop import PrivateDesktop
from evidence import require


def save(path, value): path.write_text(json.dumps(value, indent=2)+"\n")


def verify_gates(directory, token):
    sys.path.insert(0, str(LINUX / "tests/app-integration"))
    from qemu_broker import verify_result
    from qemu_core import verify_core_result
    from qemu_app import verify_app_result
    records = {}
    for name, relative, verify in (("baseline", "result.json", verify_result),
            ("core", "core-experiment/result.json", verify_core_result),
            ("app", "app-experiment/result.json", verify_app_result)):
        raw = (Path(directory) / relative).read_bytes(); value = json.loads(raw); verify(value, token)
        records[name] = dict(cases=len(value["cases"]), sha256=hashlib.sha256(raw).hexdigest())
    raw = (Path(directory) / "idle-upgrade/result.json").read_bytes(); upgrade = json.loads(raw)
    require(upgrade.get("result") == "passed" and len(upgrade.get("installations", [])) == 2,
            "Repeated idle installer verification missing")
    removal = upgrade.get("removal", {})
    require(removal.get("result") == "passed" and removal.get("cases") == [
        "modified_file_preserved_before_service_stop", "idle_removal_preserves_unknown_file",
        "repeated_removal_is_idempotent", "reinstall_after_removal_confirms_idle"],
        "Idle removal/reinstallation verification missing")
    records["idleUpgrade"] = dict(installations=2, removalCases=4, sha256=hashlib.sha256(raw).hexdigest())
    return records


def case(args, output, explicit):
    result = dict(selection="explicit" if explicit else "automatic", result="invalid", cleanupConfirmed=False)
    output.mkdir()
    actor = audio = None
    try:
        with AppActor(args.owned_guest_token, args.probe_directory, output / "actor") as actor:
            with PrivateDesktop(args.owned_guest_token, actor.fixture.parent, output / "audio") as audio:
                audio.start_graph(explicit)
                for epoch in (1, 2):
                    attach_started = time.monotonic_ns()
                    parent, state = actor.resume()
                    ready_observed = time.monotonic_ns()
                    require(audio.graph_started_ns < attach_started, "Audio graph did not precede attach")
                    audio.bind_sound(parent, epoch)
                    arrival = audio.settle(f"attached-{epoch}", True)
                    audio.result["arrivals"][-1].update(attachStartedNs=attach_started,
                        applicationReadyObservedNs=ready_observed, identity=actor.last_identity, appState=state["state"])
                    audio.save()
                    actor.pause()
                    require(not parent.exists(), "Owned USB parent survived pause")
                    audio.retire_removed_sound()
                    audio.settle(f"detached-{epoch}", False)
                audio.finish_observation()
                result.update(result="observed", defaultPreservationObserved=audio.result["defaultPreservationObserved"],
                    sourcePreservationObserved=audio.result["preservationByKind"]["source"],
                    sinkPreservationObserved=audio.result["preservationByKind"]["sink"])
            require(audio.result.get("accountProcessesAndFilesRemoved") is True and not audio.result.get("cleanupErrors")
                    and not audio.result.get("retirementError"),
                    "Private desktop cleanup failed")
        require(actor.cleanup["confirmed"], "Actor cleanup failed")
        result["cleanupConfirmed"] = True
    except BaseException as error:
        result.update(result="invalid", error=f"{type(error).__name__}: {error}")
    finally:
        if audio is not None:
            result["audioResultSha256"] = hashlib.sha256((audio.output / "result.json").read_bytes()).hexdigest()
        if actor is not None: result["actorCleanup"] = actor.cleanup
        save(output / "result.json", result)
    return result


def verify_result(directory, token, gates):
    directory = Path(directory); result = json.loads((directory / "result.json").read_text())
    require(result.get("result") == "observed" and result.get("environment") == dict(kind="owned-qemu-guest", instance=token)
        and result.get("functionalGate") is False and result.get("functionalGates") == gates,
        "Unexpected audio arrival scope or baseline attribution")
    cases = result.get("cases", [])
    require([row.get("selection") for row in cases] == ["automatic", "explicit"], "Missing separate automatic/explicit cases")
    for row in cases:
        path = directory / row["selection"]
        require(row == json.loads((path / "result.json").read_text()) and row.get("result") == "observed"
            and row.get("cleanupConfirmed") is True and row.get("actorCleanup", {}).get("confirmed") is True,
            "Audio arrival case cleanup or matching evidence missing")
        raw = (path / "audio/result.json").read_bytes(); audio = json.loads(raw)
        require(hashlib.sha256(raw).hexdigest() == row["audioResultSha256"] and audio.get("accountProcessesAndFilesRemoved") is True
            and not audio.get("cleanupErrors") and not audio.get("retirementError"), "Private audio state not restored")
        require(audio.get("ownedGuestToken") == token and audio.get("scope") == "private-audio-hotplug"
            and audio.get("selection") == row["selection"] and audio.get("noPhysicalAccessGranted") is True
            and audio.get("account", {}).get("uid", 0) > 0, "Audio result belongs to another session or privilege scope")
        require(len(audio.get("arrivals", [])) == len(audio.get("retiredSoundEpochs", [])) == 2,
                "Both attach/detach generations are required")
        require(all(value["nodesDestroyed"] is True for value in audio["retiredSoundEpochs"]), "Sound ACL epoch not retired")
        for epoch, (arrival, retired) in enumerate(zip(audio["arrivals"], audio["retiredSoundEpochs"]), 1):
            require(arrival["epoch"] == retired["epoch"] == epoch and arrival["usbParent"] == retired["usbParent"]
                and audio["graphStartedMonotonicNs"] < arrival["attachStartedNs"] <= arrival["applicationReadyObservedNs"]
                <= arrival["aclStartedNs"] <= arrival["aclFinishedNs"] < retired["retiredMonotonicNs"],
                "Graph/attach/ACL/removal chronology or epoch attribution is invalid")
        from evidence import metadata_changes, compare, preservation
        from pipewire_defaults import select_device, describe_nodes
        labels = set()
        for observed in audio["observations"]:
            label = observed["label"]
            require(label not in labels and all(ch.isalnum() or ch == "-" for ch in label), "Invalid or repeated graph snapshot label")
            labels.add(label)
            snapshot = json.loads((path / "audio" / (label+".json")).read_text())
            actual = compare(snapshot, audio["controlNodes"])
            require(all(observed.get(key) == value for key, value in actual.items()), "Graph summary differs from retained snapshot")
            require(("virtual" in observed) == label.startswith("attached-"), "Virtual identity missing or retained in detached phase")
            if "virtual" in observed:
                virtual = observed["virtual"]
                epoch = int(label.split("-")[1])
                require(epoch in (1, 2), "Unexpected attachment epoch")
                arrival = audio["arrivals"][epoch-1]
                require(virtual["usbParent"] == arrival["usbParent"]
                    and virtual["card"] == arrival["card"] and virtual["cardSysfs"] == arrival["cardSysfs"]
                    and Path(virtual["cardSysfs"]).is_relative_to(Path(virtual["usbParent"])),
                    "Graph belongs to another attachment or sound parent")
                card = select_device(snapshot, virtual["card"], virtual["cardSysfs"])
                require(virtual["deviceId"] == card["id"] and virtual["deviceSerial"] == int(card["info"]["props"]["object.serial"])
                    and virtual["deviceProperties"] == card["info"]["props"] and virtual["deviceParameters"] == card["info"].get("params", {})
                    and virtual["nodes"] == describe_nodes(snapshot, card), "Virtual node attribution differs from raw graph")
            else:
                require(not [value for value in snapshot if value.get("type", "").endswith(":Device")
                    and value.get("info", {}).get("props", {}).get("device.api") == "alsa"],
                    "Raw graph retains an ALSA device outside an attachment")
        require("before-attach" in labels and all(any(label.startswith(prefix) for label in labels)
            for prefix in ("attached-1-", "detached-1-", "attached-2-", "detached-2-")), "Missing a graph lifecycle phase")
        changes = metadata_changes((path / "audio/metadata-monitor.stdout").read_text(), audio["controlNodes"])
        require(changes == audio["metadataEvents"], "Metadata event summary differs from retained stream")
        preserved = preservation(audio["observations"], changes)
        expected = all(preserved.values())
        require(row["defaultPreservationObserved"] == audio["defaultPreservationObserved"] == expected,
                "Result hides a default replacement")
        require(audio["preservationByKind"] == preserved and row["sourcePreservationObserved"] == preserved["source"]
                and row["sinkPreservationObserved"] == preserved["sink"], "Per-direction result hides a transient replacement")
        require(json.loads((path / "actor/app/stopped.json").read_text()).get("cleanupConfirmed") is True,
                "Fresh broker cleanup ACK missing")
        require(json.loads((path / "actor/actor-cleanup.json").read_text()) == row["actorCleanup"],
                "Actor cleanup summary differs from its retained record")
        require(json.loads((path / "actor/fixture-before.json").read_text()) ==
                json.loads((path / "actor/fixture-restored.json").read_text()), "Physical fixture permissions changed")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owned-guest-token", required=True)
    parser.add_argument("--functional-results", type=Path, required=True)
    parser.add_argument("--probe-directory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    from ci_environment import validate_environment
    require(os.geteuid() == 0, "Root owned-guest observer required")
    environment = validate_environment(args.owned_guest_token)
    require(environment["kind"] == "owned-qemu-guest", "Owned guest required")
    gates = verify_gates(args.functional_results, args.owned_guest_token)
    output = args.output.resolve(); output.mkdir(parents=True, exist_ok=True)
    require(not any(output.iterdir()), "Fresh result directory required")
    result = dict(schema=1, result="invalid", environment=environment, functionalGate=False, functionalGates=gates,
        physicalDevices=False, applicationControllerExercised=True, applicationUiExercised=False,
        realDesktopSessionValidated=False, policyChanged=False, cases=[])
    try:
        for explicit in (False, True):
            row = case(args, output / ("explicit" if explicit else "automatic"), explicit)
            result["cases"].append(row)
            require(row["result"] == "observed" and row["cleanupConfirmed"], "Invalid private case; do not start another session")
        result["result"] = "observed"; save(output / "result.json", result)
        verify_result(output, args.owned_guest_token, gates)
    except BaseException as error:
        result.update(result="invalid", error=f"{type(error).__name__}: {error}")
    finally: save(output / "result.json", result)
    return 0 if result["result"] == "observed" else 1


if __name__ == "__main__": raise SystemExit(main())
