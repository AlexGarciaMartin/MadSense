import copy
import json
import types
import unittest
from unittest.mock import Mock

from policy import MARKER, configuration, monitor_evidence, validate_virtual
from qemu_audio_hotplug import run_policy_stage

PARENT = "/sys/devices/platform/vhci_hcd.0/usb1/1-1"
NONCE = "1234abcd"*4


def plan(foreign=False):
    return dict(actualParent=PARENT, targetParent=PARENT if not foreign else PARENT[:-1]+"2",
                nonce=NONCE, matched=not foreign)


def virtual(foreign=False):
    device = {} if foreign else {MARKER:NONCE}
    nodes = []
    for kind in ("Audio/Sink", "Audio/Source") if foreign else ("Audio/Sink",):
        playback = kind == "Audio/Sink"
        properties = dict(device)
        properties.update({"priority.session":(1009 if playback else 2008) if foreign else 1,
                           "audio.channels":4 if playback else 2,
                           "audio.position":"FL,FR,RL,RR" if playback else "FL,FR"})
        nodes.append(dict(mediaClass=kind, properties=properties,
            parameters={"EnumFormat":[dict(channels=4, position=["FL", "FR", "RL", "RR"])]}))
    return dict(usbParent=PARENT, cardSysfs=PARENT+"/1-1:1.0/sound/card0", deviceProperties=device, nodes=nodes)


def graph(foreign=False):
    batches = []
    for epoch in (1, 2):
        value = virtual(foreign)
        props = value["deviceProperties"] | {"device.api":"alsa", "device.sysfs.path":value["cardSysfs"][4:],
                                            "object.serial":epoch*100}
        rows = [dict(id=20, type="PipeWire:Interface:Device", info=dict(props=props))]
        for index, node in enumerate(value["nodes"], 30):
            props = node["properties"] | {"api.alsa.pcm.card":0, "device.id":20,
                "object.serial":epoch*100+index, "media.class":node["mediaClass"]}
            rows.append(dict(id=index, type="PipeWire:Interface:Node", info=dict(props=props)))
        batches.append(rows)
        batches.append([dict(id=index, info=None) for index in (20, 30, 31)])
    return batches


def text(batches): return "\n".join(json.dumps(row) for row in batches)


class PolicyTests(unittest.TestCase):
    def test_configuration_rejects_broad_parent_and_injection(self):
        self.assertIn(PARENT[4:]+"/*/sound/card*", configuration(PARENT, NONCE))
        for value in ("/sys/devices/platform/vhci_hcd.0", PARENT+"*", PARENT+"/../1-2", PARENT+'"', PARENT.replace("usb1", "usb2")):
            with self.assertRaises(RuntimeError): configuration(value, NONCE)
        with self.assertRaises(RuntimeError): configuration(PARENT, '"; os.execute("x")')

    def test_positive_keeps_quad_usb_and_removes_only_desktop_capture(self):
        self.assertEqual(validate_virtual(virtual(), plan())["desktopCaptureNodes"], 0)
        result = monitor_evidence(text(graph()), plan())
        self.assertEqual(result["publishedNodeCounts"], {"Audio/Sink":2, "Audio/Source":0})
        value = virtual(); value["nodes"][0]["parameters"]["EnumFormat"][0]["channels"] = 2
        with self.assertRaises(RuntimeError): validate_virtual(value, plan())

    def test_foreign_parent_control_retains_capture_and_original_priorities(self):
        self.assertEqual(validate_virtual(virtual(True), plan(True))["desktopCaptureNodes"], 1)
        self.assertTrue(monitor_evidence(text(graph(True)), plan(True))["captureEverPublished"])
        value = virtual(True); value["deviceProperties"][MARKER] = NONCE
        with self.assertRaises(RuntimeError): validate_virtual(value, plan(True))

    def test_transient_capture_and_late_priority_fix_are_rejected(self):
        batches = graph()
        transient = copy.deepcopy(batches[0][1]); transient["id"] = 40
        transient["info"]["props"]["media.class"] = "Audio/Source"
        batches.insert(1, [transient, dict(id=40, info=None)])
        with self.assertRaisesRegex(RuntimeError, "even transiently"):
            monitor_evidence(text(batches), plan())
        batches = graph(); old = copy.deepcopy(batches[0][1]); old["info"]["props"]["priority.session"] = 1009
        batches[0].insert(1, old)
        with self.assertRaisesRegex(RuntimeError, "before policy"):
            monitor_evidence(text(batches), plan())

    def test_neighbor_parent_missing_epoch_and_truncated_graph_are_invalid(self):
        batches = graph(); batches[0][0]["info"]["props"]["device.sysfs.path"] = PARENT[4:]+"0/1-10:1.0/sound/card0"
        with self.assertRaisesRegex(RuntimeError, "Foreign ALSA parent"):
            monitor_evidence(text(batches), plan())
        with self.assertRaises(RuntimeError): monitor_evidence(text(graph()[:2]), plan())
        with self.assertRaises(json.JSONDecodeError): monitor_evidence(text(graph())+"[", plan())

    def test_policy_cannot_start_before_baseline_observation(self):
        guest = types.SimpleNamespace(provenance=dict(baselineExit=0, coreExit=0, appExit=0), token="owned", run=Mock())
        with self.assertRaises(RuntimeError): run_policy_stage(guest)
        guest.run.assert_not_called()
        guest.provenance["audioHotplugExit"] = 0; guest.run.return_value.returncode = 0
        run_policy_stage(guest)
        self.assertEqual(guest.provenance["audioPolicyExit"], 0)


if __name__ == "__main__": unittest.main()
