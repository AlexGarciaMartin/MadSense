import copy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import socket
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from pipewire_defaults import (PREFIX, check_peer, default_sink, describe_nodes, observe_defaults,
                               optional_result, run_optional, select_device, summarize, supported_versions, volume_evidence)
from qemu_consumers import stage_consumer_research


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


class OptionalPipeWireTests(unittest.TestCase):
    ready = dict(ownedGuestToken="owned-test-uuid", usbParent="/sys/devices/platform/vhci_hcd.0/usb1/1-2")

    def record(self, **changes):
        return dict(scope="owned-private-pipewire-default-observer", ready=self.ready,
                    result="failed", accountProcessesAndFilesRemoved=True, **changes)

    def test_failed_collection_continues_only_with_exact_cleanup_record_and_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); ready = root / "ready.json"; ready.write_text(json.dumps(self.ready))
            evidence = root / "pipewire-defaults"
            def fail(*_):
                evidence.mkdir()
                (evidence / "result.json").write_text(json.dumps(self.record(error="No supported profile")))
                raise RuntimeError("No supported profile")
            with patch("pipewire_defaults.run", side_effect=fail):
                summary = run_optional(ready, evidence, "fixture")
            self.assertEqual(summary["result"], "failed")
            self.assertTrue(summary["cleanupVerified"])
            self.assertFalse(summary["gatesConsumerCompatibility"])
            self.assertEqual(summary["resultSha256"], hashlib.sha256((evidence / "result.json").read_bytes()).hexdigest())

    def test_cleanup_error_or_missing_removal_proof_blocks_the_next_session(self):
        with tempfile.TemporaryDirectory() as directory:
            result = Path(directory) / "result.json"
            for change in (dict(accountProcessesAndFilesRemoved=False), dict(accountProcessesAndFilesRemoved=None),
                           dict(cleanupErrors=["ACL changed"]), dict(result="passed")):
                result.write_text(json.dumps(self.record() | change))
                with self.subTest(change=change), self.assertRaises(RuntimeError):
                    optional_result(result, self.ready)

    def test_stale_or_missing_result_cannot_satisfy_a_new_invocation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); ready = root / "ready.json"; ready.write_text(json.dumps(self.ready))
            evidence = root / "pipewire-defaults"
            with patch("pipewire_defaults.run", side_effect=RuntimeError("early failure")), self.assertRaises(RuntimeError):
                run_optional(ready, evidence, "fixture")
            evidence.mkdir(); (evidence / "result.json").write_text(json.dumps(self.record()))
            with patch("pipewire_defaults.run") as run, self.assertRaises(RuntimeError):
                run_optional(ready, evidence, "fixture")
            run.assert_not_called()

    def test_other_guest_or_virtual_device_cleanup_is_not_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            result = Path(directory) / "result.json"
            result.write_text(json.dumps(self.record()))
            for change in (dict(ownedGuestToken="another-guest"), dict(usbParent=self.ready["usbParent"] + "x")):
                with self.subTest(change=change), self.assertRaises(RuntimeError):
                    optional_result(result, self.ready | change)

    def test_actual_staged_observer_and_consumer_modules_import_without_repository_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stage_consumer_research(root)
            research = root / "linux/research"
            code = ("import pathlib,sys; root=pathlib.Path(sys.argv[1]); sys.path.insert(0,str(root)); "
                    "import pipewire_defaults,audio_defaults,consumer_session,run_wine_consumer,qemu_consumers; "
                    "assert all(pathlib.Path(m.__file__).parent==root for m in "
                    "(pipewire_defaults,audio_defaults,consumer_session,run_wine_consumer,qemu_consumers)); "
                    "assert callable(pipewire_defaults.run_optional)")
            completed = subprocess.run([sys.executable, "-I", "-c", code, str(research)], cwd=root,
                                       capture_output=True, text=True, timeout=15)
            self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
