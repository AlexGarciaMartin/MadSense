#!/usr/bin/env python3
"""Run the neutral kernel proof in an owned, signed Ubuntu cloud-image guest."""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import shutil
import socket
import subprocess
import time
import uuid
from ci_environment import validate_environment
from kvm_acceleration import AcceleratedQemu

IMAGE_BASE = "https://cloud-images.ubuntu.com/noble/20261017"
IMAGE_NAME = "noble-server-cloudimg-amd64.img"
IMAGE_SHA256 = "04e8f2461a16a44e78a370284471f29616cdd9baf1893df25807b71f985b6c94"
KEYRING = "/usr/share/keyrings/ubuntu-cloudimage-keyring.gpg"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--persona", choices=("dualsense", "edge", "xbox"), required=True)
    parser.add_argument("--probe-directory", type=Path, required=True)
    parser.add_argument("--host-probe", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    args = parser.parse_args()
    if validate_environment()["kind"] != "github-hosted":
        raise RuntimeError("QEMU launcher must run on the disposable hosted runner")
    output, work = args.output.resolve(), args.work.resolve()
    for path in (output, work):
        path.mkdir(parents=True, exist_ok=True)
        if any(path.iterdir()):
            raise RuntimeError(f"Fresh directory required: {path}")
    token = str(uuid.uuid4())
    provenance = dict(schema=1, persona=args.persona, result="failed", instance=token,
                      imageUrl=f"{IMAGE_BASE}/{IMAGE_NAME}", imageSha256=IMAGE_SHA256,
                      physicalDevicePassthrough=False, radioPassthrough=False,
                      hostKernelModified=False, gamesValidated=False, physicalLatencyValidated=False)
    qemu = None
    owned = AcceleratedQemu(output, provenance)
    def command(label, argv, timeout=120, required=True):
        with (output / f"{label}.stdout").open("wb") as stdout, (output / f"{label}.stderr").open("wb") as stderr:
            result = subprocess.run([str(value) for value in argv], stdin=subprocess.DEVNULL,
                                    stdout=stdout, stderr=stderr, timeout=timeout)
        if required and result.returncode:
            raise RuntimeError(f"{label} failed with exit {result.returncode}")
        return result
    try:
        command("host-uname", ["uname", "-a"])
        command("qemu-version", ["qemu-system-x86_64", "--version"])
        for name in ("SHA256SUMS", "SHA256SUMS.gpg", IMAGE_NAME):
            target = work / name if name == IMAGE_NAME else output / name
            command(f"download-{name}", ["curl", "--fail", "--location", "--retry", "2", "--max-time", "300",
                    "--output", target, f"{IMAGE_BASE}/{name}"], timeout=330)
        command("verify-image-signature", ["gpgv", "--keyring", KEYRING,
                                            output / "SHA256SUMS.gpg", output / "SHA256SUMS"])
        signed_hashes = {}
        for line in (output / "SHA256SUMS").read_text().splitlines():
            digest, name = line.split(maxsplit=1)
            signed_hashes[name.lstrip("*")] = digest
        if signed_hashes.get(IMAGE_NAME) != IMAGE_SHA256:
            raise RuntimeError("Signed manifest does not contain the pinned image digest")
        with (work / IMAGE_NAME).open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual != IMAGE_SHA256:
            raise RuntimeError("Ubuntu image digest mismatch")
        provenance.update(imageSignatureVerified=True, imageDigestVerified=True,
                          keyringSha256=hashlib.sha256(Path(KEYRING).read_bytes()).hexdigest())
        command("image-info", ["qemu-img", "info", "--output=json", work / IMAGE_NAME])
        command("image-overlay", ["qemu-img", "create", "-f", "qcow2", "-F", "qcow2", "-b",
                                  work / IMAGE_NAME, work / "guest.qcow2", "12G"])
        user_key, host_key = work / "user_key", work / "host_key"
        for key in (user_key, host_key):
            command(f"keygen-{key.name}", ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", key])
        user_data = {
            "users": [{"name": "ubuntu", "shell": "/bin/bash", "lock_passwd": True,
                       "sudo": "ALL=(ALL) NOPASSWD:ALL", "ssh_authorized_keys": [user_key.with_suffix(".pub").read_text().strip()]}],
            "ssh_pwauth": False, "disable_root": True,
            "ssh_keys": {"ed25519_private": host_key.read_text(), "ed25519_public": host_key.with_suffix(".pub").read_text().strip()},
            "write_files": [{"path": "/etc/madsense-ci-owner", "owner": "root:root", "permissions": "0644", "content": token + "\n"}],
            "package_update": False, "package_upgrade": False,
        }
        (work / "user-data").write_text("#cloud-config\n" + json.dumps(user_data))
        (work / "user-data").chmod(0o600)
        (work / "meta-data").write_text(json.dumps({"instance-id": token, "local-hostname": "madsense-kernel-probe"}))
        command("cloud-seed", ["cloud-localds", work / "seed.img", work / "user-data", work / "meta-data"])
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        known = work / "known_hosts"
        known.write_text(f"[127.0.0.1]:{port} " + host_key.with_suffix(".pub").read_text())
        provenance.update(virtualCpus=2, memoryMiB=2048, sshBind="127.0.0.1", sshPort=port)
        def arguments(accelerator):
            return ["qemu-system-x86_64", "-machine", f"q35,accel={accelerator}", "-cpu", "host" if accelerator == "kvm" else "max",
                "-smp", "2", "-m", "2048", "-uuid", token, "-nodefaults", "-display", "none", "-monitor", "none",
                "-serial", f"file:{output / 'guest-console.log'}", "-no-reboot",
                "-drive", f"file={work / 'guest.qcow2'},format=qcow2,if=virtio",
                "-drive", f"file={work / 'seed.img'},format=raw,if=virtio,readonly=on",
                "-netdev", f"user,id=net0,hostfwd=tcp:127.0.0.1:{port}-:22",
                "-device", "virtio-net-pci,netdev=net0", "-device", "virtio-rng-pci"]
        accelerator = owned.start(arguments)
        qemu = owned.process
        common = ["-i", str(user_key), "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
                  "-o", "StrictHostKeyChecking=yes", "-o", f"UserKnownHostsFile={known}"]
        ssh = ["ssh", *common, "-p", str(port), "ubuntu@127.0.0.1"]
        scp = ["scp", *common, "-P", str(port)]
        boot_started = time.monotonic()
        deadline = boot_started + (180 if accelerator == "kvm" else 420)
        while True:
            if qemu.poll() is not None:
                raise RuntimeError("Owned QEMU exited before SSH readiness")
            try:
                attempt = command("ssh-ready", [*ssh, "true"], timeout=8, required=False)
                connected = attempt.returncode == 0
            except subprocess.TimeoutExpired:
                # A booting TCG guest may accept TCP before sshd can answer.
                # This bounded attempt is retryable within the overall boot budget.
                connected = False
            if connected:
                provenance["guestSshReadySeconds"] = time.monotonic() - boot_started
                break
            if time.monotonic() > deadline:
                raise TimeoutError("Owned guest did not become ready within its boot budget")
            time.sleep(2)
        command("cloud-init", [*ssh, "sudo -n cloud-init status --wait"], timeout=180)
        command("guest-directory", [*ssh, "mkdir -p /home/ubuntu/madsense"])
        payload = work / "payload"
        payload.mkdir()
        shutil.copytree(args.probe_directory.resolve(), payload / "probe")
        shutil.copy2(args.host_probe.resolve(), payload / "host_probe")
        research = payload / "research"
        research.mkdir()
        for name in ("kernel_probe.py", "ci_environment.py", "prepare_kernel.sh", "run_guest.sh"):
            shutil.copy2(Path(__file__).resolve().parent / name, research / name)
        command("copy-probe", [*scp, "-r", str(payload) + "/.", "ubuntu@127.0.0.1:/home/ubuntu/madsense/"], timeout=180)
        command("probe-permissions", [*ssh, "chmod +x /home/ubuntu/madsense/probe/MadSense.Linux.Probe /home/ubuntu/madsense/host_probe"])
        remote = "bash /home/ubuntu/madsense/research/run_guest.sh " + shlex.quote(token) + " " + shlex.quote(args.persona)
        try:
            run = command("guest-run", [*ssh, remote], timeout=600, required=False)
            provenance["guestExit"] = run.returncode
        except subprocess.TimeoutExpired:
            provenance["guestExit"] = 124
        for name in ("prepare.log", "result"):
            command(f"collect-{name}", [*scp, "-r", f"ubuntu@127.0.0.1:/home/ubuntu/madsense/{name}", output / name], required=False)
        result_path = output / "result/result.json"
        if provenance["guestExit"] != 0 or not result_path.exists():
            raise RuntimeError("Owned guest kernel proof failed; see guest-run and prepare logs")
        result = json.loads(result_path.read_text())
        if result.get("result") != "passed" or result.get("ownedPortDetached") is not True:
            raise RuntimeError("Guest kernel evidence or owned-port cleanup failed")
        provenance["result"] = "passed"
    except BaseException as error:
        provenance["reason"] = f"{type(error).__name__}: {error}"
    finally:
        try:
            owned.close()
        finally:
            (output / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
            print(json.dumps(provenance), flush=True)
    return 0 if provenance["result"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
