"""Reusable, disposable guest lifecycle for Linux platform integration tests.

Image pins and scope checks are shared with the kernel experiment. Private keys,
the cloud-init seed and disk images stay in work, never in collected evidence.
"""
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "research"))
from ci_environment import validate_environment
from qemu_kernel import IMAGE_BASE, IMAGE_NAME, IMAGE_SHA256, KEYRING
from kvm_acceleration import AcceleratedQemu


def fresh_directories(output, work):
    output, work = Path(output).resolve(), Path(work).resolve()
    if output == work or output.is_relative_to(work) or work.is_relative_to(output):
        raise RuntimeError("Evidence and private guest work directories must be separate")
    for path in (output, work):
        if path.exists() and (not path.is_dir() or any(path.iterdir())):
            raise RuntimeError(f"Fresh directory required: {path}")
    output.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    work.chmod(0o700)
    return output, work


def verify_signed_image(manifest, image_path, expected_name=IMAGE_NAME, expected_digest=IMAGE_SHA256):
    # Caller must first verify this exact manifest's detached GPG signature.
    hashes = {}
    for line in Path(manifest).read_text().splitlines():
        digest, name = line.split(maxsplit=1)
        name = name.lstrip("*")
        if name in hashes:
            raise RuntimeError("Duplicate image name in the signed manifest")
        hashes[name] = digest
    if hashes.get(expected_name) != expected_digest:
        raise RuntimeError("Signed manifest does not contain the pinned image digest")
    with Path(image_path).open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != expected_digest:
        raise RuntimeError("Ubuntu image digest mismatch")


