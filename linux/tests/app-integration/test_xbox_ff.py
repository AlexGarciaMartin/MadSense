import binascii
import copy
import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from xbox_ff import PHASES, CASE, verify_delivery, verify_result


def packet(left=0, right=0, selected=True):
    data = bytearray(78)
    data[:3] = bytes((0x31, 0, 0x10))
    data[3], data[5], data[6] = (3 if selected else 0), right, left
    data[-4:] = struct.pack("<I", binascii.crc32(b"\xa2" + data[:-4]))
    return data.hex()


def fixture():
    base = 10_000_000_000
    ticks = [0, 100_000, 200_000_000, 201_000_000, 501_000_000, 502_000_000,
             503_000_000, 504_000_000, 505_000_000]
    observed = dict(result="passed", bus=3, vendor=0x045e, product=0x028e,
        identityVerified=True, ffRumble=True, uploaded=True, started=True, stopped=True, erased=True,
        phaseTimingValid=True, explicitStopBeforeAutoExpiry=True, phaseClock="CLOCK_MONOTONIC",
        effectDurationMs=60000, phases=[dict(name=name, monotonicNs=base + tick) for name, tick in zip(PHASES, ticks)],
        marker=dict(strong=0x1234, weak=0x5678, expectedXboxLeftMotor=0x12, expectedXboxRightMotor=0x56))
    record = dict(exitCode=0, startedMonotonicNs=base - 1, endedMonotonicNs=base + 600_000_000, evidence=observed)
    rows = [dict(kind="output", monotonicNs=base + 100_000_000, hex=packet(0x12, 0x56)),
            dict(kind="output", monotonicNs=base + 300_000_000, hex=packet())]
    return record, rows


