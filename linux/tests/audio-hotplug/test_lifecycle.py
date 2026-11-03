import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import Mock

from desktop import PrivateDesktop
from qemu_audio_hotplug import run_stage


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


if __name__ == "__main__": unittest.main()
