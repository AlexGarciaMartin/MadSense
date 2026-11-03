"""Regression for USED status preceding the real USB sysfs alias in QEMU."""
from pathlib import Path
import struct
import unittest
from unittest.mock import MagicMock, patch

import test_broker_core as fixtures


class ImportSysfsTests(unittest.TestCase):
    def prepare(self, endpoint, status, expected):
        metadata = struct.pack("!IIIHHHBBBBBB", 1, 1, 3, 0x045E, 0x028E, 0, 0, 0, 0, 1, 1, 1)
        endpoint.recv.side_effect = [struct.pack("!HHI", 0x0111, 3, 0), bytes(256) + b"1-1".ljust(32, b"\0") + metadata]
        expected.__str__.return_value = "/sys/devices/platform/vhci_hcd.0/usb1/1-2"
        status.read_text.return_value = ("header\nhs 0000 006 003 00010001 000012 1-1\n"
                                         "hs 0001 004 000 00000000 000000 0-0\n")

    def test_used_before_alias_waits_then_accepts_exact_second_port(self):
        with fixtures.RecoveryTests().import_fixture() as (lease, endpoint, _, status, expected):
            self.prepare(endpoint, status, expected)
            operation = lease.attach_steps(16384, "1-1")
            self.assertIsNone(next(operation))
            status.read_text.return_value = ("header\nhs 0000 006 003 00010001 000012 1-1\n"
                                             "hs 0001 006 003 00010001 000021 1-2\n")
            aliases = []
            def resolve(path, strict=False):
                aliases.append((str(path), strict))
                if len(aliases) == 1:
                    if strict:
                        raise FileNotFoundError(str(path))
                    return path  # Actual pathlib behavior before alias publication.
                return expected
            with patch.object(Path, "resolve", resolve):
                self.assertIsNone(next(operation), "USED alone must not acknowledge enumeration")
                self.assertIsNone(lease.import_device)
                with self.assertRaises(StopIteration):
                    next(operation)
            self.assertEqual(aliases, [(str(Path("/sys/bus/usb/devices/1-2")), True)] * 2)
            self.assertEqual(lease.saved["importDevice"]["path"], str(expected))
            lease.detach.assert_not_called()

    def test_existing_different_usb_path_is_rejected_and_owned_transport_closed(self):
        with fixtures.RecoveryTests().import_fixture() as (lease, endpoint, _, status, expected):
            self.prepare(endpoint, status, expected)
            operation = lease.attach_steps(16384, "1-1")
            next(operation)
            status.read_text.return_value = "header\nhs 0001 006 003 00010001 000021 1-2\n"
            wrong = MagicMock()
            wrong.__str__.return_value = "/sys/devices/platform/vhci_hcd.0/usb1/1-3"
            with patch.object(Path, "resolve", return_value=wrong), self.assertRaises(fixtures.core.BrokerError) as error:
                next(operation)
            self.assertEqual(error.exception.code, "attach_failed")
            self.assertIn("unexpected USB path", str(error.exception))
            lease.detach.assert_called_once()


if __name__ == "__main__":
    unittest.main()