class OwnedQemu:
    def __init__(self, output, work):
        if validate_environment()["kind"] != "github-hosted":
            raise RuntimeError("Guest launcher requires a disposable GitHub-hosted runner")
        self.output, self.work = fresh_directories(output, work)
        self.token = str(uuid.uuid4())
        self.process = None
        self.acceleration = None
        self.provenance = dict(schema=1, instance=self.token, result="failed",
            imageUrl=f"{IMAGE_BASE}/{IMAGE_NAME}", imageSha256=IMAGE_SHA256,
            physicalDevicePassthrough=False, radioPassthrough=False,
            hostKernelModified=False, gamesValidated=False, physicalLatencyValidated=False,
            ownedQemuStopped=False)

    def command(self, label, argv, timeout=120, required=True):
        with (self.output / f"{label}.stdout").open("wb") as stdout, (self.output / f"{label}.stderr").open("wb") as stderr:
            result = subprocess.run([str(value) for value in argv], stdin=subprocess.DEVNULL,
                                    stdout=stdout, stderr=stderr, timeout=timeout)
        if required and result.returncode:
            raise RuntimeError(f"{label} failed with exit {result.returncode}")
        return result

    def boot(self):
        self.command("host-uname", ["uname", "-a"])
        self.command("qemu-version", ["qemu-system-x86_64", "--version"])
        for name in ("SHA256SUMS", "SHA256SUMS.gpg", IMAGE_NAME):
            target = self.work / name if name == IMAGE_NAME else self.output / name
            self.command(f"download-{name}", ["curl", "--fail", "--location", "--retry", "2",
                "--max-time", "300", "--output", target, f"{IMAGE_BASE}/{name}"], timeout=330)
        self.command("verify-image-signature", ["gpgv", "--keyring", KEYRING,
            self.output / "SHA256SUMS.gpg", self.output / "SHA256SUMS"])
        verify_signed_image(self.output / "SHA256SUMS", self.work / IMAGE_NAME)
        self.provenance.update(imageSignatureVerified=True, imageDigestVerified=True,
            keyringSha256=hashlib.sha256(Path(KEYRING).read_bytes()).hexdigest())
        self.command("image-info", ["qemu-img", "info", "--output=json", self.work / IMAGE_NAME])
        self.command("image-overlay", ["qemu-img", "create", "-f", "qcow2", "-F", "qcow2", "-b",
            self.work / IMAGE_NAME, self.work / "guest.qcow2", "12G"])
        user_key, host_key = self.work / "user_key", self.work / "host_key"
        for key in (user_key, host_key):
            self.command(f"keygen-{key.name}", ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", key])
        user_data = {
            "users": [{"name": "ubuntu", "shell": "/bin/bash", "lock_passwd": True,
                "sudo": "ALL=(ALL) NOPASSWD:ALL", "ssh_authorized_keys": [user_key.with_suffix(".pub").read_text().strip()]}],
            "ssh_pwauth": False, "disable_root": True,
            "ssh_keys": {"ed25519_private": host_key.read_text(), "ed25519_public": host_key.with_suffix(".pub").read_text().strip()},
            "write_files": [{"path": "/etc/madsense-ci-owner", "owner": "root:root", "permissions": "0644", "content": self.token + "\n"}],
            "package_update": False, "package_upgrade": False,
        }
        (self.work / "user-data").write_text("#cloud-config\n" + json.dumps(user_data))
        (self.work / "user-data").chmod(0o600)
        (self.work / "meta-data").write_text(json.dumps({"instance-id": self.token, "local-hostname": "madsense-platform-probe"}))
        self.command("cloud-seed", ["cloud-localds", self.work / "seed.img", self.work / "user-data", self.work / "meta-data"])
        with socket.socket() as endpoint:
            endpoint.bind(("127.0.0.1", 0))
            port = endpoint.getsockname()[1]
        known = self.work / "known_hosts"
        known.write_text(f"[127.0.0.1]:{port} " + host_key.with_suffix(".pub").read_text())
        self.provenance.update(virtualCpus=2, memoryMiB=2048,
                               sshBind="127.0.0.1", sshPort=port)
        def arguments(accelerator):
            return ["qemu-system-x86_64", "-machine", f"q35,accel={accelerator}",
            "-cpu", "host" if accelerator == "kvm" else "max", "-smp", "2", "-m", "2048", "-uuid", self.token,
            "-nodefaults", "-display", "none", "-monitor", "none", "-serial", f"file:{self.output / 'guest-console.log'}", "-no-reboot",
            "-drive", f"file={self.work / 'guest.qcow2'},format=qcow2,if=virtio",
            "-drive", f"file={self.work / 'seed.img'},format=raw,if=virtio,readonly=on",
            "-netdev", f"user,id=net0,hostfwd=tcp:127.0.0.1:{port}-:22",
            "-device", "virtio-net-pci,netdev=net0", "-device", "virtio-rng-pci"]
        self.acceleration = AcceleratedQemu(self.output, self.provenance)
        accelerator = self.acceleration.start(arguments)
        self.process = self.acceleration.process
        common = ["-i", str(user_key), "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
            "-o", "StrictHostKeyChecking=yes", "-o", f"UserKnownHostsFile={known}"]
        self.ssh = ["ssh", *common, "-p", str(port), "ubuntu@127.0.0.1"]
        self.scp = ["scp", *common, "-P", str(port)]
        self.wait_ready(accelerator)
        self.run("cloud-init", "sudo -n cloud-init status --wait", timeout=180)

    def wait_ready(self, accelerator):
        started = time.monotonic()
        deadline = started + (180 if accelerator == "kvm" else 420)
        while True:
            if self.process.poll() is not None:
                raise RuntimeError("Owned QEMU exited before SSH readiness")
            try:
                attempt = self.run("ssh-ready", "true", timeout=8, required=False)
                if attempt.returncode == 0:
                    self.provenance["guestSshReadySeconds"] = time.monotonic() - started
                    break
            except subprocess.TimeoutExpired:
                pass  # A slow TCG guest can accept TCP before sshd can answer.
            if time.monotonic() > deadline:
                raise TimeoutError("Owned guest did not become ready within its boot budget")
            time.sleep(2)

    def run(self, label, command, timeout=120, required=True):
        return self.command(label, [*self.ssh, command], timeout, required)

    def upload(self, directory):
        self.run("guest-directory", "mkdir -p /home/ubuntu/madsense")
        self.command("copy-payload", [*self.scp, "-r", str(directory) + "/.",
            "ubuntu@127.0.0.1:/home/ubuntu/madsense/"], timeout=180)

    def collect(self, name):
        # Fixed relative names only; callers cannot select private guest/work paths.
        if name not in ("prepare.log", "result", "usbip-binary.txt"):
            raise ValueError("Unsupported evidence entry")
        self.command(f"collect-{name}", [*self.scp, "-r",
            f"ubuntu@127.0.0.1:/home/ubuntu/madsense/{name}", self.output / name], required=False)

    def close(self):
        if self.acceleration is not None:
            self.acceleration.close()
