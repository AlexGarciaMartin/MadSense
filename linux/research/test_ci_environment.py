import os
from pathlib import Path
import unittest
from unittest.mock import patch
from ci_environment import validate_environment

TOKEN = "a480ccae-aeae-46e2-bd6a-4d244b1be4a0"


class ScopeTests(unittest.TestCase):
    def test_unmarked_linux_machine_is_rejected(self):
        with patch("sys.platform", "linux"), patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError):
                validate_environment()

    def test_guest_works_without_github_environment_only_with_matching_identity(self):
        def read(path):
            return TOKEN if path.name == "madsense-ci-owner" else "QEMU"
        with patch("sys.platform", "linux"), patch.dict(os.environ, {}, clear=True), \
                patch.object(Path, "read_text", read), \
                patch("subprocess.check_output", return_value=TOKEN):
            self.assertEqual(validate_environment(TOKEN)["kind"], "owned-qemu-guest")

    def test_guest_marker_cannot_override_wrong_dmi_identity(self):
        with patch("sys.platform", "linux"), patch.object(Path, "read_text", return_value=TOKEN), \
                patch("subprocess.check_output", return_value="00000000-0000-0000-0000-000000000000"):
            with self.assertRaises(RuntimeError):
                validate_environment(TOKEN)


if __name__ == "__main__":
    unittest.main()
