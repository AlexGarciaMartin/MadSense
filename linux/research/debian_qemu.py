"""Opt-in Debian cloud guest; HTTPS/pinned SHA-512, explicitly not GPG signed.

Ubuntu's signed-image OwnedQemu implementation is unchanged. This adapter reuses
only its owned SSH/QMP/collection lifecycle, not its image verification claim.
"""
import hashlib
import json
from pathlib import Path
import socket
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "platform/tests"))
from owned_qemu import OwnedQemu
from kvm_acceleration import AcceleratedQemu

IMAGE_BASE = "https://cloud.debian.org/images/cloud/trixie/20261026-2643"
IMAGE_NAME = "debian-13-generic-amd64-20261026-2643.qcow2"
IMAGE_BYTES = 438894592
IMAGE_SHA512 = "5bd891c6e62d01639f52a5587b72a4607421e445732214be787dba6043cf9797a6ff90bed982aada6a6c7e619f1f956d961c4fad76757171abd607920aac098a"
MANIFEST_SHA256 = "f9e3b6d25a73706755c83d34edf9ad52e990b543169d695a8fd95037b3f6155f"
METADATA_NAME = "debian-13-generic-amd64-20261026-2643.json"
METADATA_SHA256 = "53d4966f917a68aa71dfd5c546099620cce4b11751fd7565d0cfc72a521ec382"


def verify_image(manifest, image, metadata, *, expected_name=IMAGE_NAME,
                 expected_bytes=IMAGE_BYTES, expected_sha512=IMAGE_SHA512,
                 manifest_sha256=MANIFEST_SHA256, metadata_sha256=METADATA_SHA256):
    """Check exact bytes against a retained HTTPS manifest and independent pin."""
    manifest, image, metadata = map(Path, (manifest, image, metadata))
    if (hashlib.sha256(manifest.read_bytes()).hexdigest() != manifest_sha256
            or hashlib.sha256(metadata.read_bytes()).hexdigest() != metadata_sha256):
        raise RuntimeError("Debian manifest/metadata differs from the reviewed HTTPS pin")
    rows = {}
    for line in manifest.read_text().splitlines():
        value, name = line.split(maxsplit=1)
        name = name.lstrip("*")
        if name in rows or len(value) != 128 or any(c not in "0123456789abcdef" for c in value):
            raise RuntimeError("Malformed or duplicate Debian checksum entry")
        rows[name] = value
    if rows.get(expected_name) != expected_sha512 or image.stat().st_size != expected_bytes:
        raise RuntimeError("Debian image identity/size mismatch")
    with image.open("rb") as source:
        if hashlib.file_digest(source, "sha512").hexdigest() != expected_sha512:
            raise RuntimeError("Debian image digest mismatch")


def validate_image_info(info):
    if (info.get("format") != "qcow2" or info.get("backing-filename")
            or info.get("full-backing-filename") or info.get("encrypted")
            or type(info.get("virtual-size")) is not int
            or not 1024**3 <= info["virtual-size"] <= 12 * 1024**3):
        raise RuntimeError("Unexpected Debian image format, backing chain or virtual size")


