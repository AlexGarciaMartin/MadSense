#!/usr/bin/env python3
import plistlib
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from verify_deployment import inspect_macho, verify


def thin(arch="arm64", minimum=0x000F0000, legacy=False, platform=1):
    cpu, subtype = (0x0100000C, 0) if arch == "arm64" else (0x01000007, 3)
    command = (struct.pack("<IIII", 0x24, 16, minimum, 0x001A0000) if legacy else
               struct.pack("<IIIIII", 0x32, 24, platform, minimum, 0x001A0000, 0))
    return struct.pack("<IIIIIIII", 0xFEEDFACF, cpu, subtype, 6, 1, len(command), 0, 0) + command


def universal(arm, intel, wide=False):
    entry_size = 32 if wide else 20
    offset = 8 + 2 * entry_size
    entry_format = ">IIQQII" if wide else ">IIIII"
    tail = (0, 0) if wide else (0,)
    return (struct.pack(">II", 0xCAFEBABF if wide else 0xCAFEBABE, 2) +
            struct.pack(entry_format, 0x0100000C, 0, offset, len(arm), *tail) +
            struct.pack(entry_format, 0x01000007, 3, offset + len(arm), len(intel), *tail) + arm + intel)


class DeploymentTests(unittest.TestCase):
    def test_native_minimum_is_separate_from_build_sdk(self):
        metadata = inspect_macho(thin(), "arm64")
        self.assertEqual(metadata["minimum_os"], "15.0.0")
        self.assertEqual(metadata["sdk"], "26.0.0")

    def test_legacy_minimum_is_supported(self):
        metadata = inspect_macho(thin("x86_64", 0x000E0000, legacy=True), "x86_64")
        self.assertEqual(metadata["minimum_os"], "14.0.0")
        self.assertEqual(metadata["load_command"], "LC_VERSION_MIN_MACOSX")

    def test_universal_uses_only_the_requested_slice(self):
        for wide in (False, True):
            binary = universal(thin(), thin("x86_64", 0x001A0000), wide)
            self.assertEqual(inspect_macho(binary, "arm64")["minimum_os"], "15.0.0")
            self.assertEqual(inspect_macho(binary, "x86_64")["minimum_os"], "26.0.0")

    def test_wrong_architecture_and_platform_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "architecture"):
            inspect_macho(thin("x86_64"), "arm64")
        with self.assertRaisesRegex(ValueError, "non-macOS"):
            inspect_macho(thin(platform=2), "arm64")

    def test_truncated_and_missing_load_commands_are_rejected(self):
        with self.assertRaises(ValueError):
            inspect_macho(thin()[:-1], "arm64")
        missing = struct.pack("<IIIIIIII", 0xFEEDFACF, 0x0100000C, 0, 6, 0, 0, 0, 0)
        with self.assertRaisesRegex(ValueError, "minimum-version"):
            inspect_macho(missing, "arm64")
        self.assertIsNone(inspect_macho(b"MZ managed assembly resource", "arm64"))

    def test_packaging_rejects_newer_nested_dependency_and_plist_mismatch(self):
        with tempfile.TemporaryDirectory() as temporary:
            app = Path(temporary) / "MadSense.app"
            backend = app / "Contents" / "Resources" / "backend"
            backend.mkdir(parents=True)
            plist = app / "Contents" / "Info.plist"
            plist.write_bytes(plistlib.dumps({"LSMinimumSystemVersion": "15.0"}))
            (backend / "libhidapi.dylib").write_bytes(thin(minimum=0x000F0100))
            (backend / "MadSense.Backend.dll").write_bytes(b"MZnot-native")
            result = verify([app], "arm64", "15.0")
            self.assertFalse(result["compatible"])
            self.assertEqual(len(result["binaries"]), 1)
            self.assertIn("above declared", result["errors"][0])
            (backend / "libhidapi.dylib").write_bytes(thin())
            self.assertTrue(verify([app], "arm64", "15.0")["compatible"])
            plist.write_bytes(plistlib.dumps({"LSMinimumSystemVersion": "14.0"}))
            self.assertFalse(verify([app], "arm64", "15.0")["compatible"])

    def test_empty_package_is_not_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            self.assertFalse(verify([temporary], "arm64", "15.0")["compatible"])


if __name__ == "__main__":
    unittest.main()
