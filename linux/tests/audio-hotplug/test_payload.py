from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from qemu_audio_hotplug import add_payload


class PayloadTests(unittest.TestCase):
    def test_actual_staged_entrypoints_import_without_source_checkout(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); payload = root / "payload"; payload.mkdir()
            hashes = add_payload(payload)
            self.assertIn("linux/research/pipewire_defaults.py", hashes)
            self.assertIn("linux/tests/audio-hotplug/desktop.py", hashes)
            directory = payload / "linux/tests/audio-hotplug"
            command = ("import pathlib,sys; p=pathlib.Path(sys.argv[1]); sys.path.insert(0,str(p)); "
                "import run_guest,desktop,actor,evidence,qemu_audio_hotplug; "
                "assert all(pathlib.Path(m.__file__).parent==p for m in (run_guest,desktop,actor,evidence,qemu_audio_hotplug)); "
                "assert callable(run_guest.verify_result)")
            result = subprocess.run([sys.executable, "-I", "-c", command, str(directory)], cwd=root,
                capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__": unittest.main()
