import binascii
import struct
import unittest

from production_fixture import input_report, manifest


class ProtocolFixtureTests(unittest.TestCase):
    def test_headset_and_each_edge_marker_change_only_their_input_field_and_crc(self):
        neutral = input_report(sequence=123)
        for keyword, value, offset in (("headset", True, 55), ("edge_buttons", 0x10, 11),
                                       ("edge_buttons", 0x20, 11), ("edge_buttons", 0x40, 11),
                                       ("edge_buttons", 0x80, 11)):
            changed = input_report(sequence=123, **{keyword: value})
            self.assertEqual(changed[offset], int(value))
            self.assertEqual([index for index in range(74) if neutral[index] != changed[index]], [offset])
            self.assertEqual(struct.unpack_from("<I", changed, 74)[0], binascii.crc32(b"\xa1" + changed[:74]))
        with self.assertRaises(ValueError):
            input_report(edge_buttons=1)

    def test_usb_fixture_images_preserve_connection_and_each_edge_button(self):
        vectors = manifest()["input"]
        self.assertEqual(bytes.fromhex(vectors["headset"]["usb"])[54], 1)
        for name, mask in (("edge_fn_left", 0x10), ("edge_fn_right", 0x20),
                           ("edge_paddle_left", 0x40), ("edge_paddle_right", 0x80)):
            self.assertEqual(bytes.fromhex(vectors[name]["usb"])[10], mask)


if __name__ == "__main__":
    unittest.main()
