import copy
import os
from pathlib import Path, PurePosixPath
import socket
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest

from pipewire_defaults import (PREFIX, check_peer, default_sink, describe_nodes, observe_defaults,
                               select_device, summarize, supported_versions, volume_evidence)


def version_output(name, version):
    return f"{name}\nCompiled with lib{name} {version}\nLinked with lib{name} {version}\n"


class PipeWireEvidenceTests(unittest.TestCase):
    def setUp(self):
        # Representative pw-dump 1.0.5 field contracts, not claimed guest output.
        self.sysfs = PurePosixPath("/sys/devices/platform/vhci_hcd.0/usb1/1-2/1-2:1.0/sound/card2")
        self.card = dict(id=42, type=PREFIX + "Device", info=dict(props={"device.api": "alsa", "api.alsa.card": "2",
            "device.sysfs.path": str(self.sysfs)[4:], "device.name": "alsa_card.owned"},
            params={"Profile": [{"index": 1, "name": "stereo"}], "EnumProfile": [{"index": 2, "name": "Direct"}],
                    "Route": [{"props": {"channelMap": ["FL", "FR", "RL", "RR"], "channelVolumes": [.25, .25, 1.0, 1.0]}}]}))
        self.node = dict(id=45, type=PREFIX + "Node", info=dict(state="suspended", props={"device.id": "42",
            "api.alsa.pcm.card": "2", "node.name": "alsa_output.owned", "object.serial": 123, "media.class": "Audio/Sink"},
            params={"Props": [{"channelMap": ["FL", "FR", "RL", "RR"], "channelVolumes": [.25, .25, 1.0, 1.0], "mute": False}]}))
        self.metadata = dict(id=20, type=PREFIX + "Metadata", props={"metadata.name": "default"}, metadata=[
            dict(subject=0, key="default.audio.sink", type="Spa:String:JSON", value={"name": "alsa_output.owned"})])
        self.snapshot = [self.card, self.node, self.metadata]

    def test_pinned_version_family_requires_compiled_and_linked_contract(self):
        self.assertEqual(supported_versions(version_output("pipewire", "1.0.5"), version_output("wireplumber", "0.4.17"))
                         ["wireplumber"]["linked"], "0.4.17")
        for output in (version_output("wireplumber", "0.5.5"), "wireplumber 0.4.17",
                       "Compiled with libwireplumber 0.4.17\nLinked with libwireplumber 0.5.5"):
            with self.subTest(output=output), self.assertRaises(RuntimeError):
                supported_versions(version_output("pipewire", "1.0.5"), output)

    def test_exact_usb_join_rejects_matching_name_and_card_on_wrong_parent(self):
        self.assertIs(select_device(self.snapshot, 2, self.sysfs), self.card)
        self.card["info"]["props"]["device.sysfs.path"] = str(self.sysfs)[4:].replace("1-2", "1-3")
        with self.assertRaises(RuntimeError):
            select_device(self.snapshot, 2, self.sysfs)

    def test_second_alsa_card_is_not_silently_filtered(self):
        foreign = copy.deepcopy(self.card)
        foreign["id"] = 99
        foreign["info"]["props"]["api.alsa.card"] = "3"
        with self.assertRaises(RuntimeError):
            select_device([*self.snapshot, foreign], 2, self.sysfs)

    def test_duplicate_registry_ids_are_not_accepted(self):
        self.node["id"] = self.card["id"]
        with self.assertRaises(RuntimeError):
            select_device(self.snapshot, 2, self.sysfs)

    def test_node_requires_exact_card_object_and_alsa_index(self):
        self.assertEqual(describe_nodes(self.snapshot, self.card)[0]["id"], 45)
        for field, value in (("device.id", "99"), ("api.alsa.pcm.card", "3")):
            altered = copy.deepcopy(self.snapshot)
            altered[1]["info"]["props"][field] = value
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                describe_nodes(altered, self.card)

    def test_direct_advertised_does_not_mean_direct_selected(self):
        observed = summarize(self.snapshot, 2, self.sysfs)
        self.assertTrue(observed["directProfileAdvertised"])
        self.assertEqual(observed["device"]["parameters"]["Profile"][0]["name"], "stereo")

    def test_channel_order_and_attenuation_remain_visible(self):
        value = self.node["info"]["params"]["Props"][0]
        described = volume_evidence(value)
        self.assertTrue(described["quadMap"])
        self.assertTrue(described["rearChannelsReportedUnity"])
        value["channelVolumes"][2] = .999
        value["channelMap"] = ["RL", "RR", "FL", "FR"]
        described = volume_evidence(value)
        self.assertFalse(described["quadMap"])
        self.assertFalse(described["rearChannelsReportedUnity"])

    def test_scalar_or_incomplete_or_nonfinite_volume_never_proves_per_channel_gain(self):
        for value in ({"volume": 1.0}, {"channelMap": ["FL", "FR"], "channelVolumes": [1]},
                      {"channelMap": ["RL", "RR"], "channelVolumes": [1, float("nan")]},
                      {"channelMap": ["RL", "RR"], "channelVolumes": [True, 1]}):
            with self.subTest(value=value):
                described = volume_evidence(value)
                self.assertFalse(described["perChannelValuesPresent"])
                self.assertIsNone(described["rearChannelsReportedUnity"])

    def test_metadata_requires_subject_zero_json_and_live_unique_sink(self):
        expected = default_sink(self.snapshot)
        self.assertEqual(expected["id"], 45)
        self.metadata["metadata"].insert(0, dict(subject=45, key="default.audio.sink", type="Spa:String:JSON", value={"name": "foreign"}))
        self.assertEqual(default_sink(self.snapshot), expected)
        self.metadata["metadata"][-1]["value"] = {"name": "foreign"}
        with self.assertRaises(RuntimeError):
            default_sink(self.snapshot)

    def test_unresolved_or_stringified_metadata_is_not_treated_as_default(self):
        self.metadata["metadata"][0]["value"] = '{"name":"alsa_output.owned"}'
        with self.assertRaises(RuntimeError):
            default_sink(self.snapshot)

    def test_default_same_name_recreated_serial_is_a_change(self):
        before = default_sink(self.snapshot)
        self.node["info"]["props"]["object.serial"] = 124
        observation = observe_defaults(before, default_sink(self.snapshot))
        self.assertTrue(observation["changedDuringObservation"])
        self.assertFalse(observation["preservedDuringObservation"])

    def test_absent_default_is_unknown_not_preserved_or_hotplug_validated(self):
        absent = default_sink([self.card])
        observed = observe_defaults(absent, absent)
        self.assertIsNone(observed["preservedDuringObservation"])
        self.assertFalse(observed["changedDuringObservation"])
        self.assertFalse(observed["hotplugPreservationValidated"])

    def test_missing_serial_does_not_prove_object_continuity(self):
        del self.node["info"]["props"]["object.serial"]
        with self.assertRaises(RuntimeError):
            default_sink(self.snapshot)

    @unittest.skipUnless(sys.platform == "linux", "SO_PEERCRED requires Linux")
    def test_private_socket_peer_uses_actual_kernel_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "runtime").mkdir(mode=0o700)
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
                server.bind(str(root / "runtime/pipewire-0")); server.listen(2)
                server.settimeout(5)
                failures = []
                def accept():
                    try:
                        for _ in range(2):
                            endpoint, _ = server.accept(); endpoint.close()
                    except BaseException as error:
                        failures.append(error)
                thread = threading.Thread(target=accept)
                thread.start()
                session = SimpleNamespace(root=root, uid=os.getuid(), gid=os.getgid())
                fake = SimpleNamespace(pid=os.getpid() + 1, poll=lambda: None)
                with self.assertRaises(RuntimeError):
                    check_peer(session, fake)
                actual = SimpleNamespace(pid=os.getpid(), poll=lambda: None)
                self.assertEqual(check_peer(session, actual)["pid"], os.getpid())
                thread.join(6)
                self.assertFalse(thread.is_alive())
                self.assertFalse(failures)


if __name__ == "__main__":
    unittest.main()
