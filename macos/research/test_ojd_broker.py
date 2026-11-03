"""Offline tests for gates that must hold before any downloaded app executes."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from ojd_broker_probe import BUNDLE, CAPABILITY, TEAM, Blocked, Experiment, check_empty_inventory, check_identity, check_inventory_capture


def inventory_fixture():
    return {"schemaVersion": 2, "mode": "read-only-registry", "enumerationComplete": True, "queryErrors": [],
            "unsafeHIDCount": 0, "usbDeviceCount": 0, "usbInterfaceCount": 0, "bluetoothControllerCount": 1,
            "feeds": [], "hidInventory": [], "usbInventory": [],
            "bluetoothInventory": [{"class": "IOBluetoothHCIController", "BluetoothTransportConnected": False, "metadataErrors": []}],
            "bluetoothTransportQueries": {name: [] for name in ("IOBluetoothHostControllerTransport", "IOBluetoothHostController",
                "IOBluetoothHostControllerUSBTransport", "IOBluetoothHostControllerUARTTransport", "IOBluetoothHostControllerPCIeTransport")},
            "vmProvenance": {"source": "sysctl:kern.hv_vmm_present", "hypervisorPresent": True, "queryStatus": 0,
                             "queryErrno": 0, "valueSize": 4, "complete": True, "provesHardwareIsolation": False}}


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

    def test_disconnected_generic_controller_does_not_relax_existing_gate(self):
        inventory = inventory_fixture()
        check_inventory_capture(inventory)
        with self.assertRaises(Blocked):
            check_empty_inventory(inventory)
        controller = inventory["bluetoothInventory"][0]
        controller["BluetoothTransportConnected"] = None
        controller["metadataErrors"] = [{"field": "BluetoothTransportConnected", "reason": "missing"}]
        check_inventory_capture(inventory)  # Unknown remains explicit evidence, never a safe zero.
        with self.assertRaises(Blocked):
            check_empty_inventory(inventory)

    def test_capture_rejects_incomplete_or_wrongly_typed_evidence(self):
        for key, value in (("enumerationComplete", False), ("enumerationComplete", 1), ("queryErrors", ["failed"]),
                           ("usbInterfaceCount", False), ("usbInterfaceCount", 1), ("bluetoothControllerCount", True),
                           ("bluetoothTransportQueries", {})):
            with self.subTest(key=key, value=value), self.assertRaises(Blocked):
                check_inventory_capture({**inventory_fixture(), key: value})
        for value in (0, 1, "false", None):
            data = inventory_fixture()
            data["bluetoothInventory"][0]["BluetoothTransportConnected"] = value
            with self.subTest(connected=value), self.assertRaises(Blocked):
                check_inventory_capture(data)
        data = inventory_fixture()
        data["vmProvenance"]["provesHardwareIsolation"] = True
        with self.assertRaises(Blocked):
            check_inventory_capture(data)

    def test_partial_evidence_cannot_pass_either_parser_even_with_zero_counts(self):
        data = inventory_fixture()
        data.update(bluetoothControllerCount=0, bluetoothInventory=[], enumerationComplete=False,
                    queryErrors=[{"query": "IOBluetoothHostControllerTransport", "operation": "matching"}])
        data["bluetoothTransportQueries"]["IOBluetoothHostControllerTransport"] = None
        for check in (check_inventory_capture, check_empty_inventory):
            with self.subTest(check=check.__name__), self.assertRaises(Blocked):
                check(data)

    def test_inventory_mode_cannot_download_or_execute_original_release(self):
        data = json.dumps(inventory_fixture()).encode()
        with tempfile.TemporaryDirectory() as directory:
            experiment = Experiment(Path(directory))
            calls = []
            def capture(label, args, **kwargs):
                calls.append((label, args))
                return 0, data if label == "empty-host" else b"", b""
            with patch("ojd_broker_probe.sys.platform", "darwin"), patch.dict("os.environ", {"GITHUB_ACTIONS": "true"}), \
                 patch("ojd_broker_probe.urllib.request.urlopen", side_effect=AssertionError("no download")), \
                 patch.object(experiment, "background", side_effect=AssertionError("no background app")), \
                 patch.object(experiment, "execute", side_effect=AssertionError("no broker experiment")), \
                 patch.object(experiment, "run", side_effect=capture), patch.object(experiment, "record") as record:
                self.assertEqual(experiment.execute_inventory(), 0)
                self.assertEqual([label for label, _ in calls], ["build-observer", "observer-parser-self-test", "empty-host"])
                self.assertEqual(calls[-1][1][-1], "inventory")
                result = record.call_args.kwargs
                self.assertEqual(result["currentUnchangedGate"], "blocked")
                for field in ("originalReleaseExecuted", "signingRechecked", "physicalDeviceOpened", "hardwareIsolationValidated"):
                    self.assertIs(result[field], False)


if __name__ == "__main__":
    unittest.main()
