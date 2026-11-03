import binascii
import copy
import struct
import unittest

from consumer_phases import correlate, phase_windows


def bt_row(timestamp, *, flags=3, flags1=0, left=0, right=0):
    data = bytearray(78)
    data[0] = 0x31
    data[3], data[4], data[5], data[6] = flags, flags1, right, left
    struct.pack_into("<I", data, 74, binascii.crc32(b"\xa2" + data[:74]))
    return dict(kind="output", monotonicNs=timestamp, hex=data.hex())


def usb_row(timestamp, *, selected=True, left=0, right=0):
    return dict(kind="virtual-output", timestamp=timestamp, value=dict(
        rumbleEmulationEnabled=selected, useRumbleNotHaptics=selected,
        improvedRumbleEmulationEnabled=False, lightbarColorAllowed=False,
        rumbleLeft=left, rumbleRight=right, red=0, green=0, blue=0))


class PhaseEvidenceTests(unittest.TestCase):
    def setUp(self):
        names = ["rumble-call-before", "rumble-call-after", "stop-call-before", "stop-call-after",
                 "stop-observation-end", "led-call-before", "led-call-after", "output-observation-end",
                 "close-before", "close-after", "quit-before", "quit-after"]
        ms = [100, 101, 301, 302, 602, 603, 604, 704, 705, 706, 707, 708]
        self.observed = dict(phaseClock="CLOCK_MONOTONIC", phaseTimingValid=True,
            capabilities=dict(rgbLed=True), rumbleDurationMs=60000, explicitStopBeforeAutoExpiry=True,
            marker=dict(stopAccepted=True), phases=[dict(name=name, monotonicNs=t*1_000_000) for name,t in zip(names,ms)])
        self.command = dict(label="consumer_sdl3-neutral", startedMonotonicNs=1, endedMonotonicNs=800_000_000)

    def run_case(self, usb=(), bt=()):
        return correlate(self.observed, self.command, usb, bt, 1_000_000_000)

    def test_separate_stop_interval_excludes_led_and_close_packets(self):
        result = self.run_case(bt=[bt_row(610_000_000), bt_row(705_500_000)])
        self.assertFalse(result["explicitStop"]["selectedZeroObservedBt"])
        self.assertEqual(result["phases"]["led"]["btSelectedZeroCount"], 1)
        self.assertEqual(result["phases"]["close"]["btSelectedZeroCount"], 1)

    def test_rgb_only_zero_is_observation_not_motor_stop(self):
        result = self.run_case(bt=[bt_row(400_000_000, flags=0, flags1=4)])
        stop = result["explicitStop"]
        self.assertTrue(stop["selectorClearZeroFieldsObservedBt"])
        self.assertFalse(stop["selectedZeroObservedBt"])
        self.assertFalse(stop["zeroFieldsAloneProveMotorStop"])
        self.assertTrue(stop["independentStopAssertionUnchanged"])

    def test_selected_zero_in_stop_window_is_preserved_on_both_sides(self):
        result = self.run_case(usb=[usb_row(400_000_000)], bt=[bt_row(401_000_000)])
        self.assertTrue(result["explicitStop"]["selectedZeroObservedUsb"])
        self.assertTrue(result["explicitStop"]["selectedZeroObservedBt"])
        self.assertFalse(result["completeUsbBytesCaptured"])
        self.assertFalse(result["causalOneToOneMappingVerified"])
        self.assertFalse(result["latencyValidated"])

    def test_nonzero_marker_retains_expected_quantization(self):
        result = self.run_case(usb=[usb_row(150_000_000, left=0x12, right=0x56)],
                              bt=[bt_row(151_000_000, left=9, right=0x2b)])
        self.assertEqual(result["phases"]["rumble"]["usbSelectedMarkerCount"], 1)
        self.assertEqual(result["phases"]["rumble"]["btSelectedMarkerCount"], 0)

    def test_bad_bluetooth_crc_cannot_be_classified_as_valid_delivery(self):
        row = bt_row(400_000_000)
        row["hex"] = row["hex"][:-2] + ("ff" if row["hex"][-2:] != "ff" else "00")
        with self.assertRaises(RuntimeError):
            self.run_case(bt=[row])

    def test_missing_decoded_selector_is_unknown_not_clear(self):
        row = usb_row(400_000_000)
        del row["value"]["rumbleEmulationEnabled"]
        with self.assertRaises(RuntimeError):
            self.run_case(usb=[row])

    def test_false_expiry_claim_and_short_settle_are_rejected(self):
        self.observed["rumbleDurationMs"] = 500
        with self.assertRaises(RuntimeError):
            phase_windows(self.observed, self.command)
        self.observed["rumbleDurationMs"] = 60000
        self.observed["phases"][4]["monotonicNs"] -= 1
        with self.assertRaises(RuntimeError):
            phase_windows(self.observed, self.command)

    def test_duplicate_out_of_window_or_wrong_clock_are_rejected(self):
        original = copy.deepcopy(self.observed)
        for change in (lambda: self.observed["phases"][1].update(name="rumble-call-before"),
                       lambda: self.observed["phases"][0].update(monotonicNs=0),
                       lambda: self.observed.update(phaseClock="CLOCK_REALTIME")):
            self.observed = copy.deepcopy(original); change()
            with self.assertRaises(RuntimeError):
                self.run_case()
        self.observed = original
        with self.assertRaises(RuntimeError):
            correlate(self.observed, self.command, [], [], 10_000_000)


if __name__ == "__main__":
    unittest.main()
