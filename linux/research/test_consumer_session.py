"""Portable consumer-scope and evidence regressions; never opens devices."""
import binascii
import hashlib
from pathlib import Path
import struct
import tempfile
import unittest

from consumer_session import base_acl, bounded_bytes, bt_state, expected_motors, output_window
from qemu_consumers import verify_build, verify_consumer_result
from run_wine_consumer import matching_container, parse_pe_output, select_audio_endpoint, xauthority


def packet(left=0, right=0, rgb=None, flags0=3, flags2=0):
    data = bytearray(78)
    data[0] = 0x31
    data[3], data[41] = flags0, flags2
    data[5], data[6] = right, left
    if rgb is not None:
        data[4] = 4
        data[47:50] = bytes(rgb)
    struct.pack_into("<I", data, 74, binascii.crc32(b"\xa2" + data[:74]))
    return bytes(data)


def row(timestamp, data):
    return dict(kind="output", monotonicNs=timestamp, hex=data.hex())


class ConsumerEvidenceTests(unittest.TestCase):
    def test_complete_process_log_is_still_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "stdout")
            path.write_bytes(b"12345")
            self.assertEqual(bounded_bytes(path, 5), b"12345")
            with self.assertRaises(RuntimeError):
                bounded_bytes(path, 4)

    def test_only_base_acls_are_eligible_for_a_new_named_grant(self):
        self.assertEqual(base_acl("user::rw-\ngroup::r--\nother::---\n\n"),
                         "user::rw-\ngroup::r--\nother::---\n")
        for acl in ("user::rw-\nuser:123:rw-\ngroup::r--\nmask::r--\nother::---\n",
                    "user::rw-\ngroup::rw-\nmask::rw-\nother::---\n",
                    "default:user::rw-\ngroup::rw-\nother::---\n",
                    "user::rw-\ngroup::rw-\n", "user::rw-\nuser::rw-\ngroup::rw-\nother::---\n"):
            with self.subTest(acl=acl), self.assertRaises(RuntimeError):
                base_acl(acl)

    def test_delivery_requires_marker_then_zero_in_this_consumer_window(self):
        rows = [row(1, packet(18, 86, (18, 52, 86))), row(2, packet()),
                row(11, packet(18, 86, (18, 52, 86))), row(12, packet())]
        evidence = output_window(rows, 10, 20)
        self.assertEqual(evidence["rumbleMarkerMonotonicNs"], 11)
        self.assertEqual(evidence["zeroStateMonotonicNs"], 12)
        for sample in (rows[:2], rows[2:3], [rows[3], rows[2]], rows[2:] + [row(13, packet(18, 86))]):
            with self.subTest(sample=sample), self.assertRaises(RuntimeError):
                output_window(sample, 10, 20)

    def test_crc_corruption_cannot_be_accepted_as_delivery(self):
        damaged = bytearray(packet(18, 86, (18, 52, 86))); damaged[6] ^= 1
        with self.assertRaises(RuntimeError):
            bt_state(damaged)
        with self.assertRaises(RuntimeError):
            output_window([row(11, damaged), row(12, packet())], 10, 20)

    def test_motor_bytes_without_rumble_selectors_do_not_change_motor_state(self):
        marker = row(11, packet(18, 86, (18, 52, 86)))
        for flags0, flags2 in ((0, 0), (1, 0), (2, 0), (0, 4)):
            with self.subTest(flags0=flags0, flags2=flags2):
                # RGB-only packets often contain zero motor bytes, but cannot
                # stop a previously selected motor state.
                invalid = row(12, packet(rgb=(18, 52, 86), flags0=flags0, flags2=flags2))
                with self.assertRaises(RuntimeError):
                    output_window([marker, invalid], 10, 20)
                invalid_marker = row(11, packet(18, 86, (18, 52, 86), flags0, flags2))
                with self.assertRaises(RuntimeError):
                    output_window([invalid_marker, row(12, packet())], 10, 20)
        for flags0, flags2 in ((3, 0), (2, 4)):
            with self.subTest(valid=(flags0, flags2)):
                rows = [row(11, packet(18, 86, (18, 52, 86), flags0, flags2)),
                        row(12, packet(flags0=flags0, flags2=flags2))]
                self.assertEqual(output_window(rows, 10, 20)["zeroStateMonotonicNs"], 12)

    def test_expected_sdl_scaling_depends_on_firmware_and_selected_backend(self):
        firmware = bytearray(64); firmware[44:46] = (0x0223).to_bytes(2, "little")
        ready = dict(hidrawPath="/dev/hidraw4", features={"20": firmware.hex()})
        self.assertEqual(expected_motors(ready, dict(path="/dev/hidraw4", sdlMajor=3)), (9, 43))
        self.assertEqual(expected_motors(ready, dict(path="/dev/input/event9", sdlMajor=3)), (18, 86))
        firmware[44:46] = (0x0224).to_bytes(2, "little"); ready["features"]["20"] = firmware.hex()
        self.assertEqual(expected_motors(ready, dict(path="/dev/hidraw4", sdlMajor=2)), (18, 86))
        firmware[44:46] = bytes(2); ready["features"]["20"] = firmware.hex()
        self.assertEqual(expected_motors(ready, dict(path="/dev/hidraw4", sdlMajor=2)), (9, 43))
        self.assertEqual(expected_motors(ready, dict(path="/dev/hidraw4", sdlMajor=3)), (18, 86))


