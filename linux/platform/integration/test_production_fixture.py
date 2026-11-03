import ast
import binascii
import json
from pathlib import Path
import struct
import unittest
import copy

from production_fixture import descriptor, features, fixture_identity_matches, input_report, manifest
from qemu_core import REQUIRED_CORE_CASES, verify_core_result


class FixtureTests(unittest.TestCase):
    def test_native_pass_record_accepts_and_preserves_actual_observer_evidence(self):
        # Execute the runner's real recorder and callsite without importing its
        # Linux-only host operations. py_compile alone cannot catch this error.
        path = Path(__file__).with_name("run_core_guest.py")
        tree = ast.parse(path.read_text())
        main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main")
        recorder = next(node for node in main.body if isinstance(node, ast.FunctionDef) and node.name == "passed")
        calls = [node for node in ast.walk(main) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Name) and node.func.id == "passed"]
        call = next(node for node in calls if any(key.arg == "sensorObservers" for key in node.keywords))
        saved = []
        scope = dict(result={"cases": []}, observers=[1, 2], retained={1, 2, 3}, store_count=4,
                     save=lambda name, value: saved.append((name, copy.deepcopy(value))))
        exec(compile(ast.Module(body=[recorder], type_ignores=[]), str(path), "exec"), scope)
        scope["passed"]("simple_case")
        eval(compile(ast.Expression(call), str(path), "eval"), scope)
        expected = dict(name="readonly_sensor_observers_allow_acquire_and_all_grabs_are_stored",
                        passed=True, sensorObservers=2, storedGrabs=3, descriptorStoreCount=4)
        self.assertEqual(saved[-1], ("result.json", {"cases": [dict(name="simple_case", passed=True), expected]}))
        self.assertEqual(len(saved), 2)

    def test_kernel_pairing_mac_replaces_uniq_without_losing_exact_fixture_identity(self):
        label = "madsense-app-owned-uuid"
        prefix = f"HID_NAME={label}\nHID_PHYS=madsense-core-ci\n"
        self.assertTrue(fixture_identity_matches(prefix + "HID_UNIQ=" + label, label))
        self.assertTrue(fixture_identity_matches(prefix + "HID_UNIQ=02:FA:11:22:33:44", label))
        self.assertFalse(fixture_identity_matches(prefix + "HID_UNIQ=02:D5:00:00:00:00", label))
        self.assertFalse(fixture_identity_matches(prefix.replace(label, "other") + "HID_UNIQ=02:FA:11:22:33:44", label))
        self.assertFalse(fixture_identity_matches(prefix + "HID_UNIQ=02:D5:11:22:33:44", label))
        self.assertFalse(fixture_identity_matches(prefix.replace("madsense-core-ci", "other") + "HID_UNIQ=" + label, label))

    def test_manifest_matches_generated_source(self):
        self.assertEqual(json.loads(Path(__file__).with_name("fixtures.json").read_text()), manifest())

    def test_descriptor_sizes_cover_complete_transport_reports(self):
        # Independent HID short-item walk. Count input/output/feature bits per
        # report id, including the report-id byte only once.
        data, offset, report, size, count = descriptor(), 0, 0, 0, 0
        lengths = {}
        while offset < len(data):
            tag = data[offset]; offset += 1
            length = (0, 1, 2, 4)[tag & 3]
            value = int.from_bytes(data[offset:offset + length], "little"); offset += length
            kind = tag & 0xFC
            if kind == 0x84: report = value
            elif kind == 0x74: size = value
            elif kind == 0x94: count = value
            elif kind in (0x80, 0x90, 0xB0):
                key = (kind, report)
                lengths[key] = lengths.get(key, 8) + size * count
        self.assertEqual(lengths[(0x80, 0x31)], 78 * 8)
        for report, size in ((0x31, 78), (0x32, 142), (0x35, 334), (0x36, 398)):
            self.assertEqual(lengths[(0x90, report)], size * 8)
        for report, size in ((5, 41), (9, 20), (0x20, 64)):
            self.assertEqual(lengths[(0xB0, report)], size * 8)

    def test_input_counter_motion_and_crc_are_not_zero_fixtures(self):
        neutral, marker = input_report(False, 257), input_report(True, 257)
        self.assertEqual(neutral[8], 1)
        self.assertEqual(struct.unpack_from("<I", neutral, 29)[0], 257 * 3000)
        self.assertEqual(struct.unpack_from("<hhhhhh", marker, 17), (-1234, 2345, -3456, 0, 0, 8192))
        for data in (neutral, marker):
            self.assertEqual(struct.unpack_from("<I", data, 74)[0], binascii.crc32(b"\xa1" + data[:74]))
        self.assertNotEqual(neutral, marker)

    def test_calibration_denominators_nonzero_and_pairing_distinct(self):
        reports = features()
        for position in (7, 11, 15, 23, 27, 31):
            high, low = struct.unpack_from("<hh", reports[5], position)
            self.assertGreater(high - low, 0)
        self.assertNotEqual(reports[9][1:7], reports[9][10:16])

    def test_bluetooth_features_carry_kernel_a3_crc(self):
        for report in features().values():
            crc = struct.unpack_from("<I", report, len(report) - 4)[0]
            self.assertEqual(crc, binascii.crc32(b"\xa3" + report[:-4]))
            self.assertNotEqual(crc, binascii.crc32(b"\xa1" + report[:-4]))
            self.assertNotEqual(crc, binascii.crc32(b"\xa2" + report[:-4]))

    def test_south_only_fixture_changes_only_button_and_crc(self):
        neutral, south = input_report(), input_report(south_only=True)
        self.assertEqual(south[9], 0x28)
        self.assertEqual(neutral[:9], south[:9])
        self.assertEqual(neutral[10:74], south[10:74])

    def test_evidence_requires_every_stage_and_rejects_hardware_or_ui_claims(self):
        good = dict(result="passed", environment={"kind": "owned-qemu-guest", "instance": "token"},
                    physicalDevices=False, radioInitialized=False, applicationUiExercised=False,
                    cases=[dict(name=name, passed=True) for name in REQUIRED_CORE_CASES])
        verify_core_result(good, "token")
        for change in ({"cases": good["cases"][:-1]}, {"cases": good["cases"] * 2},
                       {"physicalDevices": True}, {"applicationUiExercised": True},
                       {"cleanupError": "Lease remains"}, {"result": "failed"}):
            bad = copy.deepcopy(good); bad.update(change)
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                verify_core_result(bad, "token")


if __name__ == "__main__":
    unittest.main()
