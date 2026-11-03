import binascii
import struct
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from protocol_observer import audio_route_matches, open_virtual_hidraw, trigger_matches, trigger_request


def frame(state, combined=False, headset=False):
    data = bytearray(398 if combined else 78)
    data[0] = 0x36 if combined else 0x31
    if combined:
        data[11:13] = b"\x90\x3f"
        data[13:60] = state
        data[142:144] = bytes([0x96 if headset else 0x93, 200])
    else:
        data[3:50] = state
    struct.pack_into("<I", data, len(data) - 4, binascii.crc32(b"\xa2" + data[:-4]))
    return data


class ProtocolObserverTests(unittest.TestCase):
    def test_observer_rejects_non_sysfs_vhci_lookalike_before_any_device_open(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary) / "vhci_hcd.0/usb1/1-1"
            parent.mkdir(parents=True)
            (parent / "idVendor").write_text("054c")
            (parent / "idProduct").write_text("0ce6")
            with patch("protocol_observer.os.open") as opened, self.assertRaisesRegex(AssertionError, "exact imported USB parent"):
                open_virtual_hidraw(parent)
            opened.assert_not_called()

    def test_trigger_requires_both_exact_distinct_effects_flags_and_crc(self):
        request = trigger_request()
        state = bytearray(request[1:])
        for combined in (False, True):
            self.assertTrue(trigger_matches(frame(state, combined), request))
            wrong = state.copy(); wrong[10:21], wrong[21:32] = state[21:32], state[10:21]
            self.assertFalse(trigger_matches(frame(wrong, combined), request))
            wrong = state.copy(); wrong[0] = 4
            self.assertFalse(trigger_matches(frame(wrong, combined), request))
            wrong = frame(state, combined); wrong[-1] ^= 1
            self.assertFalse(trigger_matches(wrong, request))
            self.assertFalse(trigger_matches(frame(state, combined), trigger_request(True)))

    def test_explicit_off_marker_requires_both_effects_to_be_off(self):
        request = trigger_request(True)
        self.assertTrue(trigger_matches(frame(request[1:]), request))
        state = bytearray(request[1:]); state[21] = 0x21
        self.assertFalse(trigger_matches(frame(state), request))

    def test_route_requires_matching_tag_control_volume_gain_and_crc(self):
        for headset in (False, True):
            state = bytearray(47)
            state[0], state[4], state[5], state[7], state[37] = 0xb0, 100 if headset else 0x3f, 100, 0 if headset else 0x30, 2
            state[1] = 0x80
            data = frame(state, True, headset)
            self.assertTrue(audio_route_matches(data, headset))
            self.assertFalse(audio_route_matches(data, not headset))
            wrong = state.copy(); wrong[1] = 0
            self.assertFalse(audio_route_matches(frame(wrong, True, headset), headset))
            for position in (0, 4, 5, 7, 37):
                wrong = state.copy(); wrong[position] ^= 0x10 if position in (0, 7) else 1
                self.assertFalse(audio_route_matches(frame(wrong, True, headset), headset))
            data[-1] ^= 1
            self.assertFalse(audio_route_matches(data, headset))


if __name__ == "__main__":
    unittest.main()
