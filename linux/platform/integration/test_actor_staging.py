import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from actor_staging import stage_probe, verify_actor_execution


class ActorStagingTests(unittest.TestCase):
    def test_restrictive_publish_modes_do_not_leak_into_the_actor_tree(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            (source / "nested").mkdir()
            (source / "actor").write_bytes(b"actor payload")
            (source / "nested/data").write_bytes(b"runtime data")
            for path in (source / "actor", source / "nested/data"):
                path.chmod(0o600)
            executable = stage_probe(source, root / "staged", "actor")
            self.assertEqual(executable.read_bytes(), b"actor payload")
            self.assertEqual((root / "staged/nested/data").read_bytes(), b"runtime data")
            if sys.platform == "linux":
                for name, mode in (("staged", 0o755), ("staged/actor", 0o755),
                                   ("staged/nested", 0o755), ("staged/nested/data", 0o644)):
                    self.assertEqual(stat.S_IMODE((root / name).stat().st_mode), mode)
            with self.assertRaises(FileExistsError):
                stage_probe(source, root / "staged", "actor")

    @unittest.skipUnless(sys.platform == "linux", "Native Linux filesystem and exec check")
    def test_real_elf_can_execute_after_copy_from_nonexecutable_publish_tree(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            shutil.copyfile("/usr/bin/true", source / "actor")
            (source / "actor").chmod(0o600)
            executable = stage_probe(source, root / "staged", "actor")
            self.assertEqual(subprocess.run([str(executable)], timeout=5).returncode, 0)

    @unittest.skipUnless(sys.platform == "linux", "Native symlink check")
    def test_publish_symlink_cannot_copy_outside_the_declared_tree(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "source").mkdir()
            (root / "source/actor").symlink_to("/usr/bin/true")
            with self.assertRaisesRegex(ValueError, "link or special"):
                stage_probe(root / "source", root / "staged", "actor")
            self.assertFalse((root / "staged").exists())

    def test_non_root_staging_fails_before_exec_and_preserves_diagnostics(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = root / "evidence.json"
            info = SimpleNamespace(st_uid=123, st_gid=123, st_mode=stat.S_IFDIR | 0o755, st_dev=1, st_ino=2)
            with patch("actor_staging.Path.lstat", return_value=info), patch("actor_staging.subprocess.run") as execute:
                with self.assertRaisesRegex(RuntimeError, "root-owned"):
                    verify_actor_execution(root, root / "actor", SimpleNamespace(pw_uid=1001, pw_gid=1001), evidence)
            execute.assert_not_called()
            value = json.loads(evidence.read_text())
            self.assertEqual(value["result"], "failed")
            self.assertEqual(value["root"]["uid"], 123)


if __name__ == "__main__":
    unittest.main()