class OwnedDebianQemu(OwnedQemu):
    def __init__(self, output, work):
        super().__init__(output, work)
        self.provenance.pop("imageSha256", None)
        self.provenance.update(guestDistribution="debian-13-generic", imageUrl=f"{IMAGE_BASE}/{IMAGE_NAME}",
            imageSha512=IMAGE_SHA512, imageBytes=IMAGE_BYTES, imageSignatureVerified=False,
            imageVerification="official HTTPS + pinned SHA-512; upstream cloud checksum has no GPG signature",
            namespaceSecurityPolicyModified=False)

    def boot(self):
        self.command("host-uname", ["uname", "-a"])
        self.command("qemu-version", ["qemu-system-x86_64", "--version"])
        for name, maximum in (("SHA512SUMS", 65536), (METADATA_NAME, 1048576), (IMAGE_NAME, IMAGE_BYTES)):
            target = self.work / name if name == IMAGE_NAME else self.output / name
            self.command(f"download-{name}", ["curl", "--fail", "--proto", "=https", "--proto-redir", "=https",
                "--location", "--retry", "2", "--max-time", "300", "--max-filesize", str(maximum),
                "--output", target, f"{IMAGE_BASE}/{name}"], timeout=330)
        verify_image(self.output / "SHA512SUMS", self.work / IMAGE_NAME, self.output / METADATA_NAME)
        self.provenance.update(imageDigestVerified=True, manifestSha256=MANIFEST_SHA256,
                               imageMetadataSha256=METADATA_SHA256)
        self.command("image-info", ["qemu-img", "info", "--output=json", self.work / IMAGE_NAME])
        validate_image_info(json.loads((self.output / "image-info.stdout").read_text()))
        self.command("image-overlay", ["qemu-img", "create", "-f", "qcow2", "-F", "qcow2", "-b",
            self.work / IMAGE_NAME, self.work / "guest.qcow2", "12G"])
        user_key, host_key = self.work / "user_key", self.work / "host_key"
        for key in (user_key, host_key):
            self.command(f"keygen-{key.name}", ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", key])
        # Preserve the existing harness's /home/ubuntu path; this is an explicit
        # ephemeral account name, not a claim about the guest distribution.
        user_data = {"users": [{"name": "ubuntu", "shell": "/bin/bash", "lock_passwd": True,
            "sudo": "ALL=(ALL) NOPASSWD:ALL", "ssh_authorized_keys": [user_key.with_suffix(".pub").read_text().strip()]}],
            "ssh_pwauth": False, "disable_root": True,
            "ssh_keys": {"ed25519_private": host_key.read_text(), "ed25519_public": host_key.with_suffix(".pub").read_text()},
            "write_files": [{"path": "/etc/madsense-ci-owner", "owner": "root:root", "permissions": "0644",
                             "content": self.token + "\n"}], "package_update": False, "package_upgrade": False}
        (self.work / "user-data").write_text("#cloud-config\n" + json.dumps(user_data))
        (self.work / "user-data").chmod(0o600)
        (self.work / "meta-data").write_text(json.dumps({"instance-id": self.token, "local-hostname": "madsense-debian-consumers"}))
        self.command("cloud-seed", ["cloud-localds", self.work / "seed.img", self.work / "user-data", self.work / "meta-data"])
        with socket.socket() as endpoint:
            endpoint.bind(("127.0.0.1", 0)); port = endpoint.getsockname()[1]
        known = self.work / "known_hosts"
        known.write_text(f"[127.0.0.1]:{port} " + host_key.with_suffix(".pub").read_text())
        self.provenance.update(virtualCpus=2, memoryMiB=2048, sshBind="127.0.0.1", sshPort=port)
        def arguments(accelerator):
            return ["qemu-system-x86_64", "-machine", f"q35,accel={accelerator}",
                "-cpu", "host" if accelerator == "kvm" else "max", "-smp", "2", "-m", "2048", "-uuid", self.token,
                "-nodefaults", "-display", "none", "-monitor", "none", "-serial", f"file:{self.output / 'guest-console.log'}",
                "-no-reboot", "-drive", f"file={self.work / 'guest.qcow2'},format=qcow2,if=virtio",
                "-drive", f"file={self.work / 'seed.img'},format=raw,if=virtio,readonly=on",
                "-netdev", f"user,id=net0,hostfwd=tcp:127.0.0.1:{port}-:22",
                "-device", "virtio-net-pci,netdev=net0", "-device", "virtio-rng-pci"]
        self.acceleration = AcceleratedQemu(self.output, self.provenance)
        accelerator = self.acceleration.start(arguments); self.process = self.acceleration.process
        common = ["-i", str(user_key), "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
            "-o", "StrictHostKeyChecking=yes", "-o", f"UserKnownHostsFile={known}"]
        self.ssh = ["ssh", *common, "-p", str(port), "ubuntu@127.0.0.1"]
        self.scp = ["scp", *common, "-P", str(port)]
        self.wait_ready(accelerator)
        self.run("cloud-init", "sudo -n cloud-init status --wait", timeout=180)
