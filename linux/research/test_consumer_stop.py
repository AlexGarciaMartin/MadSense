import base64
import copy
import unittest

from consumer_stop import (ARRAY_FIELDS, BOOLEAN_FIELDS, INTEGER_FIELDS,
                           decoded_zero, explicit_stop_delivery)
import test_consumer_phases as phase_tests
from test_consumer_phases import bt_row, usb_row


def zero_value():
    return ({name: False for name in BOOLEAN_FIELDS} | {name: 0 for name in INTEGER_FIELDS}
            | {name: base64.b64encode(bytes(11)).decode() for name in ARRAY_FIELDS})


class ExplicitStopTests(unittest.TestCase):
    def setUp(self):
        phase = phase_tests.PhaseEvidenceTests(); phase.setUp()
        self.observed = phase.observed | dict(sdlMajor=3, version="3.2.28", result="passed",
            identityVerified=True, inputMatches=True, path="/dev/hidraw7", capabilities=dict(rgbLed=False),
            marker=dict(rumbleAccepted=True, stopAccepted=True))
        self.observed["phases"] = [row for row in self.observed["phases"] if not row["name"].startswith("led-")]
        self.record = phase.command | dict(exitCode=0, evidence=self.observed)
        firmware = bytearray(64); firmware[44:46] = (0x0630).to_bytes(2, "little")
        self.ready = dict(ownedGuestToken="one", vendorId=0x054c, productId=0x0ce6,
                          hidrawPath="/dev/hidraw7", features={"20": firmware.hex()})
        self.core_ready = dict(ownedGuestToken="one", timestampFrequency=1_000_000_000)
        self.usb = [usb_row(150_000_000, left=18, right=86),
                    dict(kind="virtual-output", timestamp=400_000_000, value=zero_value())]
        self.bt = [bt_row(151_000_000, left=18, right=86), bt_row(401_000_000, flags=0)]

    def proof(self):
        return explicit_stop_delivery(self.record, self.ready, self.core_ready, self.usb, self.bt)

    def test_isolated_zero_report_proves_forwarding_only(self):
        result = self.proof()
        self.assertTrue(result["stopReportForwarded"])
        self.assertEqual(result["stopSemantics"], "explicit-selector-clear")
        for key in ("physicalActuationVerified", "audioHapticsResumptionVerified", "completeUsbBytesCaptured",
                    "causalOneToOneMappingVerified", "latencyValidated"):
            self.assertFalse(result[key])

    def test_only_individually_audited_sdl2_versions_can_use_selector_clear(self):
        self.observed["sdlMajor"] = 2
        for version in ("2.30.0", "2.32.4"):
            self.observed["version"] = version
            self.assertTrue(self.proof()["stopReportForwarded"])
        for version in ("2.30.1", "2.32.3", "2.32.5", "3.2.28", None):
            self.observed["version"] = version
            with self.subTest(version=version), self.assertRaises(RuntimeError): self.proof()

    def test_enabled_raw_observer_requires_one_exact_full_stop_report_in_its_phase(self):
        self.core_ready["rawOutputObservationEnabled"] = True
        with self.assertRaises(RuntimeError): self.proof()
        raw = dict(kind="virtual-output-raw", timestamp=399_000_000,
                   value=dict(length=48, hex=(b"\x02" + bytes(47)).hex()))
        self.usb.append(raw)
        result = self.proof()
        self.assertTrue(result["completeUsbBytesCaptured"])
        self.assertTrue(result["requiresCleanCoreRawObserverAtShutdown"])
        for value in (dict(length=48, hex=(b"\x02" + bytes(46) + b"\x01").hex()),
                      dict(length=49, hex=(b"\x02" + bytes(48)).hex()),
                      dict(length=48, hex=(b"\x03" + bytes(47)).hex())):
            raw["value"] = value
            with self.assertRaises(RuntimeError): self.proof()
        raw["value"] = dict(length=48, hex=(b"\x02" + bytes(47)).hex())
        raw["timestamp"] = 705_000_000
        with self.assertRaises(RuntimeError): self.proof()

    def test_every_active_decoded_field_and_missing_unknown_or_wrong_types_fail(self):
        for key in BOOLEAN_FIELDS | INTEGER_FIELDS | ARRAY_FIELDS:
            value = zero_value()
            value[key] = True if key in BOOLEAN_FIELDS else (1 if key in INTEGER_FIELDS else
                         base64.b64encode(b"\x01" + bytes(10)).decode())
            with self.subTest(key=key), self.assertRaises(RuntimeError): decoded_zero(value)
        for value in (zero_value() | dict(unknown=0), {k:v for k,v in zero_value().items() if k != "audioControl"},
                      zero_value() | dict(rumbleLeft=False), zero_value() | dict(resetLights=0),
                      zero_value() | dict(rightTriggerEffect=base64.b64encode(bytes(10)).decode())):
            with self.assertRaises(RuntimeError): decoded_zero(value)

    def test_rgb_only_zero_motor_fields_and_selected_zero_are_not_selector_clear_proof(self):
        for flags, flags1 in ((0, 4), (3, 0)):
            self.bt[1] = bt_row(401_000_000, flags=flags, flags1=flags1)
            with self.subTest(flags=flags, flags1=flags1), self.assertRaises(RuntimeError): self.proof()

    def test_initialization_led_cleanup_and_duplicate_reports_cannot_satisfy_stop(self):
        original = copy.deepcopy(self.bt)
        for timestamp in (50_000_000, 610_000_000, 705_500_000, 750_000_000):
            self.bt = copy.deepcopy(original); self.bt[1]["monotonicNs"] = timestamp
            with self.subTest(timestamp=timestamp), self.assertRaises(RuntimeError): self.proof()
        self.bt = original + [copy.deepcopy(original[1])]
        with self.assertRaises(RuntimeError): self.proof()

    def test_bad_crc_and_nonzero_reserved_state_byte_are_rejected(self):
        import binascii, struct
        original = bytes.fromhex(self.bt[1]["hex"])
        for recompute in (False, True):
            data = bytearray(original); data[40] = 1
            if recompute: struct.pack_into("<I", data, 74, binascii.crc32(b"\xa2" + data[:74]))
            self.bt[1]["hex"] = data.hex()
            with self.subTest(recompute=recompute), self.assertRaises(RuntimeError): self.proof()

    def test_rgb_delivery_remains_required_in_its_own_later_phase(self):
        phase = phase_tests.PhaseEvidenceTests(); phase.setUp()
        self.observed["phases"] = phase.observed["phases"]
        self.observed["capabilities"]["rgbLed"] = True
        self.observed["marker"]["ledAccepted"] = True
        with self.assertRaises(RuntimeError): self.proof()
        from test_consumer_session import packet, row
        self.usb.append(dict(kind="virtual-output", timestamp=610_000_000,
                            value=zero_value() | dict(lightbarColorAllowed=True, red=18, green=52, blue=86)))
        self.bt.append(row(611_000_000, packet(rgb=(18, 52, 86), flags0=0)))
        self.assertTrue(self.proof()["stopReportForwarded"])

    def test_prior_markers_identity_api_expiry_and_clock_are_independent_gates(self):
        for mutation in (
            lambda: self.bt.pop(0), lambda: self.usb.pop(0),
            lambda: self.observed.update(path="/dev/hidraw8"),
            lambda: self.observed.update(version="3.4.0"),
            lambda: self.observed.update(identityVerified=False),
            lambda: self.observed.update(rumbleDurationMs=500),
            lambda: self.observed["marker"].update(stopAccepted=False),
            lambda: self.core_ready.update(ownedGuestToken="another"),
            lambda: self.core_ready.update(timestampFrequency=10000000)):
            self.setUp(); mutation()
            with self.assertRaises(RuntimeError): self.proof()


if __name__ == "__main__":
    unittest.main()
