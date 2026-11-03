import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from qemu_app import REQUIRED_CASES, prepare_app_payload, verify_app_result


class PayloadTests(unittest.TestCase):
    def test_guest_entrypoint_imports_from_the_actual_staged_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            neutral, app = root / "neutral", root / "app"
            neutral.mkdir(); app.mkdir()
            (neutral / "MadSense.Linux.Probe").write_bytes(b"not executed by this packaging test")
            (app / "MadSense.Linux.AppProbe").write_bytes(b"not executed by this packaging test")
            payload = root / "payload"
            prepare_app_payload(payload, neutral, app)
            entry = payload / "linux/tests/app-integration/run_app_guest.py"
            # Fresh isolated interpreter prevents imported host modules from
            # concealing missing guest files. Only platform-native modules are
            # stubbed on Windows; no fixture, actor or privileged action runs.
            code = "import runpy,sys,types\n"
            if sys.platform != "linux":
                code += "sys.modules['fcntl']=types.ModuleType('fcntl')\nsys.modules['pwd']=types.ModuleType('pwd')\n"
            code += "runpy.run_path(sys.argv[1], run_name='payload_import_check')\n"
            result = subprocess.run([sys.executable, "-I", "-c", code, str(entry)], cwd=payload,
                                    capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_gate_requires_controller_scope_all_cases_and_clean_teardown(self):
        good = dict(result="passed", environment=dict(kind="owned-qemu-guest", instance="owned"),
                    applicationControllerExercised=True, applicationUiExercised=False,
                    physicalDevices=False, radioInitialized=False,
                    cases=[dict(name=name, passed=True) for name in sorted(REQUIRED_CASES)])
        verify_app_result(good, "owned")
        for change in ({"cases": good["cases"][:-1]}, {"cases": good["cases"] * 2}, {"cleanupError": "pending"},
                       {"applicationControllerExercised": False}, {"applicationUiExercised": True}, {"physicalDevices": True}):
            value = copy.deepcopy(good); value.update(change)
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                verify_app_result(value, "owned")


if __name__ == "__main__":
    unittest.main()