class XboxFfTests(unittest.TestCase):
    def test_exact_selected_marker_and_explicit_stop_preserve_raw_packets(self):
        record, rows = fixture()
        result = verify_delivery(record, rows)
        self.assertTrue(result["outputDeliveryVerified"])
        self.assertTrue(result["stopReportForwarded"])
        self.assertFalse(result["physicalActuationVerified"])
        self.assertEqual(result["marker"]["hex"], rows[0]["hex"])
        self.assertEqual(result["explicitStop"]["hex"], rows[1]["hex"])

    def test_stop_from_erase_close_or_previous_consumer_is_not_delivery(self):
        for timestamp in (9_000_000_000, 10_502_000_000, 10_504_000_000, 70_000_000_000):
            record, rows = fixture()
            rows[1]["monotonicNs"] = timestamp
            with self.subTest(timestamp=timestamp), self.assertRaisesRegex(RuntimeError, "Selected zero"):
                verify_delivery(record, rows)

    def test_unselected_motor_bytes_and_swapped_markers_are_rejected(self):
        for replacement in (packet(0x56, 0x12), packet(0x12, 0x56, False)):
            record, rows = fixture()
            rows[0]["hex"] = replacement
            with self.subTest(replacement=replacement), self.assertRaisesRegex(RuntimeError, "FF marker"):
                verify_delivery(record, rows)
        record, rows = fixture()
        rows[1]["hex"] = packet(selected=False)
        with self.assertRaisesRegex(RuntimeError, "Selected zero"):
            verify_delivery(record, rows)

    def test_invalid_crc_cannot_claim_forwarding(self):
        record, rows = fixture()
        corrupted = bytearray.fromhex(rows[0]["hex"]); corrupted[5] ^= 1
        rows[0]["hex"] = corrupted.hex()
        with self.assertRaisesRegex(RuntimeError, "CRC"):
            verify_delivery(record, rows)

    def test_api_failure_wrong_identity_and_automatic_expiry_are_rejected(self):
        for changes in (dict(stopped=False), dict(erased=False), dict(product=0x0ce6), dict(effectDurationMs=500),
                        dict(phaseTimingValid=False), dict(explicitStopBeforeAutoExpiry=False)):
            record, rows = fixture(); record["evidence"].update(changes)
            with self.subTest(changes=changes), self.assertRaises(RuntimeError):
                verify_delivery(record, rows)

    def test_missing_duplicate_reordered_and_short_stop_phases_are_rejected(self):
        record, rows = fixture()
        variants = [record["evidence"]["phases"][:-1], record["evidence"]["phases"] * 2,
                    list(reversed(record["evidence"]["phases"]))]
        shorter = copy.deepcopy(record["evidence"]["phases"])
        shorter[4]["monotonicNs"] = shorter[3]["monotonicNs"] + 1
        variants.append(shorter)
        for phases in variants:
            changed = copy.deepcopy(record); changed["evidence"]["phases"] = phases
            with self.subTest(phases=phases), self.assertRaises(RuntimeError):
                verify_delivery(changed, rows)

    def test_phase_window_cannot_escape_the_consumer_process(self):
        record, rows = fixture(); record["endedMonotonicNs"] = 10_300_000_000
        with self.assertRaisesRegex(RuntimeError, "process window"):
            verify_delivery(record, rows)

    def test_extension_requires_base_cases_consumer_cleanup_and_forwarding_scope(self):
        from qemu_app import REQUIRED_CASES
        record, rows = fixture()
        ready = dict(ownedGuestToken="token", vendorId=0x045e, productId=0x028e, hidrawPath=None,
                     usbParent="/sys/devices/platform/vhci_hcd.0/usb1/1-1", evdevPath="/dev/input/event1")
        ready_bytes = json.dumps(ready).encode()
        good = dict(result="passed", environment=dict(kind="owned-qemu-guest", instance="token"),
            applicationControllerExercised=True, applicationUiExercised=False, physicalDevices=False,
            radioInitialized=False, cases=[dict(name=name, passed=True) for name in REQUIRED_CASES],
            xboxForceFeedback=dict(requested=True, result="passed", readySha256=hashlib.sha256(ready_bytes).hexdigest(), consumer=dict(result="passed", ready=ready,
                accountProcessesAndFilesRemoved=True, cases=[dict(name=CASE, passed=True, delivery=verify_delivery(record, rows))])))
        verify_result(good, "token")
        for mutate in (
            lambda data: data["cases"].pop(),
            lambda data: data.update(cleanupError="pending recovery"),
            lambda data: data["xboxForceFeedback"].update(requested=False),
            lambda data: data["xboxForceFeedback"]["consumer"]["ready"].update(ownedGuestToken="different-guest"),
            lambda data: data["xboxForceFeedback"]["consumer"]["ready"].update(productId=0x0ce6),
            lambda data: data["xboxForceFeedback"]["consumer"]["ready"].update(hidrawPath="/dev/hidraw9"),
            lambda data: data["xboxForceFeedback"].pop("readySha256"),
            lambda data: data["xboxForceFeedback"]["consumer"].update(accountProcessesAndFilesRemoved=False),
            lambda data: data["xboxForceFeedback"]["consumer"].update(cleanupErrors=["ACL restore failed"]),
            lambda data: data["xboxForceFeedback"]["consumer"]["cases"].append(data["xboxForceFeedback"]["consumer"]["cases"][0]),
            lambda data: data["xboxForceFeedback"]["consumer"]["cases"][0]["delivery"].update(physicalActuationVerified=True),
        ):
            bad = copy.deepcopy(good); mutate(bad)
            with self.assertRaises(RuntimeError):
                verify_result(bad, "token")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ready.json"
            path.write_bytes(ready_bytes)
            verify_result(good, "token", path)
            path.write_bytes(ready_bytes + b" ")
            with self.assertRaisesRegex(RuntimeError, "readiness differs"):
                verify_result(good, "token", path)
            path.write_bytes(ready_bytes)
            bad = copy.deepcopy(good); bad["xboxForceFeedback"]["consumer"]["ready"]["evdevPath"] = "/dev/input/event2"
            with self.assertRaisesRegex(RuntimeError, "readiness differs"):
                verify_result(bad, "token", path)


if __name__ == "__main__":
    unittest.main()
