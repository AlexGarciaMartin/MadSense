#!/usr/bin/env python3
"""Private ignored ALSA card + client-owned playback, after unmodified app gates."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from actor import AppActor
from run_guest import save, verify_gates, verify_case
from policy import predict_parents, monitor_evidence
from evidence import compare, metadata_changes, preservation, require
from export_policy import IgnoreRule, configuration, publication, validate_node, PREFIX, validate_plan, alsa_node, snapshot_source
from export_desktop import ExportDesktop

CASES = ("owned-automatic", "owned-explicit", "foreign-parent", "foreign-nonce")

class MarkedActor(AppActor):
    def __init__(self, *args, nonce): super().__init__(*args); self.nonce = nonce
    def actor_environment(self):
        return super().actor_environment() | {"MADSENSE_AUDIO_INTERFACE_NONCE":self.nonce}

def descriptor_evidence(directory, epoch, reference, raw, hid):
    expected = bytearray.fromhex(reference["device"] + reference["config"])
    require(expected[18+17] == 0 and expected[16] == 0, "Reference AC string/serial is not the expected baseline")
    expected[18+17] = len(reference["strings"])+1
    require(raw == expected and hid == bytes.fromhex(reference["hid"]),
        "Runtime changed more than the AC0 string index, or changed HID/device descriptors")
    (directory / f"usb-descriptors-{epoch}.bin").write_bytes(raw)
    (directory / f"hid-descriptor-{epoch}.bin").write_bytes(hid)

def case(args, output, name, reference):
    output.mkdir()
    parents, inventory = predict_parents(); nonce = uuid.uuid4().hex
    plan = dict(actualParent=parents[0], targetParent=parents[1 if name == "foreign-parent" else 0],
        nonce=nonce, ruleNonce=uuid.uuid4().hex if name == "foreign-nonce" else nonce,
        matched=name.startswith("owned-"), vhciBefore=inventory)
    validate_plan(plan, name)
    explicit = name == "owned-explicit"
    row = dict(caseId=name, plan=plan, selection="explicit" if explicit else "automatic",
        result="invalid", cleanupConfirmed=False, productionBinary=False)
    actor = audio = rule = None
    try:
        # Rule outlives both audio and USB owner, including exceptions.
        with IgnoreRule(plan, output / "rule") as rule:
            with MarkedActor(args.owned_guest_token, args.marked_probe_directory, output / "actor", nonce=nonce) as actor:
                with ExportDesktop(args.owned_guest_token, actor.fixture.parent, output / "audio",
                        plan=plan, executable=args.export_client) as audio:
                    audio.result["exportPlan"] = plan
                    audio.start_graph(explicit)
                    require(rule.record["reloadAcknowledgedNs"] < audio.graph_started_ns, "Ignore rule loaded too late")
                    for epoch in (1, 2):
                        started = time.monotonic_ns(); parent, state = actor.resume(); ready = time.monotonic_ns()
                        require(str(parent) == plan["actualParent"], "Different imported parent")
                        audio.bind_sound(parent, epoch)
                        hid = (Path(actor.last_identity["hidrawSysfs"]) / "device/report_descriptor").read_bytes()
                        descriptor_evidence(audio.output, epoch, reference, (parent / "descriptors").read_bytes(), hid)
                        audio.result["arrivals"][-1].update(attachStartedNs=started,
                            applicationReadyObservedNs=ready, identity=actor.last_identity, appState=state["state"])
                        audio.save()
                        if plan["matched"]:
                            audio.settle(f"ignored-{epoch}", True)
                            audio.start_export(f"export-{epoch}"); audio.settle(f"exported-{epoch}", True)
                            if epoch == 1:
                                audio.stop_export(crash=True); audio.settle("client-crashed-1", True)
                                audio.start_export("export-restarted-1"); audio.settle("export-restarted-1", True)
                            audio.stop_export(); audio.settle(f"client-stopped-{epoch}", True)
                        else:
                            audio.settle(f"attached-{epoch}", True)
                        actor.pause(); require(not parent.exists(), "USB survived pause")
                        audio.retire_removed_sound(); audio.settle(f"detached-{epoch}", False)
                    audio.finish_observation()
                    row.update(result="observed", defaultPreservationObserved=audio.result["defaultPreservationObserved"],
                        sourcePreservationObserved=audio.result["preservationByKind"]["source"],
                        sinkPreservationObserved=audio.result["preservationByKind"]["sink"])
                require(audio.result.get("accountProcessesAndFilesRemoved") is True and not audio.result.get("cleanupErrors")
                    and not audio.result.get("retirementError") and not audio.result.get("exportCleanupError"), "Audio cleanup failed")
            require(actor.cleanup["confirmed"], "Actor cleanup failed")
        require(rule.record["removed"] and rule.record["reloadAfterRemoval"], "Ignore rule cleanup failed")
        row["cleanupConfirmed"] = True
    except BaseException as error: row.update(result="invalid", error=f"{type(error).__name__}: {error}")
    finally:
        if audio is not None and (audio.output / "result.json").exists():
            row["audioResultSha256"] = hashlib.sha256((audio.output / "result.json").read_bytes()).hexdigest()
        if actor is not None: row["actorCleanup"] = actor.cleanup
        save(output / "result.json", row)
    return row

def audit_case(path, row, token, reference):
    plan = row["plan"]; matched = plan["matched"]
    validate_plan(plan, row["caseId"])
    require(row == json.loads((path / "result.json").read_text()) and row["result"] == "observed"
        and row["cleanupConfirmed"] and row["actorCleanup"]["confirmed"] and row["productionBinary"] is False,
        "Missing matching result/cleanup")
    raw = (path / "audio/result.json").read_bytes(); audio = json.loads(raw)
    require(hashlib.sha256(raw).hexdigest() == row["audioResultSha256"] and audio["ownedGuestToken"] == token
        and audio["exportPlan"] == plan and audio["accountProcessesAndFilesRemoved"]
        and not audio.get("cleanupErrors") and not audio.get("exportCleanupError")
        and audio["noPhysicalAccessGranted"] and audio["account"]["uid"] > 0, "Audio scope or cleanup mismatch")
    sources = audio.get("snapshotSources", {})
    require(sources and all(record["label"] in sources for record in audio["observations"]), "Snapshot raw attribution missing")
    for label, recorded in sources.items():
        require(label and all(c.isalnum() or c == "-" for c in label), "Invalid snapshot source label")
        require(recorded == snapshot_source((path / "audio" / (label+"-pw-dump.stdout")).read_bytes(),
            (path / "audio" / (label+".json")).read_bytes()), "Snapshot raw hash/size attribution differs")
    rule = json.loads((path / "rule/ignore-rule.json").read_text())
    content = configuration(plan["targetParent"], plan["ruleNonce"]).encode()
    require(rule["plan"] == plan and (path / "rule/ignore.rules").read_bytes() == content
        and rule["sha256"] == hashlib.sha256(content).hexdigest() and rule["installed"] and rule["removed"]
        and rule["reloadAfterRemoval"] and rule["reloadAcknowledgedNs"] < audio["graphStartedMonotonicNs"],
        "Ignore rule provenance, installation or cleanup missing")
    require(len(audio["arrivals"]) == len(audio["retiredSoundEpochs"]) == 2, "Missing USB reuse epochs")
    for epoch, (arrival, retired) in enumerate(zip(audio["arrivals"], audio["retiredSoundEpochs"]), 1):
        require(arrival["epoch"] == retired["epoch"] == epoch and arrival["usbParent"] == retired["usbParent"] == plan["actualParent"]
            and retired["nodesDestroyed"] and audio["graphStartedMonotonicNs"] < arrival["attachStartedNs"]
            <= arrival["applicationReadyObservedNs"] <= arrival["aclStartedNs"] <= arrival["aclFinishedNs"]
            < retired["retiredMonotonicNs"] < rule["removedMonotonicNs"], "USB/ACL/rule chronology differs")
        require(arrival["identity"]["usbSerial"] is None, "Experimental nonce leaked into iSerial")
        require((path / f"audio/interface-{epoch}.txt").read_text().strip() == PREFIX+plan["nonce"], "Interface string differs from lease nonce")
        props = json.loads((path / f"audio/udev-card-{epoch}.json").read_text())
        require(props == arrival["udevProperties"] and props.get("ACP_IGNORE") == ("1" if matched else None)
            and props.get("PULSE_IGNORE") == ("1" if matched else None)
            and props.get("MADSENSE_AUDIO_LEASE") == (plan["nonce"] if matched else None), "Ignore matched a foreign identity")
        # Pure comparison: no rewrite of evidence during independent audit.
        expected = bytearray.fromhex(reference["device"] + reference["config"]); expected[18+17] = len(reference["strings"])+1
        require((path / f"audio/usb-descriptors-{epoch}.bin").read_bytes() == expected
            and (path / f"audio/hid-descriptor-{epoch}.bin").read_bytes() == bytes.fromhex(reference["hid"]),
            "Runtime descriptor evidence differs")
    if not matched:
        verify_case(path, row, token)
        observed = monitor_evidence((path / "audio/graph-monitor.stdout").read_text(), plan)
    else:
        observed = publication((path / "audio/graph-monitor.stdout").read_text(), plan, 3)
        labels = set()
        for record in audio["observations"]:
            label = record["label"]
            require(label not in labels and all(c.isalnum() or c == "-" for c in label), "Invalid snapshot label")
            labels.add(label)
            snapshot = json.loads((path / "audio" / (label+".json")).read_text())
            compared = compare(snapshot, audio["controlNodes"])
            require(all(record.get(key) == value for key, value in compared.items()), "Default summary differs from graph")
            nodes = []
            for value in snapshot:
                props = value.get("info", {}).get("props", {})
                if value.get("type") == "PipeWire:Interface:Device": require(props.get("device.api") != "alsa", "Automatic card present")
                if value.get("type") == "PipeWire:Interface:Node" and alsa_node(props):
                    nodes.append(validate_node(value, plan, require_format=True))
            require(len(nodes) == int(record["expectedExport"]), "Unexpected export count")
            if nodes:
                require(record["export"] == nodes[0], "Export snapshot differs")
                exports = [entry for entry in audio["exports"] if entry.get("nodeIdentity") == {key:nodes[0][key] for key in ("id","serial","clientId")}]
                require(len(exports) == 1, "Export has no unique owning process")
                clients = [obj for obj in snapshot if obj.get("type") == "PipeWire:Interface:Client" and obj["id"] == nodes[0]["clientId"]]
                require(len(clients) == 1 and str(clients[0]["info"]["props"].get("application.process.id")) == str(exports[0]["pid"]), "Wrong export owner")
        require(all(any(label.startswith(prefix) for label in labels) for prefix in (
            "before-attach", "ignored-1-", "ignored-2-", "exported-1-", "exported-2-", "client-crashed-1-",
            "export-restarted-1-", "client-stopped-1-", "client-stopped-2-", "detached-1-", "detached-2-")), "Missing lifecycle phase")
        require([entry["stopKind"] for entry in audio["exports"]] == ["sigkill","owner-eof","owner-eof"], "Missing client lifetime controls")
        changes = metadata_changes((path / "audio/metadata-monitor.stdout").read_text(), audio["controlNodes"])
        preserved = preservation(audio["observations"], changes)
        require(changes == audio["metadataEvents"] and preserved == audio["preservationByKind"], "Transient default mismatch")
        require(row["defaultPreservationObserved"] == audio["defaultPreservationObserved"] == all(preserved.values())
            and row["sourcePreservationObserved"] == preserved["source"]
            and row["sinkPreservationObserved"] == preserved["sink"], "Row flags hide a default replacement")
        require(json.loads((path / "actor/actor-cleanup.json").read_text()) == row["actorCleanup"],
            "Actor cleanup summary differs from its retained record")
        require(json.loads((path / "actor/app/stopped.json").read_text())["cleanupConfirmed"] is True
            and json.loads((path / "actor/fixture-before.json").read_text()) == json.loads((path / "actor/fixture-restored.json").read_text()), "Actor/kernel cleanup missing")
    require(all(audio["preservationByKind"][kind] == matched for kind in ("sink","source")), "Default-preservation expectation failed")
    return dict(publication=observed, defaultPreserved=audio["preservationByKind"], cleanupConfirmed=True,
        usbDescriptorOnlyAcInterfaceChanged=True, hidDescriptorUnchanged=True, pcmValidated=False,
        wineContainerAssociationValidated=False, physicalAudioValidated=False)

def verify_result(directory, token, gates):
    directory = Path(directory); value = json.loads((directory / "result.json").read_text())
    require(value["ownedGuestToken"] == token and value["functionalGates"] == gates and value["result"] == "passed"
        and value["productionBinary"] is False and value["scope"] == "private-ignore-and-client-export",
        "Export result is not attributed to the gated VM")
    reference = json.loads((directory / "descriptor-reference.json").read_text())
    require([row["caseId"] for row in value["cases"]] == list(CASES), "Missing export controls")
    require(len({row["plan"]["nonce"] for row in value["cases"]}) == len(CASES), "Separate cases reused an interface nonce")
    for row in value["cases"]:
        require(row["audit"] == audit_case(directory / row["caseId"], row, token, reference), "Export audit differs")
    return value

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("functional-results","marked-probe-directory","export-client","descriptor-reference","output"):
        parser.add_argument("--"+name, type=Path, required=True)
    parser.add_argument("--owned-guest-token", required=True)
    args = parser.parse_args()
    from ci_environment import validate_environment
    require(os.geteuid() == 0 and validate_environment(args.owned_guest_token)["kind"] == "owned-qemu-guest", "Owned root VM required")
    gates = verify_gates(args.functional_results, args.owned_guest_token)
    output = args.output.resolve(); output.mkdir()
    reference = json.loads(args.descriptor_reference.read_text()); save(output / "descriptor-reference.json", reference)
    result = dict(schema=1, scope="private-ignore-and-client-export", result="invalid", ownedGuestToken=args.owned_guest_token,
        functionalGates=gates, productionBinary=False, productionPolicyModified=False, cases=[])
    try:
        for name in CASES:
            row = case(args, output/name, name, reference); result["cases"].append(row)
            require(row["result"] == "observed" and row["cleanupConfirmed"], "Export case failed; no subsequent session")
            row["audit"] = audit_case(output/name, row, args.owned_guest_token, reference)
            save(output/name/"result.json", row)
        result["result"] = "passed"; save(output/"result.json", result)
        verify_result(output, args.owned_guest_token, gates)
    except BaseException as error: result.update(result="invalid", error=f"{type(error).__name__}: {error}")
    finally: save(output/"result.json", result)
    return 0 if result["result"] == "passed" else 1

if __name__ == "__main__": raise SystemExit(main())
