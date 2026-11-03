import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from debian_qemu import verify_image, validate_image_info
from ge_namespace_probe import validate_child


class DebianImageTests(unittest.TestCase):
    def test_exact_pin_rejects_corrupt_image_and_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); image = root / "guest.qcow2"; metadata = root / "guest.json"; manifest = root / "SHA512SUMS"
            image.write_bytes(b"test qcow bytes"); metadata.write_text('{}')
            digest = hashlib.sha512(image.read_bytes()).hexdigest()
            manifest.write_text(digest + '  guest.qcow2\n')
            kwargs = dict(expected_name=image.name, expected_bytes=image.stat().st_size, expected_sha512=digest,
                manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(),
                metadata_sha256=hashlib.sha256(metadata.read_bytes()).hexdigest())
            verify_image(manifest, image, metadata, **kwargs)
            image.write_bytes(b"test qcow bytex")
            with self.assertRaisesRegex(RuntimeError, 'digest mismatch'):
                verify_image(manifest, image, metadata, **kwargs)
            image.write_bytes(b"test qcow bytes"); manifest.write_text(digest + '  other.qcow2\n')
            with self.assertRaisesRegex(RuntimeError, 'manifest/metadata'):
                verify_image(manifest, image, metadata, **kwargs)

    def test_matching_manifest_digest_cannot_hide_duplicate_image(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); image = root / 'image'; image.write_bytes(b'x')
            metadata = root / 'metadata'; metadata.write_bytes(b'{}')
            digest = hashlib.sha512(b'x').hexdigest(); manifest = root / 'sums'
            manifest.write_text((digest + '  image\n') * 2)
            with self.assertRaisesRegex(RuntimeError, 'duplicate'):
                verify_image(manifest, image, metadata, expected_name='image', expected_bytes=1, expected_sha512=digest,
                    manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(),
                    metadata_sha256=hashlib.sha256(metadata.read_bytes()).hexdigest())

    def test_external_backing_and_unbounded_virtual_disk_rejected(self):
        value = {'format': 'qcow2', 'virtual-size': 3*1024**3}
        validate_image_info(value)
        for change in ({'backing-filename':'/etc/shadow'}, {'format':'raw'}, {'encrypted':True},
                       {'virtual-size':True}, {'virtual-size':13*1024**3}, {'virtual-size':32*1024**3}):
            with self.assertRaises(RuntimeError): validate_image_info(value | change)

    def test_namespace_must_preserve_nonroot_identity_and_no_route(self):
        value = {'ordinaryUid':1000,'ordinaryGid':1000,'networkRouteAbsent':True}
        validate_child(value,1000,1000)
        for change in ({'ordinaryUid':0}, {'ordinaryGid':0}, {'ordinaryUid':True}, {'networkRouteAbsent':False}):
            with self.assertRaises(RuntimeError): validate_child(value | change,1000,1000)


if __name__ == '__main__': unittest.main()
