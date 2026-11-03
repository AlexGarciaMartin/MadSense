import copy
import hashlib
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import owned_qemu
from qemu_broker import REQUIRED_CASES, verify_result


class GuestLifecycleTests(unittest.TestCase):
    def test_private_work_cannot_be_inside_evidence_or_its_ancestor(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for output, work in ((root / "same", root / "same"), (root / "out", root / "out/private"),
                                 (root / "private/out", root / "private")):
                with self.subTest(output=output, work=work), self.assertRaises(RuntimeError):
                    owned_qemu.fresh_directories(output, work)

    def test_existing_evidence_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "out"; output.mkdir()
            (output / "prior.txt").write_text("preserve")
            with self.assertRaises(RuntimeError):
                owned_qemu.fresh_directories(output, root / "private")
            self.assertEqual((output / "prior.txt").read_text(), "preserve")

    def test_unmarked_host_fails_before_creating_directories(self):
        with patch.object(owned_qemu, "validate_environment", side_effect=RuntimeError("unmarked host")), \
             patch.object(owned_qemu, "fresh_directories") as directories:
            with self.assertRaises(RuntimeError):
                owned_qemu.OwnedQemu("output", "work")
        directories.assert_not_called()

    def test_signed_pin_and_download_must_both_match(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image, manifest = root / "image", root / "SHA256SUMS"
            image.write_bytes(b"fixture image")
            digest = hashlib.sha256(image.read_bytes()).hexdigest()
            manifest.write_text(digest + " *image\n")
            owned_qemu.verify_signed_image(manifest, image, "image", digest)
            image.write_bytes(b"different bytes")
            with self.assertRaises(RuntimeError):
                owned_qemu.verify_signed_image(manifest, image, "image", digest)
            manifest.write_text(digest + " *image\n" + digest + " image\n")
            with self.assertRaises(RuntimeError):
                owned_qemu.verify_signed_image(manifest, image, "image", digest)

    def test_transient_ssh_timeout_retries_until_the_guest_boot_deadline(self):
        guest = owned_qemu.OwnedQemu.__new__(owned_qemu.OwnedQemu)
        guest.process = MagicMock(); guest.process.poll.return_value = None
        guest.run = MagicMock(side_effect=[subprocess.TimeoutExpired("ssh", 8), subprocess.CompletedProcess("ssh", 0)])
        with patch.object(owned_qemu.time, "sleep"), patch.object(owned_qemu.time, "monotonic", return_value=0):
            guest.wait_ready("tcg")
        self.assertEqual(guest.run.call_count, 2)

    def test_ssh_timeout_cannot_extend_the_guest_boot_deadline(self):
        guest = owned_qemu.OwnedQemu.__new__(owned_qemu.OwnedQemu)
        guest.process = MagicMock(); guest.process.poll.return_value = None
        guest.run = MagicMock(side_effect=subprocess.TimeoutExpired("ssh", 8))
        with patch.object(owned_qemu.time, "sleep"), patch.object(owned_qemu.time, "monotonic", side_effect=[0, 421]), \
             self.assertRaises(TimeoutError):
            guest.wait_ready("tcg")

    def test_cleanup_targets_only_the_owned_process_handle(self):
        guest = owned_qemu.OwnedQemu.__new__(owned_qemu.OwnedQemu)
        guest.process = MagicMock(); guest.process.poll.return_value = None
        guest.process.wait.side_effect = [subprocess.TimeoutExpired("owned qemu", 10), 0]
        guest.stderr = MagicMock(); guest.provenance = {}
        guest.close()
        guest.process.terminate.assert_called_once_with()
        guest.process.kill.assert_called_once_with()
        self.assertTrue(guest.provenance["ownedQemuStopped"])
        guest.stderr.close.assert_called_once_with()


class EvidenceTests(unittest.TestCase):
    def valid(self):
        return dict(result="passed", physicalDevices=False, radioInitialized=False, retainedGrabSamples=1,
            environment={"kind": "owned-qemu-guest", "instance": "token"},
            cases=[dict(name=name, passed=True) for name in sorted(REQUIRED_CASES)])

    def test_all_required_assertions_and_correct_guest_are_required(self):
        value = self.valid()
        verify_result(value, "token")
        alterations = [lambda v: v["cases"].pop(), lambda v: v["cases"].append(v["cases"][0]),
            lambda v: v.update(retainedGrabSamples=0), lambda v: v.update(physicalDevices=True),
            lambda v: v.update(cleanupError="retained journal"),
            lambda v: v["environment"].update(instance="different guest")]
        for alter in alterations:
            changed = copy.deepcopy(value); alter(changed)
            with self.subTest(value=changed), self.assertRaises(RuntimeError):
                verify_result(changed, "token")


if __name__ == "__main__":
    unittest.main()
