import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import actor_files


class ActorFileContractTests(unittest.TestCase):
    def test_parent_always_drops_to_actor_credentials(self):
        with patch.object(actor_files.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, b"null", b"")) as run:
            self.assertIsNone(actor_files.call("/opt/owned/actor", 1001, 1002, "read", dict(name="status.json")))
        self.assertEqual(run.call_args.kwargs["user"], 1001)
        self.assertEqual(run.call_args.kwargs["group"], 1002)
        self.assertEqual(run.call_args.kwargs["extra_groups"], [])


@unittest.skipUnless(sys.platform == "linux" and os.geteuid() != 0, "Requires ordinary Linux UID")
class NativeActorFilesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.actor = self.root / "actor"; self.actor.mkdir(); (self.actor / "commands").mkdir()
        self.outside = self.root / "outside"; self.outside.write_text("unchanged")

    def invoke(self, operation, request):
        return subprocess.run([sys.executable, "-I", "-c", actor_files.PROGRAM, str(self.actor), operation],
            input=json.dumps(request), text=True, capture_output=True, timeout=5)

    def test_atomic_command_and_bounded_regular_snapshot(self):
        result = self.invoke("command", dict(sequence=1, operation="resume"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads((self.actor / "commands/000001.json").read_text()), dict(operation="resume"))
        self.assertFalse((self.actor / "commands/000001.json.tmp").exists())
        self.assertEqual(set(json.loads(self.invoke("snapshot", {}).stdout)), {"commands/000001.json"})

    def test_symlink_temporary_or_directory_cannot_write_outside(self):
        (self.actor / "commands/000001.json.tmp").symlink_to(self.outside)
        self.assertNotEqual(self.invoke("command", dict(sequence=1, operation="resume")).returncode, 0)
        (self.actor / "commands/000001.json.tmp").unlink(); (self.actor / "commands").rmdir()
        (self.actor / "commands").symlink_to(self.root, target_is_directory=True)
        self.assertNotEqual(self.invoke("command", dict(sequence=2, operation="pause")).returncode, 0)
        self.assertEqual(self.outside.read_text(), "unchanged")
        self.assertFalse((self.root / "000002.json").exists())

    def test_snapshot_and_status_reject_symlinks_and_hardlinks(self):
        (self.actor / "status.json").symlink_to(self.outside)
        self.assertNotEqual(self.invoke("read", dict(name="status.json")).returncode, 0)
        self.assertNotEqual(self.invoke("snapshot", {}).returncode, 0)
        (self.actor / "status.json").unlink(); os.link(self.outside, self.actor / "status.json")
        self.assertNotEqual(self.invoke("read", dict(name="status.json")).returncode, 0)
        self.assertEqual(self.outside.read_text(), "unchanged")


if __name__ == "__main__": unittest.main()
