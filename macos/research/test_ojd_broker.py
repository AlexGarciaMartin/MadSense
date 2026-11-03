"""Offline tests for gates that must hold before any downloaded app executes."""
import copy
import unittest
from ojd_broker_probe import BUNDLE, CAPABILITY, TEAM, Blocked, check_empty_inventory, check_identity


class BrokerGateTests(unittest.TestCase):
    def test_profile_and_executable_must_both_grant_exact_identity(self):
        executable = {CAPABILITY: True, "com.apple.application-identifier": f"{TEAM}.{BUNDLE}",
                      "com.apple.developer.team-identifier": TEAM, "com.apple.security.app-sandbox": False}
        profile = {"Entitlements": executable.copy(), "ProvisionsAllDevices": True}
        check_identity(executable, profile)
        for target in ("executable", "profile"):
            for key, invalid in ((CAPABILITY, False), (CAPABILITY, 1), ("com.apple.application-identifier", "other.app"),
                                 ("com.apple.developer.team-identifier", "other-team")):
                with self.subTest(target=target, key=key):
                    changed_executable, changed_profile = executable.copy(), copy.deepcopy(profile)
                    (changed_executable if target == "executable" else changed_profile["Entitlements"])[key] = invalid
                    with self.assertRaises(Blocked):
                        check_identity(changed_executable, changed_profile)
        profile["ProvisionsAllDevices"] = False
        with self.assertRaises(Blocked):
            check_identity(executable, profile)
        profile["ProvisionsAllDevices"] = True
        for sandbox in (True, 1, None):
            with self.subTest(sandbox=sandbox), self.assertRaises(Blocked):
                check_identity({**executable, "com.apple.security.app-sandbox": sandbox}, profile)

    def test_no_hardware_or_existing_virtual_feed_may_pass_preflight(self):
        empty = {"unsafeHIDCount": 0, "usbDeviceCount": 0, "bluetoothControllerCount": 0, "feeds": []}
        check_empty_inventory(empty)
        for key in empty:
            with self.subTest(key=key):
                present = dict(empty)
                present[key] = [{"registryID": 42}] if key == "feeds" else 1
                with self.assertRaises(Blocked):
                    check_empty_inventory(present)
                missing = dict(empty)
                del missing[key]
                with self.assertRaises(Blocked):
                    check_empty_inventory(missing)
        for value in (False, True, None, 0.0, "0", -1):
            with self.subTest(value=value), self.assertRaises(Blocked):
                check_empty_inventory({**empty, "unsafeHIDCount": value})


if __name__ == "__main__":
    unittest.main()
