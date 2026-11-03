import copy
import hashlib
import io
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zlib

from hidapi_windows_evidence import COMMIT, verify
from hidapi_windows_source import RefuseRedirect, verified_extract


class HidapiFunctionalEvidenceTests(unittest.TestCase):
    def setUp(self):
        feature = {"09": "09" + "06050403d502" + "00" * 13, "20": "20" + "00" * 63, "05": "05" + "00" * 40}
        # Real consumer-ready.json has no clock field; core/ready.json owns it.
        self.ready = dict(features=feature, virtualMacHex="02d503040506", rawOutputObservationEnabled=True,
            ownedGuestToken="ed913ce6-e0b4-4a47-873d-1c06b93e465d", actorPid=11089,
            hidrawPath="/dev/hidraw1", vendorId=0x054c, productId=0x0ce6)
        self.core_ready = dict(ownedGuestToken=self.ready["ownedGuestToken"], pid=11089,
            virtualDevicePath="/dev/hidraw1", vendorId=0x054c, productId=0x0ce6,
            timestampFrequency=1_000_000_000, rawOutputObservationEnabled=True)
        data = bytearray(64); data[:7] = bytes([1, 128, 128, 128, 128, 0, 0]); data[8] = 0x28
        output = bytearray(48); output[0] = 2; output[2] = 4; output[45:48] = bytes.fromhex("123456")
        rows = [dict(kind="runtime", scope="real-hidapi-windows-consumer", hidapiVersion="0.15.0", hidapiCommit=COMMIT,
                     wineVersion="9.0", physicalDevices=False, protonValidated=False),
                dict(kind="hidapi-candidate", macMatched=True),
                *[dict(kind="hidapi-feature", reportId=int(key, 16), hex=value, bufferLength=64,
                       returnValue=len(bytes.fromhex(value)), meaningfulLength=len(bytes.fromhex(value))) for key, value in feature.items()],
                dict(kind="hidapi-input", state="south", returnValue=64, hex=data.hex()),
                dict(kind="hidapi-write", requested=48, returnValue=47, libraryReportedError=False,
                     fullRequestedCount=False, outputDeliveryVerified=False, nativeWindowsParityVerified=False, hex=output.hex()),
                dict(kind="result", result="observed")]
        self.command = dict(exitCode=0, startedMonotonicNs=100, endedMonotonicNs=200, fixtureState="south", evidence=rows)
        self.usb = [dict(kind="virtual-output-raw", timestamp=140, value=dict(length=48, hex=output.hex()))]
        bt = bytearray(78); bt[0] = 0x31; bt[2] = 0x10; bt[3:50] = output[1:]
        bt[-4:] = zlib.crc32(bytes([0xa2]) + bt[:-4]).to_bytes(4, "little")
        self.bt = [dict(kind="output", monotonicNs=150, hex=bt.hex())]

    def call(self): return verify(self.ready, self.command, self.usb, self.bt, self.core_ready)

    def test_core_clock_is_joined_to_actual_actor_and_exact_virtual_node(self):
        self.assertNotIn("timestampFrequency", self.ready)
        self.assertTrue(self.call()["outputReportForwarded"])
        for key, value in (("ownedGuestToken", "other"), ("pid", 11090), ("virtualDevicePath", "/dev/hidraw0"),
                           ("timestampFrequency", 10_000_000), ("timestampFrequency", True),
                           ("rawOutputObservationEnabled", False)):
            with self.subTest(key=key), patch.dict(self.core_ready, {key:value}), self.assertRaises(RuntimeError):
                self.call()

    def test_47_is_library_nonerror_forwarding_but_never_count_parity(self):
        result = self.call()
        self.assertTrue(result["outputReportForwarded"])
        self.assertFalse(result["fullRequestedCount"])
        self.assertFalse(result["nativeWindowsParityVerified"])
        self.assertFalse(result["physicalActuationVerified"])

    def test_48_remains_separate_full_requested_count(self):
        self.command["evidence"][-2].update(returnValue=48, fullRequestedCount=True)
        self.assertTrue(self.call()["fullRequestedCount"])

    def test_count_alone_never_proves_delivery(self):
        for rows in ([], self.bt + self.bt):
            with self.subTest(rows=len(rows)), patch.object(self, "bt", rows), self.assertRaises(RuntimeError): self.call()
        self.usb = []
        with self.assertRaises(RuntimeError): self.call()

    def test_zero_short_large_error_and_bool_return_are_rejected(self):
        for count in (-1, 0, 46, 49, True):
            self.command["evidence"][-2]["returnValue"] = count
            with self.subTest(count=count), self.assertRaises(RuntimeError): self.call()

    def test_corrupted_crc_wrong_feature_wrong_input_or_late_report_fail(self):
        original = copy.deepcopy((self.command, self.bt))
        for change in ("crc", "feature", "input", "late"):
            self.command, self.bt = copy.deepcopy(original)
            if change == "crc": self.bt[0]["hex"] = self.bt[0]["hex"][:-2] + "aa"
            if change == "feature": self.command["evidence"][2]["hex"] = "09ff"
            if change == "input": self.command["evidence"][-3]["state"] = "neutral"
            if change == "late": self.bt[0]["monotonicNs"] = 201
            with self.subTest(change=change), self.assertRaises(RuntimeError): self.call()


class SourceBoundaryTests(unittest.TestCase):
    def test_redirect_is_rejected_before_another_request(self):
        with self.assertRaises(ValueError):
            RefuseRedirect().redirect_request(None, None, 302, "", {}, "https://untrusted.invalid/source")

    def test_digest_failure_prevents_directory_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); archive = root / "archive"; archive.write_bytes(b"changed")
            with self.assertRaises(ValueError): verified_extract(archive, root / "out")
            self.assertFalse((root / "out").exists())

    def test_even_digest_matched_links_and_traversal_are_rejected(self):
        for name, link in (("hidapi-owned/../../escape", False), ("hidapi-owned/link", True)):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory); archive = root / "archive.tar"
                with tarfile.open(archive, "w") as out:
                    item = tarfile.TarInfo(name); item.size = 0
                    if link: item.type = tarfile.SYMTYPE; item.linkname = "/outside"
                    out.addfile(item, io.BytesIO(b""))
                data = archive.read_bytes()
                with patch("hidapi_windows_source.SIZE", len(data)), \
                     patch("hidapi_windows_source.SHA256", hashlib.sha256(data).hexdigest()), \
                     patch("hidapi_windows_source.ROOT", "hidapi-owned"), self.assertRaises(ValueError):
                    verified_extract(archive, root / "out")
                self.assertFalse((root / "out").exists())


if __name__ == "__main__":
    unittest.main()
