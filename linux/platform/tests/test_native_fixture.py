from pathlib import Path
import stat
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from native_broker_integration import complete_generic_nodes, prepare_generic_driver


class GenericFixtureTests(unittest.TestCase):
    def test_cached_loader_is_restarted_after_policy_and_before_module_unload(self):
        state = dict(policy=False, cached_block=False, loaded=True)
        events = []
        def fixture_file(path, text):
            self.assertEqual(path, Path("/run/modprobe.d/madsense-ci-fixture.conf"))
            self.assertIn("blacklist hid_playstation\n", text)
            state["policy"] = True
        def command(label, argv):
            events.append(label)
            if argv == ["systemctl", "restart", "systemd-udevd.service"]:
                state["cached_block"] = state["policy"]
            if argv == ["modprobe", "-r", "hid_playstation"]:
                state["loaded"] = False
                # A queued HID modalias is processed by the cached builtin.
                if not state["cached_block"]:
                    state["loaded"] = True
        with patch.object(Path, "exists", return_value=True):
            prepare_generic_driver(command, fixture_file)
        self.assertFalse(state["loaded"])
        self.assertEqual(events[:3], ["restart-fixture-udev", "settle-before-fixtures", "unload-fixture-driver"])

    def test_sysfs_only_or_rebound_fixture_is_not_ready(self):
        nodes = [Path("/dev/hidraw0"), Path("/dev/input/event0")]
        parent = Path("/sys/devices/virtual/misc/uhid/fixture")
        with patch.object(Path, "resolve", return_value=Path("/sys/bus/hid/drivers/hid-generic")):
            with patch.object(Path, "stat", side_effect=FileNotFoundError):
                self.assertFalse(complete_generic_nodes(parent, nodes))
            with patch.object(Path, "stat", return_value=SimpleNamespace(st_mode=stat.S_IFCHR | 0o600)):
                self.assertTrue(complete_generic_nodes(parent, nodes))
                self.assertFalse(complete_generic_nodes(parent, nodes[:1]))
        with patch.object(Path, "resolve", return_value=Path("/sys/bus/hid/drivers/playstation")):
            self.assertFalse(complete_generic_nodes(parent, nodes))


if __name__ == "__main__":
    unittest.main()
