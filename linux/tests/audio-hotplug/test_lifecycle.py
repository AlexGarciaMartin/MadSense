import json
from pathlib import Path
import re
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from desktop import PrivateDesktop
from qemu_audio_hotplug import run_stage
from evidence import compare


class SoundEpochTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.session = PrivateDesktop.__new__(PrivateDesktop)
        self.session.parent = self.root / "removed-usb"
        self.session.epoch = 1
        self.session.grants = [dict(path=str(self.root / "controlC0"), inode=100)]
        self.session.result = dict(retiredSoundEpochs=[])
        self.session.audit_access = Mock(); self.session.save = Mock()

    def test_destroyed_nodes_retire_without_writing_any_reused_path(self):
        self.session.retire_removed_sound()
        self.assertIsNone(self.session.parent)
        self.assertEqual(self.session.grants, [])
        self.assertTrue(self.session.result["retiredSoundEpochs"][0]["nodesDestroyed"])
        self.assertEqual(self.session.result["retiredSoundEpochs"][0]["grants"][0]["inode"], 100)
        self.session.audit_access.assert_called_once_with([])

    def test_live_parent_and_reused_sound_name_never_clear_old_grant(self):
        self.session.parent.mkdir()
        with self.assertRaises(RuntimeError): self.session.retire_removed_sound()
        self.session.parent.rmdir()
        (self.root / "controlC0").write_text("unrelated replacement")
        with self.assertRaises(RuntimeError): self.session.retire_removed_sound()
        self.assertEqual(len(self.session.grants), 1)
        self.assertEqual((self.root / "controlC0").read_text(), "unrelated replacement")
        self.session.audit_access.assert_not_called()

    def test_optional_stage_requires_all_original_gates(self):
        guest = types.SimpleNamespace(provenance=dict(baselineExit=0, coreExit=0), run=Mock(), token="owned")
        with self.assertRaises(RuntimeError): run_stage(guest)
        guest.run.assert_not_called()
        guest.provenance["appExit"] = 0; guest.run.return_value.returncode = 0
        run_stage(guest)
        self.assertEqual(guest.provenance["audioHotplugExit"], 0)
        self.assertEqual(guest.run.call_args.kwargs["timeout"], 660)


class ExplicitControlSelectionTests(unittest.TestCase):
    def test_both_software_controls_satisfy_real_wpctl_exact_class_contract(self):
        # Model wpctl 0.4's actual strict class check, which rejected the first
        # native source fixture before any controller attachment was attempted.
        with tempfile.TemporaryDirectory() as temporary:
            session = PrivateDesktop.__new__(PrivateDesktop)
            session.output = Path(temporary); session.env = {"PIPEWIRE_REMOTE":"owned"}
            session.result = {}; session.save = Mock(); session.check_monitors = Mock()
            nodes, configured = {}, {}
            def command(label, argv):
                if label.startswith("create-control-"):
                    kind = label.removeprefix("create-control-")
                    properties = argv[-1]
                    self.assertIn("node.virtual = true", properties)
                    nodes[kind] = dict(id=20+len(nodes), type="PipeWire:Interface:Node", info=dict(props={
                        "node.name":re.search(r'node.name = "([^"]+)"', properties)[1],
                        "media.class":re.search(r'media.class = "([^"]+)"', properties)[1],
                        "object.serial":100+len(nodes)}))
                elif label.startswith("select-control-"):
                    kind = label.removeprefix("select-control-")
                    node = nodes[kind]
                    if node["info"]["props"]["media.class"] not in ("Audio/Sink", "Audio/Source", "Video/Source"):
                        raise RuntimeError("wpctl: not a device node")
                    self.assertEqual(int(argv[-1]), node["id"])
                    configured[kind] = node["info"]["props"]["node.name"]
            def capture(*_):
                metadata = [dict(subject=0, key="default.audio."+kind, type="Spa:String:JSON",
                    value=dict(name=node["info"]["props"]["node.name"])) for kind, node in nodes.items()]
                metadata += [dict(subject=0, key="default.configured.audio."+kind, type="Spa:String:JSON",
                    value=dict(name=name)) for kind, name in configured.items()]
                return [*nodes.values(), dict(id=99, type="PipeWire:Interface:Metadata",
                    props={"metadata.name":"default"}, metadata=metadata)]
            def start(label, argv):
                if label == "metadata-monitor":
                    text = 'Found "default" metadata 99\n'
                    text += ''.join(f"update: id:0 key:'default.audio.{kind}' value:'{json.dumps(dict(name=name))}' type:'Spa:String:JSON'\n"
                                    for kind, name in configured.items())
                    (session.output / "metadata-monitor.stdout").write_text(text)
                return Mock()
            session.command = command; session.start = start
            session.observe = lambda *_: compare(capture(), session.controls)
            with patch("desktop.private_stack", return_value=(Mock(), Mock())), patch("desktop.capture", side_effect=capture), \
                 patch.dict(sys.modules, {"kernel_probe":types.SimpleNamespace(wait_for=lambda function, seconds: function())}):
                session.start_graph(True)
            self.assertEqual(set(configured), {"sink", "source"})
            self.assertTrue(session.result["distributionPolicyUnmodified"])


if __name__ == "__main__": unittest.main()
