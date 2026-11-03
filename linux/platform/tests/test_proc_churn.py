"""Real /proc descriptor close/reuse smoke; no HID hardware or root changes."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import broker_core as core


@unittest.skipUnless(sys.platform == "linux", "Linux /proc required")
class ProcChurnTests(unittest.TestCase):
    def test_ordinary_child_reuses_unrelated_descriptors_without_false_isolation_loss(self):
        if os.geteuid() == 0:
            self.skipTest("This smoke must run as an ordinary test account")
        program = """
import json, os, time
held = os.open('/dev/zero', os.O_RDONLY)
print(json.dumps({'fd': held, 'uid': os.geteuid()}), flush=True)
deadline = time.monotonic() + 5
while time.monotonic() < deadline:
    fd = os.open('/dev/null', os.O_RDONLY)
    os.close(fd)
    r, w = os.pipe()
    os.close(r)
    os.close(w)
"""
        child = subprocess.Popen([sys.executable, "-c", program], stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        class ChildProc:
            def iterdir(self):
                return iter([Path("/proc", str(child.pid))])
            def __truediv__(self, value):
                return Path("/proc") / value
        try:
            identity = json.loads(child.stdout.readline())
            self.assertEqual(identity["uid"], os.geteuid())
            start = core.process_start(child.pid)
            proc = ChildProc()
            # A stable target is still identified through the exact same real
            # proc walker while unrelated descriptors continuously change.
            owners = core.foreign_device_owners(os.stat("/dev/zero").st_rdev, os.getpid(), proc)
            self.assertIn((child.pid, start, str(identity["fd"])), owners)
            deadline = time.monotonic() + 1
            scans = 0
            while time.monotonic() < deadline:
                self.assertIsNone(child.poll(), "Churn child exited before the scan completed")
                self.assertEqual(core.foreign_device_owners(-1, os.getpid(), proc), [])
                scans += 1
            self.assertGreater(scans, 0)
        finally:
            if child.poll() is None:
                child.terminate()
            child.communicate(timeout=3)


if __name__ == "__main__":
    unittest.main()
