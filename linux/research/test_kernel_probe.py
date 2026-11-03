"""Portable validation of kernel-proof evidence interpretation; no device access."""
import unittest
from kernel_probe import contains_exact


class PcmEvidenceTests(unittest.TestCase):
    def test_accepts_exact_stream_with_only_frame_aligned_silence_around_it(self):
        data = bytes(range(1, 33))
        evidence = contains_exact(bytes(8) + data + bytes(16), data, 8, True)
        self.assertEqual(evidence["offsetFrames"], 1)
        self.assertEqual(evidence["expectedFrames"], 4)

    def test_rejects_drops_duplicates_padding_corruption_and_unexpected_signal(self):
        expected = bytes(range(1, 33))
        for captured in (expected[:8] + expected[16:], expected[:8] * 2 + expected[8:],
                         expected[:8] + bytes(8) + expected[8:], expected[::-1],
                         bytes([7]) * 8 + expected, b"\0" + expected + bytes(7)):
            with self.subTest(captured=captured), self.assertRaises(AssertionError):
                contains_exact(captured, expected, 8, True)

    def test_synthetic_capture_may_be_contiguous_subset_of_emitted_stream(self):
        source = bytes(range(1, 65))
        evidence = contains_exact(source, source[8:40], 4, False)
        self.assertEqual(evidence["offsetFrames"], 2)
        with self.assertRaises(AssertionError):
            contains_exact(source, source[8:20] + bytes(4) + source[20:40], 4, False)


if __name__ == "__main__":
    unittest.main()
