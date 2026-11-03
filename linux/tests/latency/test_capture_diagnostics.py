import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import capture_diagnostics as diagnostics


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def child(self, code, **kwargs):
        return diagnostics._capture_command(self.root, "child", [sys.executable, "-c", code], **kwargs)

    def test_nonzero_child_retains_both_streams_and_is_reaped(self):
        result = self.child("import sys; print('out'); print('err', file=sys.stderr); sys.exit(7)")
        self.assertEqual(result["returncode"], 7)
        self.assertTrue(result["childReaped"])
        self.assertFalse(result["complete"])
        self.assertEqual((self.root / "child.stdout").read_text().strip(), "out")
        self.assertEqual((self.root / "child.stderr").read_text().strip(), "err")

    def test_simultaneous_large_streams_are_bounded_without_pipe_deadlock(self):
        code = ("import os,threading; "
                "t=threading.Thread(target=lambda:os.write(2,b'e'*2000000)); t.start(); "
                "os.write(1,b'o'*2000000); t.join()")
        result = self.child(code, timeout=3, limit=8192)
        self.assertTrue(result["childReaped"])
        self.assertTrue(result["outputLimitReached"])
        self.assertFalse(result["complete"])
        self.assertTrue(any(result[name]["truncated"] for name in ("stdout", "stderr")))
        for name in ("stdout", "stderr"):
            self.assertLessEqual((self.root / f"child.{name}").stat().st_size, 8192)

    def test_deadline_kills_and_reaps_only_started_child(self):
        start = time.monotonic()
        result = self.child("import time; print('before wait',flush=True); time.sleep(30)", timeout=0.4)
        self.assertLess(time.monotonic() - start, 4)
        self.assertTrue(result["timedOut"])
        self.assertTrue(result["childReaped"])
        self.assertFalse(result["complete"])
        self.assertIn(b"before wait", (self.root / "child.stdout").read_bytes())

    def test_guard_rejection_starts_no_process_or_output(self):
        output = self.root / "capture"
        with patch.object(diagnostics, "_owned_guest", side_effect=RuntimeError("wrong guest")), \
             patch.object(diagnostics.subprocess, "Popen") as popen:
            result = diagnostics.capture_diagnostics(output, "wrong")
        popen.assert_not_called()
        self.assertFalse(output.exists())
        self.assertFalse(result["complete"])
        self.assertIn("wrong guest", result["errors"][0])

    def test_existing_evidence_is_not_overwritten(self):
        output = self.root / "capture"
        output.mkdir()
        (output / "result.json").write_text("original")
        with patch.object(diagnostics, "_owned_guest", return_value={"kind": "owned-qemu-guest"}), \
             patch.object(diagnostics.subprocess, "Popen") as popen:
            result = diagnostics.capture_diagnostics(output, "owned")
        popen.assert_not_called()
        self.assertFalse(result["complete"])
        self.assertEqual((output / "result.json").read_text(), "original")

    def test_command_failure_is_recorded_and_does_not_hide_other_commands(self):
        commands = (("one", (sys.executable, "-c", "raise SystemExit(3)")),
                    ("two", (sys.executable, "-c", "print('later command')")))
        output = self.root / "capture"
        with patch.object(diagnostics, "_owned_guest", return_value={"kind": "owned-qemu-guest"}), \
             patch.object(diagnostics, "COMMANDS", commands):
            result = diagnostics.capture_diagnostics(output, "owned")
        self.assertFalse(result["complete"])
        self.assertEqual(result["commands"]["one"]["returncode"], 3)
        self.assertTrue(result["commands"]["two"]["complete"])
        self.assertEqual(json.loads((output / "result.json").read_text()), result)


if __name__ == "__main__":
    unittest.main()
