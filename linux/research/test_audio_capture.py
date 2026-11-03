import binascii
import ctypes
import ctypes.util
import math
import struct
import unittest

from audio_capture import OpusDecoder, audio_packet, expected_native_haptics, haptic_matches, speaker_reference, waveform_metrics


def report(combined=True):
    data = bytearray(398 if combined else 334)
    data[0] = 0x36 if combined else 0x35
    data[2:5] = b"\x91\x07\xfe"
    route = 142 if combined else 11
    data[route:route + 2] = b"\x93\xc8"
    data[route + 2:route + 202] = bytes(range(200))
    if combined:
        data[11:13] = b"\x90\x3f"
        data[76:78] = b"\x92\x40"
        data[78:142] = bytes(range(64))
    struct.pack_into("<I", data, len(data) - 4, binascii.crc32(b"\xa2" + data[:-4]))
    return data


class CaptureTests(unittest.TestCase):
    def test_layout_extracts_exact_payload_and_rejects_corrupted_capture(self):
        for combined in (False, True):
            data = report(combined)
            packet = audio_packet(data)
            self.assertEqual(packet["opus"], bytes(range(200)))
            self.assertEqual(packet["haptics"], bytes(range(64)) if combined else None)
            for broken in (data[:-1], data + b"\0"):
                with self.assertRaises(ValueError):
                    audio_packet(broken)
            data[20] ^= 1
            with self.assertRaises(ValueError):
                audio_packet(data)

    def test_crc_alone_cannot_validate_wrong_route_header(self):
        data = report()
        data[142] = 0x91
        struct.pack_into("<I", data, len(data) - 4, binascii.crc32(b"\xa2" + data[:-4]))
        with self.assertRaises(ValueError):
            audio_packet(data)

    def test_reference_averages_continuously_with_ties_even_and_saturation(self):
        values = ((128, -128), (384, -384), (32767, -32768))
        pcm = b"".join(struct.pack("<hh", *frame) * 16 for frame in values)
        self.assertEqual(expected_native_haptics(pcm), bytes([0, 0, 2, 254, 127, 128]))
        impulse = struct.pack("<hh", 4096, -4096) + bytes(15 * 4)
        self.assertEqual(expected_native_haptics(impulse), bytes([1, 255]))

    def test_repeated_reordered_and_ambiguous_blocks_never_prove_integrity(self):
        blocks = [bytes((i * 67 + j) % 256 for j in range(64)) for i in range(3)]
        reference = b"".join(blocks)
        self.assertTrue(haptic_matches(blocks, reference)["exactUniqueMonotonic"])
        for observed in ((blocks[0], blocks[0]), (blocks[1], blocks[0])):
            result = haptic_matches(observed, reference)
            self.assertFalse(result["exactUniqueMonotonic"])
            self.assertEqual(result["repeatedOrReorderedBlocks"], 1)
        self.assertEqual(haptic_matches([blocks[0]], reference * 2)["ambiguousBlocks"], 1)
        self.assertFalse(haptic_matches([bytes(64)], reference)["exactUniqueMonotonic"])
        gap = haptic_matches([blocks[0], blocks[2]], reference)
        self.assertEqual(gap["skippedReferenceFrames"], 32)
        self.assertEqual(haptic_matches([bytes([255]) * 64], reference)["unmatchedNonzeroBlocks"], 1)

    def test_rational_reference_preserves_endpoint_sign_and_channels(self):
        source = b"".join(struct.pack("<hh", i * 31 - 9000, 16000 - i * 47) for i in range(512))
        result = list(struct.iter_unpack("<hh", speaker_reference(source)))
        self.assertEqual(len(result), 480)
        self.assertEqual(result[0], (-9000, 16000))
        self.assertEqual(result[-1], (511 * 31 - 9000, 16000 - 511 * 47))
        with self.assertRaises(ValueError):
            speaker_reference(source[:-4])

    def test_waveform_validator_rejects_swapped_channels_and_local_replay(self):
        frames = [(int(10000 * math.sin(i * .071 + i * i * .000011)),
                   int(13000 * math.sin(i * .133 - i * i * .000005))) for i in range(9600)]
        pcm = b"".join(struct.pack("<hh", *frame) for frame in frames)
        delayed = bytes(32 * 4) + pcm[:-32 * 4]
        good = waveform_metrics(pcm, delayed, max_lag=64)
        self.assertTrue(good["passed"])
        self.assertEqual(good["codecAlignmentFrames"], 32)
        swapped = b"".join(struct.pack("<hh", right, left) for left, right in frames)
        self.assertFalse(waveform_metrics(pcm, swapped, max_lag=64)["passed"])
        replay = bytearray(pcm)
        replay[480 * 8 * 4:480 * 9 * 4] = pcm[480 * 3 * 4:480 * 4 * 4]
        self.assertFalse(waveform_metrics(pcm, replay, max_lag=64)["passed"])
        with self.assertRaises(ValueError):
            waveform_metrics(pcm, pcm[:-1920], max_lag=64)

    @unittest.skipUnless(ctypes.util.find_library("opus"), "independent native libopus unavailable")
    def test_native_decoder_accepts_real_10ms_cbr_and_preserves_distinct_channels(self):
        decoder = OpusDecoder()
        lib = decoder.lib
        # Constants from xiph/opus v1.5.2 include/opus_defines.h.
        lib.opus_encoder_create.argtypes = [ctypes.c_int32, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
        lib.opus_encoder_create.restype = ctypes.c_void_p
        lib.opus_encoder_ctl.argtypes = [ctypes.c_void_p, ctypes.c_int]
        lib.opus_encoder_ctl.restype = ctypes.c_int
        lib.opus_encode.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int16), ctypes.c_int,
                                   ctypes.POINTER(ctypes.c_ubyte), ctypes.c_int32]
        lib.opus_encode.restype = ctypes.c_int
        lib.opus_encoder_destroy.argtypes = [ctypes.c_void_p]
        lib.opus_encoder_destroy.restype = None
        error = ctypes.c_int()
        encoder = lib.opus_encoder_create(48000, 2, 2049, ctypes.byref(error))
        self.assertTrue(encoder)
        try:
            self.assertEqual(error.value, 0)
            self.assertEqual(lib.opus_encoder_ctl(encoder, 4002, ctypes.c_int(160000)), 0)
            self.assertEqual(lib.opus_encoder_ctl(encoder, 4006, ctypes.c_int(0)), 0)
            decoded = bytearray()
            for frame in range(12):
                samples = []
                for i in range(480):
                    t = (frame * 480 + i) / 48000
                    samples.extend((int(8000 * math.sin(2 * math.pi * 440 * t)),
                                    int(12000 * math.sin(2 * math.pi * 997 * t))))
                source = (ctypes.c_int16 * 960)(*samples)
                packet = (ctypes.c_ubyte * 200)()
                self.assertEqual(lib.opus_encode(encoder, source, 480, packet, 200), 200)
                decoded.extend(decoder.decode(bytes(packet)))
            stereo = list(struct.iter_unpack("<hh", decoded))
            self.assertGreater(sum(left * left for left, _ in stereo), 1_000_000)
            self.assertGreater(sum(right * right for _, right in stereo), 1_000_000)
            self.assertNotEqual([left for left, _ in stereo], [right for _, right in stereo])
        finally:
            lib.opus_encoder_destroy(encoder)
            decoder.close()


if __name__ == "__main__":
    unittest.main()
