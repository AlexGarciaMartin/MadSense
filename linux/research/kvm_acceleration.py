"""Scoped KVM ACL and QMP-verified acceleration for disposable hosted CI only.

No chmod, group changes, udev rules, module loading or mitigation changes.
Root operations affect only the already registered /dev/kvm character device.
"""
import argparse
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import sys
import tempfile
import time
import uuid

DEVICE = Path("/dev/kvm")


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def hosted():
    require(sys.platform == "linux" and os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted", "Disposable GitHub-hosted Linux runner required")


def node_identity(info):
    return dict(device=info.st_dev, inode=info.st_ino, rdev=info.st_rdev, uid=info.st_uid, gid=info.st_gid)


def registered_node():
    info = DEVICE.lstat()
    require(stat.S_ISCHR(info.st_mode) and info.st_uid == 0, "KVM must be an existing root-owned character device")
    number = f"{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}"
    require(Path("/sys/class/misc/kvm/dev").read_text().strip() == number
            and any(line.split() == [str(os.minor(info.st_rdev)), "kvm"]
                    for line in Path("/proc/misc").read_text().splitlines()), "KVM node must match its registered kernel device")
    return info


def api_probe():
    # No CREATE_VM here. A registered node avoids intentionally opening a stale
    # static device that would request module autoload.
    fd = -1
    try:
        import fcntl
        info = registered_node()
        fd = os.open(DEVICE, os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW)
        require(node_identity(os.fstat(fd)) == node_identity(info), "KVM node changed while opening")
        return dict(apiVersion=fcntl.ioctl(fd, 0xAE00, 0))
    except (OSError, RuntimeError) as error:
        return dict(error=str(error), errno=getattr(error, "errno", None))
    finally:
        if fd >= 0:
            os.close(fd)


def acl_text(fd):
    return subprocess.check_output(["/usr/bin/getfacl", "--omit-header", "--numeric", "--no-effective",
                                    "/proc/self/fd/" + str(fd)], pass_fds=(fd,), text=True, timeout=5).strip() + "\n"


def acl_for_uid(original, uid):
    # Reject extended ACLs instead of recalculating a mask that could unmask
    # another principal's previously ineffective permissions.
    rows = original.strip().splitlines()
    require(len(rows) == 3 and all(re.fullmatch(tag + r"::[r-][w-][x-]", row)
                                 for tag, row in zip(("user", "group", "other"), rows)), "Only a base KVM ACL is eligible")
    group = rows[1].split(":")[-1]
    mask = "rw" + group[2]
    return f"{rows[0]}\nuser:{uid}:rw-\n{rows[1]}\nmask::{mask}\n{rows[2]}\n"


def set_acl(fd, contents):
    subprocess.run(["/usr/bin/setfacl", "--no-mask", "--set-file=-", "/proc/self/fd/" + str(fd)],
                   input=contents, text=True, pass_fds=(fd,), check=True, capture_output=True, timeout=5)


def restore_exact(fd, saved, uid, info):
    require(saved["schema"] == 1 and saved["uid"] == uid and saved["identity"] == node_identity(info),
            "Refusing to restore another KVM node or caller's grant")
    require(acl_for_uid(saved["original"], uid) == saved["granted"], "Invalid saved KVM ACL")
    require(acl_text(fd) in (saved["original"], saved["granted"]), "KVM ACL changed outside this grant")
    set_acl(fd, saved["original"])
    require(acl_text(fd) == saved["original"] and stat.S_IMODE(os.fstat(fd).st_mode) == saved["mode"]
            and node_identity(DEVICE.lstat()) == saved["identity"], "KVM ACL restoration could not be verified")


def private_state(token):
    return Path("/run", "madsense-kvm-ci-" + str(uuid.UUID(token)))


def write_state(directory, saved):
    temporary = directory / "state.tmp"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(saved, stream); stream.write("\n"); stream.flush(); os.fsync(stream.fileno())
        temporary.replace(directory / "state.json")
    finally:
        temporary.unlink(missing_ok=True)


def root_operation(operation, token, uid):
    hosted()
    require(os.geteuid() == 0 and uid > 0 and os.environ.get("SUDO_UID") == str(uid), "Only the invoking ordinary runner UID may receive access")
    directory = private_state(token)
    if operation == "restore" and not directory.exists():
        return dict(restored=True, stateAbsent=True)
    info = registered_node()
    fd = os.open(DEVICE, os.O_PATH | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        require(node_identity(os.fstat(fd)) == node_identity(info), "KVM identity changed")
        if operation == "grant":
            require(api_probe().get("apiVersion") == 12, "Root KVM API probe failed")
            original = acl_text(fd)
            granted = acl_for_uid(original, uid)
            directory.mkdir(mode=0o700)  # Unique root-owned state, never overwrite.
            saved = dict(schema=1, uid=uid, identity=node_identity(info), mode=stat.S_IMODE(info.st_mode),
                         original=original, granted=granted)
            write_state(directory, saved)  # Save before the ACL mutation.
            set_acl(fd, granted)
            require(acl_text(fd) == granted, "KVM ACL grant verification failed")
            require(node_identity(DEVICE.lstat()) == saved["identity"], "KVM path changed during ACL grant")
            return dict(applied=True, uid=uid, identity=saved["identity"],
                        originalAclSha256=hashlib.sha256(original.encode()).hexdigest(),
                        grantedAclSha256=hashlib.sha256(granted.encode()).hexdigest())
        state_path = directory / "state.json"
        for path, kind, mode in ((directory, stat.S_ISDIR, 0o700), (state_path, stat.S_ISREG, 0o600)):
            value = path.lstat()
            require(kind(value.st_mode) and value.st_uid == 0 and stat.S_IMODE(value.st_mode) == mode,
                    "Unsafe root KVM recovery state")
        require(state_path.stat().st_size <= 8192, "Oversized KVM recovery state")
        saved = json.loads(state_path.read_text())
        restore_exact(fd, saved, uid, info)
        state_path.unlink(); directory.rmdir()
        return dict(restored=True, identity=saved["identity"], originalAclSha256=hashlib.sha256(saved["original"].encode()).hexdigest())
    finally:
        os.close(fd)


class TemporaryKvmAccess:
    def __init__(self, evidence):
        self.token = str(uuid.uuid4())
        self.evidence = evidence
        self.restore_required = False
        evidence.update(temporaryAclApplied=False, temporaryAclRestored=True, apiIsNotVmProof=True)

    def root(self, operation):
        result = subprocess.run(["sudo", "-n", "--preserve-env=GITHUB_ACTIONS,RUNNER_ENVIRONMENT", "/usr/bin/python3", "-I",
            str(Path(__file__).resolve()), "--operation", operation, "--token", self.token, "--uid", str(os.getuid())],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=20)
        require(result.returncode == 0, "KVM " + operation + " failed: " + result.stderr[-1500:])
        return json.loads(result.stdout)

    def prepare(self):
        hosted()
        require(os.geteuid() != 0, "QEMU must remain an ordinary runner process")
        probe = api_probe()
        self.evidence["initialApiProbe"] = probe
        if probe.get("apiVersion") == 12:
            self.evidence["access"] = "existing"
            return True
        if probe.get("errno") != errno.EACCES:
            self.evidence["fallbackReason"] = "KVM is unavailable for a reason other than device DAC permissions"
            return False
        self.restore_required = True  # Also recover if the grant helper exits after mutating.
        self.evidence["temporaryAclRestored"] = False
        try:
            grant = self.root("grant")
            self.evidence.update(temporaryAclApplied=True, grant=grant, access="temporary-runner-uid-acl")
            after = api_probe()
            self.evidence["afterGrantApiProbe"] = after
            require(after.get("apiVersion") == 12, "Runner cannot query KVM after the ACL grant")
            return True
        except Exception as error:
            self.evidence["fallbackReason"] = str(error)
            self.restore()
            return False

    def restore(self):
        if self.restore_required:
            restored = self.root("restore")
            require(restored.get("restored") is True, "KVM restoration lacks confirmation")
            self.evidence["restoration"] = restored
            self.evidence["temporaryAclRestored"] = True
            self.restore_required = False


def stop_owned(process):
    if process is not None:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=5)
        else:
            process.wait()


class Qmp:
    def __init__(self, path, process):
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.buffer = bytearray()
        self.next_id = 0
        deadline = time.monotonic() + 15
        try:
            while True:
                require(process.poll() is None, "Owned QEMU exited before QMP readiness")
                try:
                    self.socket.connect(str(path)); break
                except (FileNotFoundError, ConnectionRefusedError):
                    require(time.monotonic() < deadline, "Owned QMP socket did not appear")
                    time.sleep(.05)
            self.socket.settimeout(10)
            require("QMP" in self.read(), "Invalid QMP greeting")
            self.execute("qmp_capabilities")
        except BaseException:
            self.socket.close(); raise

    def read(self):
        while b"\n" not in self.buffer:
            data = self.socket.recv(4096)
            require(data and len(self.buffer) + len(data) <= 65536, "QMP response ended or exceeded its bound")
            self.buffer.extend(data)
        line, _, rest = self.buffer.partition(b"\n")
        self.buffer = bytearray(rest)
        return json.loads(line)

    def execute(self, command):
        self.next_id += 1
        self.socket.sendall((json.dumps(dict(execute=command, id=self.next_id)) + "\n").encode())
        for _ in range(64):
            response = self.read()
            if response.get("id") == self.next_id:
                require("return" in response, "QMP command failed: " + json.dumps(response))
                return response["return"]
        raise RuntimeError("Too many unrelated QMP events")

    def close(self):
        self.socket.close()


class AcceleratedQemu:
    """Owns QEMU and restores its optional ACL only after every child is reaped."""
    def __init__(self, output, provenance):
        self.output, self.provenance = Path(output), provenance
        self.evidence = provenance.setdefault("kvmAcceleration", {})
        self.access = TemporaryKvmAccess(self.evidence)
        self.process = self.stderr = self.qmp = None
        self.directory = tempfile.TemporaryDirectory(prefix="madsense-qmp-")

    def stop_process(self):
        stop_owned(self.process)  # If this fails, do not restore under a live QEMU.
        self.process = None
        if self.qmp is not None:
            self.qmp.close(); self.qmp = None
        if self.stderr is not None:
            self.stderr.close(); self.stderr = None

    def start(self, argv_for_accelerator):
        candidate = self.access.prepare()
        for accelerator in (("kvm", "tcg") if candidate else ("tcg",)):
            path = Path(self.directory.name, "control.sock")
            path.unlink(missing_ok=True)  # Private directory, only our exited child's socket.
            argv = [*argv_for_accelerator(accelerator), "-S", "-qmp", f"unix:{path},server=on,wait=off"]
            (self.output / f"qemu-{accelerator}-arguments.json").write_text(json.dumps(argv, indent=2) + "\n")
            try:
                self.stderr = (self.output / f"qemu-{accelerator}.stderr").open("wb")
                started = time.monotonic()
                self.process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                                stderr=self.stderr, umask=0o077)
                self.qmp = Qmp(path, self.process)
                observed = self.qmp.execute("query-kvm")
                require(observed.get("enabled") is (accelerator == "kvm"), "QMP accelerator does not match the selected mode")
                if accelerator == "kvm":
                    require(observed.get("present") is True, "QMP did not confirm built-in KVM support")
                    self.evidence["qmpKvmConfirmed"] = True
                self.evidence["qmp"] = observed
                self.evidence["qmpStartupSeconds"] = time.monotonic() - started
                self.provenance["accelerator"] = accelerator
                (self.output / "qemu-arguments.json").write_text(json.dumps(argv, indent=2) + "\n")
                self.qmp.execute("cont")
                return accelerator
            except Exception as error:
                self.stop_process()
                self.access.restore()
                if accelerator != "kvm":
                    raise
                self.evidence["qmpKvmConfirmed"] = False
                self.evidence["fallbackReason"] = "KVM QEMU startup/QMP failed: " + str(error)
        raise RuntimeError("No accelerator could start")

    def close(self):
        try:
            self.stop_process()
            self.provenance["ownedQemuStopped"] = True
            self.access.restore()
            self.directory.cleanup()
        except BaseException as error:
            self.provenance["result"] = "failed"
            self.provenance["qemuCleanupError"] = str(error)
            raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--operation", choices=("grant", "restore"), required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--uid", type=int, required=True)
    arguments = parser.parse_args()
    print(json.dumps(root_operation(arguments.operation, arguments.token, arguments.uid)))
