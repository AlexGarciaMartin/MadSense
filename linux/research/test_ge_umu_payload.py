import hashlib
from pathlib import Path
import tempfile
import subprocess
import sys
import unittest
from unittest.mock import patch

from ge_umu_payload import stage


class GePrivatePayloadTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.cache = self.root / "cache"; self.cache.mkdir()
        self.payload = self.root / "private-work/payload"; self.payload.mkdir(parents=True)
        self.output = self.root / "collected"
        (self.cache / "archive.tar").write_bytes(b"locked-private-payload")
        self.sha = hashlib.sha256((self.cache / "archive.tar").read_bytes()).hexdigest()
        self.lock = dict(downloads=[dict(name="archive.tar")])

    def verify(self, directory, _lock):
        digest = hashlib.sha256((Path(directory) / "archive.tar").read_bytes()).hexdigest()
        if digest != self.sha: raise ValueError("Changed copy")
        return [dict(name="archive.tar", sha256=digest)]

    def test_copy_is_verified_inside_private_payload_and_never_under_evidence(self):
        from qemu_consumers import stage_consumer_research
        stage_consumer_research(self.payload)
        with patch("ge_umu_payload.load_lock", return_value=self.lock), \
             patch("ge_umu_payload.verify_bundle", side_effect=self.verify) as verify:
            result = stage(self.cache, self.payload, self.output)
        self.assertEqual(verify.call_count, 2)
        self.assertEqual((self.payload / "ge-umu-private-cache/archive.tar").read_bytes(), b"locked-private-payload")
        self.assertFalse(self.output.exists())
        self.assertFalse(result["runtimeCacheCollected"])
        self.assertFalse(result["runtimeExecuted"])
        self.assertTrue((self.payload / "linux/research/ge_umu_consumer.py").is_file())
        research = self.payload / "linux/research"
        command = ("import pathlib,sys; p=pathlib.Path(sys.argv[1]); sys.path.insert(0,str(p)); "
                   "import ge_umu_consumer,ge_umu_session,run_wine_consumer; "
                   "assert all(pathlib.Path(m.__file__).parent==p for m in "
                   "(ge_umu_consumer,ge_umu_session,run_wine_consumer)); "
                   "assert callable(run_wine_consumer.run_windows_cases)")
        result = subprocess.run([sys.executable, "-I", "-c", command, str(research)], cwd=self.root,
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_evidence_overlap_and_payload_cache_overlap_are_rejected_before_copy(self):
        for output in (self.payload, self.payload.parent, self.payload / "evidence", self.cache, self.cache / "evidence"):
            with patch("ge_umu_payload.verify_bundle") as verify, self.assertRaises(ValueError):
                stage(self.cache, self.payload, output)
            verify.assert_not_called()
        with self.assertRaises(ValueError): stage(self.cache, self.cache, self.output)

    def test_changed_copy_and_existing_destination_fail(self):
        import shutil
        copy = shutil.copyfile
        def corrupt(source, destination):
            copy(source, destination)
            Path(destination).write_bytes(b"tampered")
        with patch("ge_umu_payload.load_lock", return_value=self.lock), \
             patch("ge_umu_payload.verify_bundle", side_effect=self.verify), \
             patch("ge_umu_payload.shutil.copyfile", side_effect=corrupt), self.assertRaises(ValueError):
            stage(self.cache, self.payload, self.output)
        with patch("ge_umu_payload.verify_bundle") as verify, self.assertRaises(ValueError):
            stage(self.cache, self.payload, self.output)
        verify.assert_not_called()


if __name__ == "__main__":
    unittest.main()