class RuntimeArtifactTests(unittest.TestCase):
    def test_execution_requires_successful_same_commit_untampered_binaries(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / "bin").mkdir()
            (root / "bin/consumer").write_bytes(b"test executable")
            digest = hashlib.sha256(b"test executable").hexdigest()
            (root / "binary-sha256.txt").write_text(digest + "  /build/bin/consumer\n")
            (root / "provenance.txt").write_text("MadSense commit: " + "a" * 40 + "\nCompilation: passed\n")
            self.assertEqual(verify_build(root, ("consumer",), "a" * 40), {"consumer": digest})
            with self.assertRaises(RuntimeError):
                verify_build(root, ("consumer",), "b" * 40)
            with self.assertRaises(RuntimeError):
                verify_build(root, ("consumer", "missing"), "a" * 40)
            (root / "bin/consumer").write_bytes(b"modified")
            with self.assertRaises(RuntimeError):
                verify_build(root, ("consumer",), "a" * 40)

    def test_partial_or_cleanup_failed_runtime_never_passes(self):
        result = dict(result="passed", accountProcessesAndFilesRemoved=True, wineRequested=True,
                      physicalDevices=False, protonValidated=False,
                      cases=[dict(name=f"consumer_sdl{major}-{state}", passed=True)
                             for major in (2, 3) for state in ("south", "neutral")],
                      wineSession=dict(kind="distribution-wine", defaultWinebus=True, protonValidated=False,
                                       cases=[dict(name=name, passed=True) for name in
                                              ("wine-sony-south", "wine-sony-neutral", "wine-wasapi")]))
        verify_consumer_result(result)
        for change in (dict(accountProcessesAndFilesRemoved=False), dict(cleanupErrors=["ACL changed"]),
                       dict(cases=result["cases"][:-1]), dict(wineRequested=False), dict(protonValidated=True)):
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                verify_consumer_result(result | change)


class WineEvidenceTests(unittest.TestCase):
    container = "a4cf5483-1295-4f79-9912-142c91a0c064"

    def test_audio_selection_requires_exact_unique_hid_container(self):
        rows = [dict(kind="hid-candidate", macMatched=True, containerKnown=True,
                     containerId="{" + self.container.upper() + "}")]
        self.assertEqual(matching_container(rows), self.container)
        endpoints = [dict(kind="audio-endpoint", id="owned", containerKnown=True, containerId=self.container),
                     dict(kind="audio-endpoint", id="unknown", containerKnown=False,
                          containerId="{00000000-0000-0000-0000-000000000000}")]
        self.assertEqual(select_audio_endpoint(endpoints, self.container), "owned")
        for values in (endpoints[1:], endpoints + endpoints[:1]):
            with self.subTest(values=values), self.assertRaises(RuntimeError):
                select_audio_endpoint(values, self.container)

    def test_missing_mac_or_zero_container_never_becomes_audio_identity(self):
        for values in ([], [dict(kind="hid-candidate", macMatched=False, containerKnown=True, containerId=self.container)],
                       [dict(kind="hid-candidate", macMatched=True, containerKnown=True,
                             containerId="{00000000-0000-0000-0000-000000000000}")]):
            with self.subTest(values=values), self.assertRaises(RuntimeError):
                matching_container(values)

    def test_pe_output_needs_final_result(self):
        self.assertEqual(parse_pe_output(b'{"kind":"result","result":"failed"}\n')[-1]["result"], "failed")
        with self.assertRaises(RuntimeError):
            parse_pe_output(b'{"kind":"audio-endpoint","id":"arbitrary"}\n')

    def test_private_xauthority_has_one_exact_cookie_and_display(self):
        cookie = bytes(range(16)); data = xauthority(cookie, 37)
        self.assertEqual(struct.unpack_from(">H", data)[0], 65535)
        offset, fields = 2, []
        for _ in range(4):
            count = struct.unpack_from(">H", data, offset)[0]; offset += 2
            fields.append(data[offset:offset + count]); offset += count
        self.assertEqual(fields, [b"", b"37", b"MIT-MAGIC-COOKIE-1", cookie])
        self.assertEqual(offset, len(data))


if __name__ == "__main__":
    unittest.main()
