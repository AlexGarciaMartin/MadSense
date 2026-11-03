import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from evidence import compare, control_nodes, metadata_changes, preservation
from pipewire_defaults import describe_nodes
from run_guest import verify_result
from test_evidence import snapshot, PREFIX


class CollectedResultTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.top = dict(result="observed", environment=dict(kind="owned-qemu-guest", instance="owned"),
            functionalGate=False, functionalGates={}, cases=[])
        for selection in ("automatic", "explicit"):
            path = self.root / selection
            (path / "audio").mkdir(parents=True); (path / "actor/app").mkdir(parents=True)
            controls = control_nodes(snapshot(), dict(sink="control-sink", source="control-source"))
            monitor = 'Found "default" metadata 2\n'+''.join(
                f"update: id:0 key:'default.audio.{kind}' value:'{json.dumps(dict(name='control-'+kind))}' type:'Spa:String:JSON'\n"
                for kind in ("sink", "source"))
            (path / "audio/metadata-monitor.stdout").write_text(monitor)
            changes = metadata_changes(monitor, controls)
            audio = dict(ownedGuestToken="owned", scope="private-audio-hotplug", selection=selection,
                noPhysicalAccessGranted=True, account=dict(uid=1001), accountProcessesAndFilesRemoved=True,
                controlNodes=controls, graphStartedMonotonicNs=1, arrivals=[], retiredSoundEpochs=[], observations=[],
                metadataEvents=changes, defaultPreservationObserved=True, preservationByKind=dict(sink=True, source=True))
            for epoch in (1, 2):
                audio["arrivals"].append(dict(epoch=epoch, usbParent="/sys/devices/platform/vhci_hcd.0/usb1/1-1",
                    card=0, cardSysfs="/sys/devices/platform/vhci_hcd.0/usb1/1-1/1-1:1.0/sound/card0",
                    attachStartedNs=epoch*100+10, applicationReadyObservedNs=epoch*100+20,
                    aclStartedNs=epoch*100+30, aclFinishedNs=epoch*100+40))
                audio["retiredSoundEpochs"].append(dict(epoch=epoch, usbParent=audio["arrivals"][-1]["usbParent"],
                    nodesDestroyed=True, retiredMonotonicNs=epoch*100+90))
            for label in ("before-attach", "attached-1-01", "detached-1-01", "attached-2-01", "detached-2-01"):
                raw = snapshot(); observed = compare(raw, controls)
                if label.startswith("attached"):
                    sysfs = "/sys/devices/platform/vhci_hcd.0/usb1/1-1/1-1:1.0/sound/card0"
                    card = dict(id=20, type=PREFIX+"Device", info=dict(props={"device.api":"alsa", "api.alsa.card":0,
                        "device.sysfs.path":sysfs[4:], "device.name":"owned", "object.serial":120}, params={}))
                    raw.append(card)
                    for node in raw[2:4]: node["info"]["props"].update({"device.id":20, "api.alsa.pcm.card":0})
                    observed["virtual"] = dict(usbParent=audio["arrivals"][0]["usbParent"], card=0, cardSysfs=sysfs,
                        deviceId=20, deviceSerial=120, deviceProperties=card["info"]["props"], deviceParameters={},
                        nodes=describe_nodes(raw, card))
                observed["label"] = label; audio["observations"].append(observed)
                self.write(path / "audio" / (label+".json"), raw)
            self.write(path / "audio/result.json", audio)
            self.write(path / "actor/app/stopped.json", dict(cleanupConfirmed=True))
            self.write(path / "actor/actor-cleanup.json", dict(confirmed=True))
            for name in ("fixture-before.json", "fixture-restored.json"): self.write(path / "actor" / name, ["owned-node"])
            row = dict(selection=selection, result="observed", cleanupConfirmed=True, actorCleanup=dict(confirmed=True),
                audioResultSha256=hashlib.sha256((path / "audio/result.json").read_bytes()).hexdigest(),
                defaultPreservationObserved=True, sourcePreservationObserved=True, sinkPreservationObserved=True)
            self.write(path / "result.json", row); self.top["cases"].append(row)
        self.write(self.root / "result.json", self.top)

    def write(self, path, value): path.write_text(json.dumps(value))

    def change_audio(self, **changes):
        path = self.root / "automatic/audio/result.json"; data = json.loads(path.read_text()); data.update(changes)
        self.write(path, data)
        row = self.top["cases"][0]; row["audioResultSha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.write(self.root / "automatic/result.json", row); self.write(self.root / "result.json", self.top)

    def test_collected_snapshot_and_monitor_are_recomputed(self):
        verify_result(self.root, "owned", {})
        path = self.root / "automatic/audio/detached-1-01.json"
        data = json.loads(path.read_text()); data[-1]["metadata"][1]["value"]["name"] = "virtual-source"
        self.write(path, data)
        with self.assertRaises(RuntimeError): verify_result(self.root, "owned", {})

    def test_foreign_token_and_graph_started_after_attach_are_rejected(self):
        self.change_audio(ownedGuestToken="other")
        with self.assertRaises(RuntimeError): verify_result(self.root, "owned", {})
        self.change_audio(ownedGuestToken="owned", graphStartedMonotonicNs=200)
        with self.assertRaises(RuntimeError): verify_result(self.root, "owned", {})

    def test_detached_snapshot_with_remaining_alsa_card_is_invalid(self):
        path = self.root / "automatic/audio/detached-1-01.json"
        raw = json.loads(path.read_text())
        raw.append(dict(id=40, type=PREFIX+"Device", info=dict(props={"device.api":"alsa"})))
        self.write(path, raw)
        with self.assertRaises(RuntimeError): verify_result(self.root, "owned", {})


if __name__ == "__main__": unittest.main()
