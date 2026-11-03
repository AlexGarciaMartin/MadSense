#!/usr/bin/python3
"""One-lease Linux privilege broker. No shell commands, unbinds, or application termination."""
import argparse
import array
import base64
from collections import deque
import errno
import hashlib
import json
import os
import re
import queue
import select
import signal
import socket
import stat
import struct
import subprocess
import sys
import time
import threading
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from broker_core import (BrokerError, DEVICE_PATH, HID_NAME, MAX_LINE, MAX_REPORT,
                         foreign_device_owners, identity, process_start, request,
                         require, same_node, validate_journal, neutral_report)

RUN = Path("/run/madsense")
JOURNAL = RUN / "lease.json"
RULE = Path("/run/udev/rules.d/72-madsense-lease.rules")
SOCKET = RUN / "broker.sock"
ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "LC_ALL": "C"}
TOOLS = {}
IO_READ = 2
IO_WRITE = 1
EVIOCGRAB = 0x40044590
EVIOCGBIT_TYPES = 0x80044520  # EVIOCGBIT(0, sizeof(uint32_t)); EV_MAX is 31.
OUTPUT_EVENT_TYPES = (1 << 0x11) | (1 << 0x12) | (1 << 0x15)  # EV_LED, EV_SND, EV_FF.


class RecoveryRequired(RuntimeError):
    """Leave PID 1's descriptors intact and force a service restart."""


def queue_packet(client, value):
    packet = (json.dumps(value, separators=(",", ":")) + "\n").encode()
    require(len(client["out"]) + len(packet) <= 65536, "client_stalled", "Client response queue is full")
    if value.get("event") == "input":
        require(len(client["inputEnds"]) < 8, "client_stalled", "Eight physical input reports are already pending")
        client["inputEnds"].append(client["sent"] + len(client["out"]) + len(packet))
    client["out"].extend(packet)


def advance_output(client, count):
    del client["out"][:count]
    client["sent"] += count
    while client["inputEnds"] and client["inputEnds"][0] <= client["sent"]:
        client["inputEnds"].popleft()


def ioctl_code(direction, number, size):
    return (direction << 30) | (size << 16) | (ord("H") << 8) | number


def tool(name):
    if name not in TOOLS:
        for directory in ("/usr/bin", "/usr/sbin", "/bin", "/sbin"):
            path = Path(directory, name).resolve()
            if path.exists():
                info = path.stat()
                require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022,
                        "setup_needed", "A required system utility is not root-owned")
                TOOLS[name] = str(path)
                break
        require(name in TOOLS, "setup_needed", "Required system utility is missing: " + name)
    return TOOLS[name]


def command(name, args, data=None, fds=(), timeout=10):
    try:
        result = subprocess.run([tool(name), *args], input=data, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env=ENV, pass_fds=fds, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise BrokerError("operation_failed", name + " timed out") from None
    require(result.returncode == 0, "operation_failed", name + " failed: " + result.stderr.decode(errors="replace")[:300])
    return result.stdout


def boot_id():
    return Path("/proc/sys/kernel/random/boot_id").read_text().strip()


def atomic_json(path, value):
    data = (json.dumps(value, separators=(",", ":")) + "\n").encode()
    require(len(data) <= 65536, "recovery_failed", "Recovery journal exceeds the supported bound")
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        remaining = memoryview(data)
        while remaining:
            written = os.write(fd, remaining)
            require(written > 0, "recovery_failed", "Recovery journal write did not progress")
            remaining = remaining[written:]
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def install_rule(contents):
    # udev reads only *.rules. Never expose a partly written final rule: a
    # crash before link leaves no rule; a crash after link leaves the exact
    # journaled bytes. link is exclusive and cannot overwrite another rule.
    RULE.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    temporary = RULE.with_name(".madsense-" + uuid.uuid4().hex + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    try:
        remaining = memoryview(contents)
        while remaining:
            written = os.write(fd, remaining)
            require(written > 0, "operation_failed", "Isolation rule write did not progress")
            remaining = remaining[written:]
        os.fsync(fd)
        os.link(temporary, RULE, follow_symlinks=False)
        directory = os.open(RULE.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        os.close(fd)
        temporary.unlink(missing_ok=True)


def read_journal():
    if not JOURNAL.exists():
        return None
    fd = os.open(JOURNAL, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o077 and info.st_size <= 65536,
                "recovery_failed", "Unsafe recovery journal")
        return json.loads(os.read(fd, 65537))
    finally:
        os.close(fd)


def notify(message, fds=()):
    address = os.environ.get("NOTIFY_SOCKET", "")
    require(address.startswith(("/", "@")), "setup_needed", "The broker must run in its systemd service")
    if address.startswith("@"):
        address = "\0" + address[1:]
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM | socket.SOCK_CLOEXEC) as endpoint:
        endpoint.connect(address)
        ancillary = [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", fds))] if fds else []
        endpoint.sendmsg([message.encode()], ancillary)


def store_descriptor(fd, name):
    require(int(os.environ.get("FDSTORE", "0")) >= 33, "setup_needed", "systemd descriptor storage is required")
    notify("FDSTORE=1\nFDPOLL=0\nFDNAME=" + name, (fd,))


def descriptor_barrier():
    for _ in descriptor_barrier_steps():
        time.sleep(.01)


def descriptor_barrier_steps():
    # PID 1 closes the write end only after processing preceding notifications.
    read_end, write_end = os.pipe2(os.O_CLOEXEC)
    try:
        notify("BARRIER=1", (write_end,))
        os.close(write_end)
        write_end = -1
        deadline = time.monotonic() + 5
        while not select.select([read_end], [], [], 0)[0]:
            require(time.monotonic() < deadline, "operation_failed", "Descriptor storage acknowledgement timed out")
            yield
        require(os.read(read_end, 1) == b"", "operation_failed", "Invalid descriptor storage acknowledgement")
    finally:
        os.close(read_end)
        if write_end >= 0:
            os.close(write_end)


def expected_import_path(status_path, selected_port):
    """Resolve a VHCI HS port to its USB sysfs path before kernel attachment."""
    match = re.fullmatch(r"status(?:\.([0-9]+))?", status_path.name)
    parent = re.fullmatch(r"vhci_hcd\.([0-9]+)", status_path.parent.name)
    require(match and parent, "attach_failed", "Unsupported VHCI controller layout")
    controller = status_path.parent.parent / ("vhci_hcd." + (match[1] or parent[1]))
    hs_ports = []
    for line in status_path.read_text().splitlines()[1:]:
        fields = line.split()
        require(len(fields) == 7, "attach_failed", "Unsupported VHCI status format")
        if fields[0] == "hs":
            hs_ports.append(int(fields[1]))
    require(selected_port in hs_ports and hs_ports == sorted(set(hs_ports)) and 1 <= len(hs_ports) <= 31,
            "attach_failed", "Cannot map the selected high-speed port")
    roots = [path for path in controller.glob("usb[0-9]*")
             if re.fullmatch(r"usb[0-9]+", path.name) and (path / "speed").read_text().strip() == "480"]
    require(len(roots) == 1, "attach_failed", "Cannot identify the VHCI USB2 root hub")
    root = roots[0]
    bus = (root / "busnum").read_text().strip()
    require(re.fullmatch(r"[1-9][0-9]*", bus) and root.name == "usb" + bus,
            "attach_failed", "USB root-hub identity changed")
    return root / f"{bus}-{hs_ports.index(selected_port) + 1}"


def wait_owned_import(saved):
    """Observe disappearance only. Never mutate a possibly reused numeric port."""
    token = saved.get("importToken")
    if not token:
        return
    require(type(token) is dict and all(type(token.get(key)) is int for key in ("port", "devid", "sockfd")),
            "recovery_failed", "Invalid owned import observation token")
    deadline = time.monotonic() + 5
    while True:
        matches = False
        for status_path in Path("/sys/devices/platform").glob("vhci_hcd.*/status*"):
            for line in status_path.read_text().splitlines()[1:]:
                fields = line.split()
                require(len(fields) == 7, "recovery_failed", "Unsupported VHCI status format during cleanup")
                if int(fields[1]) == token["port"] and (int(fields[2]) not in (4, 6) or (
                        int(fields[4], 16) == token["devid"] and int(fields[5]) == token["sockfd"]
                        and int(fields[2]) != 4)):
                    # Intermediate/error states omit the identity columns and
                    # do not prove removal. Wait; this never detaches a port.
                    matches = True
        if not matches:
            break
        require(time.monotonic() < deadline, "detach_failed", "Owned USB/IP shutdown has not completed; no numeric port was detached")
        time.sleep(.02)
    device = saved.get("importDevice")
    if device:
        require(type(device) is dict and type(device.get("path")) is str
                and re.fullmatch(r"/sys/devices/platform/vhci_hcd\.[0-9]+/usb[0-9]+/[0-9]+-[0-9.]+", device["path"])
                and type(device.get("inode")) is int and type(device.get("dev")) is int,
                "recovery_failed", "Invalid owned virtual-device identity")
        while True:
            try:
                info = Path(device["path"]).stat()
            except FileNotFoundError:
                return
            if (info.st_dev, info.st_ino) != (device["dev"], device["inode"]):
                return
            require(time.monotonic() < deadline, "detach_failed", "Owned virtual device still exists after transport shutdown")
            time.sleep(.02)
    else:
        expected = saved.get("expectedImportPath")
        require(type(expected) is str
                and re.fullmatch(r"/sys/devices/platform/vhci_hcd\.[0-9]+/usb[0-9]+/[0-9]+-[0-9]+", expected),
                "recovery_failed", "Pre-enumeration import path is missing; removal cannot be verified")
        # Before enumeration there is no inode to distinguish a reused port.
        # Wait conservatively for absence, even if another importer now uses it.
        while True:
            try:
                Path(expected).stat()
            except FileNotFoundError:
                return
            require(time.monotonic() < deadline, "detach_failed", "Pre-enumeration virtual-device path is still present")
            time.sleep(.02)


def write_neutral(fd, sequence=0):
    # Matches shared encoder fixtures; deliberately does not invent audio mute or
    # firmware commands. A controller that disappeared cannot acknowledge reset.
    deadline = time.monotonic() + 1
    for index, kind in enumerate(("rumble_legacy", "rumble_v2", "triggers_off")):
        data = neutral_report(kind, (sequence + index) & 15)
        while True:
            try:
                require(os.write(fd, data) == len(data), "reset_failed", "Physical neutral report was truncated")
                break
            except BlockingIOError:
                require(time.monotonic() < deadline, "reset_failed", "Physical neutral report timed out")
                select.select([], [fd], [], .02)


def neutralize(fd, sequence=0):
    # HID writes can block inside a transport even on O_NONBLOCK descriptors.
    # A separate, fixed helper invocation bounds the reset without killing apps.
    try:
        result = subprocess.run([sys.executable, "-I", str(Path(__file__).resolve()),
                                 "--neutral-fd", str(fd), "--neutral-sequence", str(sequence & 15)],
                                pass_fds=(fd,), env=ENV, stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE, timeout=1.5)
    except subprocess.TimeoutExpired:
        raise BrokerError("reset_failed", "The bounded physical effect reset timed out") from None
    require(result.returncode == 0, "reset_failed", "Physical effect reset failed: " + result.stderr.decode(errors="replace")[:200])


def reset_saved_device(saved):
    if not saved or not saved.get("isolationVerified") or saved.get("boot") != boot_id():
        return
    import fcntl
    node = next((item for item in saved["nodes"] if item["path"] == saved.get("physicalPath")), None)
    require(node is not None, "recovery_failed", "Physical reset identity is absent")
    try:
        fd = os.open(node["path"], os.O_RDWR | os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOFOLLOW)
    except FileNotFoundError:
        return
    try:
        if not same_node(os.fstat(fd), node):
            return
        raw = bytearray(8)
        fcntl.ioctl(fd, ioctl_code(IO_READ, 3, 8), raw, True)
        bus, vendor, product = struct.unpack("Ihh", raw)
        require(bus == 5 and vendor & 0xFFFF == 0x054C and product & 0xFFFF in (0x0CE6, 0x0DF2),
                "recovery_failed", "Physical reset HID identity changed")
        neutralize(fd, int(saved.get("neutralSequence", 0)) & 15)
    except OSError as error:
        if error.errno not in (errno.ENODEV, errno.EIO, errno.ENXIO):
            raise
    finally:
        os.close(fd)


def release_grab(fd):
    import fcntl
    try:
        fcntl.ioctl(fd, EVIOCGRAB, 0)
    except OSError as error:
        if error.errno not in (errno.ENODEV, errno.EINVAL, errno.ENXIO):
            raise


def recover_imports(main_process=False):
    """Recover all stored lease descriptors; preserve grabs until virtual shutdown."""
    restored = set()
    if int(os.environ.get("LISTEN_PID", "0")) != os.getpid():
        if main_process:
            saved = read_journal()
            if saved and not saved.get("importName"):
                same_boot = validate_journal(saved, boot_id())
                require(not same_boot or not saved.get("isolationVerified"),
                        "recovery_failed", "Active lease descriptors are missing from systemd; isolation cannot be verified")
                reset_saved_device(saved)
                return {item["name"] for item in saved.get("storedGrabs", [])}
        return restored
    count = int(os.environ.get("LISTEN_FDS", "0"))
    names = os.environ.get("LISTEN_FDNAMES", "").split(":") if count else []
    require(0 <= count <= 33 and len(names) == count and len(set(names)) == count,
            "recovery_failed", "Unexpected inherited descriptors")
    saved = read_journal()
    if saved:
        validate_journal(saved, boot_id())
        require(not saved.get("isolationVerified") or all(item["name"] in names for item in saved.get("storedGrabs", [])),
                "recovery_failed", "Active input grabs are missing from the descriptor store")
    grabbed = []
    for fd, name in zip(range(3, 3 + count), names):
        if re.fullmatch(r"madsense-grab-[0-9a-f]{32}-[0-9]{1,2}", name):
            record = next((item for item in (saved or {}).get("storedGrabs", []) if item["name"] == name), None)
            if saved:
                require(record is not None and same_node(os.fstat(fd), record),
                        "recovery_failed", "Stored grab identity changed")
            grabbed.append((fd, name))
            continue
        require(re.fullmatch(r"madsense-import-[0-9a-f]{32}", name), "recovery_failed", "Unknown stored descriptor")
        with socket.socket(fileno=fd) as endpoint:
            require(endpoint.family == socket.AF_INET and endpoint.type & 0xF == socket.SOCK_STREAM,
                    "recovery_failed", "Invalid stored import socket")
            try:
                require(endpoint.getpeername()[0] == "127.0.0.1", "recovery_failed", "Stored import is not loopback")
                endpoint.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass  # An already disconnected owned socket is also cleaned up.
        restored.add(name)
    if saved:
        require(not saved.get("importName") or saved["importName"] in restored,
                "recovery_failed", "Owned import socket is missing from the descriptor store")
        wait_owned_import(saved)
        reset_saved_device(saved)
    for fd, name in grabbed:
        release_grab(fd)
        os.close(fd)
        restored.add(name)
    if saved and not saved.get("importName") and not saved.get("isolationVerified"):
        # A crash while storing an incomplete acquisition may leave only a
        # subset in PID 1. No virtual import existed before storage completed.
        restored.update(item["name"] for item in saved.get("storedGrabs", []))
    return restored


def restore_journal(restored_imports=frozenset()):
    saved = read_journal()
    if saved is None:
        return
    same_boot = validate_journal(saved, boot_id())
    import_name = saved.get("importName")
    require(not (same_boot and import_name and import_name not in restored_imports),
            "recovery_failed", "Owned import awaits systemd restart recovery; physical isolation remains until then")
    require(not same_boot or all(item["name"] in restored_imports for item in saved.get("storedGrabs", [])),
            "recovery_failed", "Stored input grabs await systemd restart recovery")
    errors = []
    if RULE.exists():
        raw = RULE.read_bytes()
        require(hashlib.sha256(raw).hexdigest() == saved.get("ruleSha256"),
                "recovery_failed", "Runtime rule changed outside this lease")
    if same_boot:
        for node in saved["nodes"]:
            try:
                fd = os.open(node["path"], os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOFOLLOW)
            except FileNotFoundError:
                continue
            try:
                if not same_node(os.fstat(fd), node):
                    continue  # A reused device number/path must never be restored.
                os.fchown(fd, node["uid"], node["gid"])
                os.fchmod(fd, node["mode"])
                command("setfacl", ["--set-file=-", "/proc/self/fd/" + str(fd)], node["acl"].encode(), (fd,))
            except (OSError, BrokerError) as error:
                errors.append(str(error))
            finally:
                os.close(fd)
    require(not errors, "recovery_failed", "; ".join(errors))
    # Restore the saved base permissions first. Then let current udev/logind
    # policy replace seat ACLs, which may have changed since acquisition.
    if RULE.exists():
        RULE.unlink()
    # Recovery may restart after unlink but before reload. The daemon can still
    # have the deleted lease rule cached, so reload even when the file is gone.
    command("udevadm", ["control", "--reload-rules"])
    parent = saved.get("hidParent", {})
    if same_boot and type(parent) is dict and type(parent.get("path")) is str:
        candidate = Path(parent["path"])
        if candidate.is_relative_to(Path("/sys/devices")) and HID_NAME.fullmatch(candidate.name) and candidate.exists():
            info = candidate.stat()
            if (info.st_dev, info.st_ino) == (parent.get("dev"), parent.get("inode")):
                command("udevadm", ["trigger", "--action=change", "--parent-match=" + str(candidate)])
                command("udevadm", ["settle", "--timeout=5"])
    JOURNAL.unlink()


class Lease:
    def __init__(self, pid, uid, start, pidfd, owner_socket):
        self.pid, self.uid, self.start, self.pidfd = pid, uid, start, pidfd
        self.owner_socket = owner_socket
        self.fd = -1
        self.nodes = []
        self.grabs = []
        self.grab_names = []
        self.lease_id = uuid.uuid4().hex
        self.neutral_sequence = 0
        self.saved = {"version": 1, "boot": boot_id(), "nodes": [], "importName": None}
        self.import_socket = None
        self.import_device = None
        self.bus_id = None
        self.hid_parent = None
        self.rule = b""
        self.active = False
        self.audit = None
        self.audit_started = 0
        self.audit_due = 0

    def alive(self):
        require(not select.select([self.pidfd], [], [], 0)[0] and process_start(self.pid) == self.start,
                "owner_lost", "The lease owner exited")
        # Unlike MSG_PEEK, RDHUP detects EOF even behind unread request bytes.
        # Never consume or close this endpoint here; the main loop owns it.
        require(self.owner_socket.fileno() >= 0, "owner_lost", "The lease owner's connection closed")
        events = select.poll()
        events.register(self.owner_socket, select.POLLRDHUP | select.POLLHUP | select.POLLERR | select.POLLNVAL)
        require(not events.poll(0), "owner_lost", "The lease owner's connection closed")

    def authorize(self):
        self.alive()
        require(self.uid != 0, "not_authorized", "The application must run as an ordinary user")
        try:
            notify("WATCHDOG_USEC=130000000\nWATCHDOG=1")
            # Only this directly owned child is terminated on cancellation.
            # No external agent, process group, or caller process is signalled.
            child = subprocess.Popen([tool("pkcheck"), "--action-id", "org.madsense.bridge.acquire", "--process",
                                      f"{self.pid},{self.start},{self.uid}", "--allow-user-interaction"],
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, env=ENV)
            try:
                deadline = time.monotonic() + 120
                while True:
                    self.alive()
                    remaining = deadline - time.monotonic()
                    require(remaining > 0, "not_authorized", "Controller authorization timed out")
                    try:
                        _, error = child.communicate(timeout=min(.1, remaining))
                    except subprocess.TimeoutExpired:
                        continue
                    # An authorization completing concurrently with cancellation
                    # must never authorize a disconnected attempt.
                    self.alive()
                    require(child.returncode == 0, "not_authorized",
                            "Controller isolation was not authorized: " + error.decode(errors="replace")[:300])
                    return
            finally:
                if child.poll() is None:
                    child.kill()
                child.communicate()  # Reap our child and close its pipe on every exit.
        finally:
            notify("WATCHDOG_USEC=15000000\nWATCHDOG=1")

    def inventory(self):
        result = []
        for directory, prefix in ((Path("/sys/class/hidraw"), "/dev/"), (Path("/sys/class/input"), "/dev/input/")):
            for entry in directory.iterdir():
                path = prefix + entry.name
                if not DEVICE_PATH.fullmatch(path):
                    continue
                if (entry / "device").resolve().is_relative_to(self.hid_parent):
                    result.append(path)
        return sorted(result)

    def acquire(self, path):
        import fcntl
        self.authorize()
        self.alive()
        self.hid_parent = (Path("/sys/class/hidraw") / Path(path).name / "device").resolve(strict=True)
        require(self.hid_parent.is_relative_to(Path("/sys/devices")) and HID_NAME.fullmatch(self.hid_parent.name),
                "unsupported_device", "Only a physical Bluetooth DualSense or DualSense Edge is supported")
        parent_info = self.hid_parent.stat()
        self.saved["hidParent"] = {"path": str(self.hid_parent), "dev": parent_info.st_dev, "inode": parent_info.st_ino}
        self.saved["physicalPath"] = path
        self.fd = os.open(path, os.O_RDWR | os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOFOLLOW)
        info = os.fstat(self.fd)
        require(stat.S_ISCHR(info.st_mode), "unsupported_device", "The selected node is not a character device")
        device_number = (Path("/sys/class/hidraw") / Path(path).name / "dev").read_text().strip()
        require(device_number == f"{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}",
                "unsupported_device", "The selected node no longer matches its sysfs identity")
        raw = bytearray(8)
        fcntl.ioctl(self.fd, ioctl_code(IO_READ, 3, 8), raw, True)
        bus, vendor, product = struct.unpack("Ihh", raw)
        vendor, product = vendor & 0xFFFF, product & 0xFFFF
        require(bus == 5 and vendor == 0x054C and product in (0x0CE6, 0x0DF2),
                "unsupported_device", "Physical Bluetooth identity did not match")
        self.nodes = self.inventory()
        require(path in self.nodes and 1 <= len(self.nodes) <= 32 and any("/event" in item for item in self.nodes),
                "isolation_unverified", "All physical input children could not be identified")
        for node in self.nodes:
            node_fd = os.open(node, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOFOLLOW)
            try:
                current = os.fstat(node_fd)
                require(stat.S_ISCHR(current.st_mode), "isolation_unverified", "An input node changed type")
                acl = command("getfacl", ["--omit-header", "/proc/self/fd/" + str(node_fd)], fds=(node_fd,)).decode()
                require(len(acl) <= 8192, "isolation_unverified", "ACL exceeds the supported bound")
                event_types = 0
                if "/event" in node:
                    capabilities = bytearray(4)
                    fcntl.ioctl(node_fd, EVIOCGBIT_TYPES, capabilities, True)
                    event_types = int.from_bytes(capabilities, sys.byteorder)
                self.saved["nodes"].append({"path": node, **identity(current), "uid": current.st_uid,
                                            "gid": current.st_gid, "mode": stat.S_IMODE(current.st_mode), "acl": acl,
                                            "eventTypes": event_types, "outputCapable": bool(event_types & OUTPUT_EVENT_TYPES)})
            finally:
                os.close(node_fd)
        self.rule = (f'# MadSense temporary lease; exact validated HID parent only.\n'
                     f'SUBSYSTEM=="hidraw|input", KERNELS=="{self.hid_parent.name}", '
                     'OWNER:="root", GROUP:="root", MODE:="0600", TAG-="uaccess", TAG-="seat"\n').encode()
        self.saved["ruleSha256"] = hashlib.sha256(self.rule).hexdigest()
        self.alive()  # Inventory/ACL reads may outlast the requesting connection.
        atomic_json(JOURNAL, self.saved)  # Durable intent precedes every permission mutation.
        install_rule(self.rule)
        command("udevadm", ["control", "--reload-rules"])
        for node in self.saved["nodes"]:
            self.alive()
            node_fd = os.open(node["path"], os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOFOLLOW)
            try:
                require(same_node(os.fstat(node_fd), node), "isolation_unverified", "Input node was replaced")
                command("setfacl", ["--remove-all", "/proc/self/fd/" + str(node_fd)], fds=(node_fd,))
                os.fchown(node_fd, 0, 0)
                os.fchmod(node_fd, 0o600)
                if "/event" in node["path"]:
                    fcntl.ioctl(node_fd, EVIOCGRAB, 1)
                    self.grabs.append(node_fd)
                    node_fd = -1
            finally:
                if node_fd >= 0:
                    os.close(node_fd)
        # Existing udev records must lose seat/uaccess tags too. Trigger only this HID subtree.
        command("udevadm", ["trigger", "--action=change", "--parent-match=" + str(self.hid_parent)])
        command("udevadm", ["settle", "--timeout=5"])
        self.verify()
        self.initial_audit()
        self.saved["storedGrabs"] = []
        self.saved["systemdStart"] = process_start(1)
        for index, grabbed in enumerate(self.grabs):
            name = f"madsense-grab-{self.lease_id}-{index}"
            self.grab_names.append(name)
            self.saved["storedGrabs"].append({"name": name, **identity(os.fstat(grabbed))})
        atomic_json(JOURNAL, self.saved)
        for name, grabbed in zip(self.grab_names, self.grabs):
            store_descriptor(grabbed, name)
        descriptor_barrier()
        require(process_start(1) == self.saved["systemdStart"], "isolation_unverified", "The descriptor-store owner changed")
        self.saved["isolationVerified"] = True
        atomic_json(JOURNAL, self.saved)
        self.active = True
        self.start_audit()
        return {"isolationVerified": True, "path": path, "vendorId": vendor, "productId": product,
                "productName": "DualSense Edge" if product == 0x0DF2 else "DualSense"}

    def verify(self):
        self.alive()
        require(self.inventory() == self.nodes and RULE.read_bytes() == self.rule,
                "isolation_lost", "The physical device nodes or isolation rule changed")
        for saved in self.saved["nodes"]:
            current = os.stat(saved["path"], follow_symlinks=False)
            require(same_node(current, saved) and current.st_uid == 0 and current.st_gid == 0
                    and stat.S_IMODE(current.st_mode) == 0o600,
                    "isolation_lost", "Physical node permissions or identity changed")
            try:
                acl = os.getxattr(saved["path"], "system.posix_acl_access", follow_symlinks=False)
            except OSError as error:
                if error.errno != errno.ENODATA:
                    raise
            else:
                require(not acl, "isolation_lost", "Physical node access ACL changed outside the lease")
        for grabbed in self.grabs:
            events = select.poll()
            events.register(grabbed, select.POLLERR | select.POLLHUP | select.POLLNVAL)
            require(not events.poll(0), "isolation_lost", "A grabbed input device disappeared")
        if self.import_socket is not None and self.import_device is not None:
            path, inode = self.import_device
            require(path.exists() and path.stat().st_ino == inode,
                    "isolation_lost", "The owned virtual USB device disappeared")
        if self.active:
            self.check_audit()

    def audit_foreign(self):
        targets = {node["rdev"] for node in self.saved["nodes"] if node["path"].startswith("/dev/hidraw")}
        writers = {node["rdev"] for node in self.saved["nodes"] if node["path"].startswith("/dev/input/event") and node.get("outputCapable")}
        force_feedback = {node["rdev"] for node in self.saved["nodes"] if node["path"].startswith("/dev/input/event")
                          and node.get("eventTypes", 0) & (1 << 0x15)}
        require(targets, "isolation_unverified", "No physical hidraw interfaces were identified")
        require(not foreign_device_owners(targets, os.getpid(), writable_targets=writers,
                    force_feedback_targets=force_feedback,
                    systemd_grabs=self.saved.get("storedGrabs", ()) if self.active else (),
                    systemd_start=self.saved.get("systemdStart") if self.active else None),
                "physical_device_busy", "Another process has a physical hidraw/force-feedback interface or evdev output writer open; close it and retry")

    def start_audit(self):
        # The initial audit completes before ACTIVE. Later full /proc scans never
        # block report forwarding; failure or a bounded deadline ends the lease.
        result = queue.Queue(maxsize=1)
        self.audit = result
        self.audit_started = time.monotonic()
        def run():
            try:
                self.audit_foreign()
                result.put(None)
            except Exception as error:
                result.put(error)
        threading.Thread(target=run, name="madsense-descriptor-audit", daemon=True).start()

    def initial_audit(self):
        self.start_audit()
        try:
            error = self.audit.get(timeout=2)
        except queue.Empty:
            raise BrokerError("isolation_unverified", "Initial process descriptor audit exceeded two seconds") from None
        self.audit = None
        if isinstance(error, BrokerError):
            raise error
        require(error is None, "isolation_unverified", str(error))

    def check_audit(self):
        now = time.monotonic()
        if self.audit is not None:
            try:
                error = self.audit.get_nowait()
            except queue.Empty:
                require(now - self.audit_started < 2, "isolation_lost", "Process descriptor audit did not complete within two seconds")
                return
            self.audit = None
            require(error is None, "isolation_lost", str(error) if error else "Process descriptor audit failed")
            self.audit_due = now + .5
        if now >= self.audit_due:
            self.start_audit()

    def read_feature(self, report_id, size):
        import fcntl
        data = bytearray(size)
        data[0] = report_id
        count = fcntl.ioctl(self.fd, ioctl_code(IO_READ | IO_WRITE, 7, size), data, True)
        require(0 <= count <= size, "io_failed", "Invalid feature report length")
        return bytes(data[:count])

    def send_feature(self, data):
        import fcntl
        fcntl.ioctl(self.fd, ioctl_code(IO_READ | IO_WRITE, 6, len(data)), bytearray(data), True)

    def attach_steps(self, port, bus_id):
        """Cooperative import: the service keeps draining HID and checking isolation."""
        require(self.import_socket is None, "already_attached", "This lease already owns an import")
        self.verify()
        # Require the requesting process to own the loopback listener, not merely name a port.
        socket_ids = set()
        for item in Path(f"/proc/{self.pid}/fd").iterdir():
            try:
                target = os.readlink(item)
            except FileNotFoundError:
                continue
            if target.startswith("socket:["):
                socket_ids.add(target[8:-1])
        entries = Path("/proc/net/tcp").read_text().splitlines()[1:]
        require(any((parts := line.split())[1] == f"0100007F:{port:04X}" and parts[3] == "0A" and parts[9] in socket_ids
                    for line in entries), "invalid_exporter", "The lease owner must own the IPv4 loopback listener")
        endpoint = socket.socket(socket.AF_INET, socket.SOCK_STREAM | socket.SOCK_CLOEXEC)
        endpoint.setblocking(False)
        try:
            deadline = time.monotonic() + 5
            result = endpoint.connect_ex(("127.0.0.1", port))
            require(result in (0, errno.EINPROGRESS, errno.EWOULDBLOCK), "attach_failed", "Cannot connect to the local exporter")
            while not select.select([], [endpoint], [], 0)[1]:
                require(time.monotonic() < deadline, "attach_failed", "Local exporter connection timed out")
                yield
            require(endpoint.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR) == 0,
                    "attach_failed", "Local exporter connection failed")
            outgoing = memoryview(struct.pack("!HHI", 0x0111, 0x8003, 0) + bus_id.encode().ljust(32, b"\0"))
            while outgoing:
                require(time.monotonic() < deadline, "attach_failed", "USB/IP import handshake timed out")
                try:
                    written = endpoint.send(outgoing)
                    require(written > 0, "attach_failed", "USB/IP import request ended early")
                    outgoing = outgoing[written:]
                except BlockingIOError:
                    yield
            def receive(size):
                data = bytearray()
                while len(data) < size:
                    require(time.monotonic() < deadline, "attach_failed", "USB/IP import handshake timed out")
                    try:
                        chunk = endpoint.recv(size - len(data))
                    except BlockingIOError:
                        yield
                        continue
                    require(chunk, "attach_failed", "USB/IP import response ended early")
                    data.extend(chunk)
                return data
            version, operation, status = struct.unpack("!HHI", (yield from receive(8)))
            require((version, operation, status) == (0x0111, 3, 0), "attach_failed", "USB/IP import was rejected")
            device = yield from receive(312)
            require(bytes(device[256:288]).split(b"\0", 1)[0] == bus_id.encode(), "attach_failed", "Exported bus id changed")
            bus, number, speed, vendor, product, _bcd, _cls, _sub, _protocol, _cfg, _configs, interfaces = struct.unpack("!IIIHHHBBBBBB", device[288:])
            require((vendor, product) in ((0x054C, 0x0CE6), (0x054C, 0x0DF2), (0x045E, 0x028E))
                    and 0 < bus <= 65535 and 0 < number <= 65535 and speed in (2, 3) and 0 < interfaces <= 8,
                    "attach_failed", "Unsupported exported persona")
            status_files = sorted(Path("/sys/devices/platform").glob("vhci_hcd.*/status*"))
            candidates = []
            for path in status_files:
                for line in path.read_text().splitlines()[1:]:
                    fields = line.split()
                    require(len(fields) == 7, "attach_failed", "Unsupported VHCI status format")
                    if fields[0] == "hs" and int(fields[2]) == 4:
                        candidates.append((path, int(fields[1])))
            require(candidates, "attach_failed", "No free VHCI high-speed port is available")
            status_path, selected_port = candidates[0]
            expected_path = expected_import_path(status_path, selected_port)
            require(not expected_path.exists(), "attach_failed", "The selected USB port still has a device node")
            attach_path = status_path.parent / "attach"
            name = "madsense-import-" + uuid.uuid4().hex
            self.saved["importName"] = name
            self.import_socket, self.bus_id = endpoint, bus_id
            self.saved["neutralSequence"] = self.neutral_sequence
            self.saved["importToken"] = {"port": selected_port, "devid": (bus << 16) | number, "sockfd": endpoint.fileno()}
            self.saved["expectedImportPath"] = str(expected_path)
            store_descriptor(endpoint.fileno(), name)
            yield from descriptor_barrier_steps()
            atomic_json(JOURNAL, self.saved)
            endpoint.settimeout(None)
            # A concurrent importer wins or loses atomically in the kernel; never detach it.
            with attach_path.open("w") as control:
                control.write(f"{selected_port} {endpoint.fileno()} {(bus << 16) | number} {speed}\n")
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                for status_path in status_files:
                    for line in status_path.read_text().splitlines()[1:]:
                        fields = line.split()
                        if (len(fields) == 7 and int(fields[1]) == selected_port and int(fields[2]) == 6
                                and int(fields[4], 16) == ((bus << 16) | number)
                                and int(fields[5]) == endpoint.fileno() and fields[6] != "0-0"):
                            # VHCI reports USED before USB core publishes its
                            # sysfs alias. Non-strict resolve would return the
                            # still-missing /sys/bus alias and reject the exact
                            # expected /sys/devices path as a different device.
                            try:
                                device_path = (Path("/sys/bus/usb/devices") / fields[6]).resolve(strict=True)
                                device_info = device_path.stat()
                            except FileNotFoundError:
                                continue  # Creation/removal race; keep the same deadline.
                            require(device_path == expected_path, "attach_failed",
                                    f"The virtual device appeared on an unexpected USB path: {device_path}; expected {expected_path}")
                            self.import_device = (device_path, device_info.st_ino)
                if self.import_device is not None:
                    break
                yield
            require(self.import_device is not None, "attach_failed", "The owned virtual USB device did not enumerate")
            virtual_info = self.import_device[0].stat()
            self.saved["importDevice"] = {"path": str(self.import_device[0]), "dev": virtual_info.st_dev,
                                          "inode": virtual_info.st_ino}
            atomic_json(JOURNAL, self.saved)
            self.alive()
        except BaseException:
            # Generator cancellation is part of owner EOF/isolation-loss cleanup.
            # Finish owned transport teardown before the lease releases any grab.
            if self.import_socket is endpoint:
                self.detach()
            else:
                endpoint.close()
            raise

    def detach(self):
        if self.import_socket is not None:
            try:
                self.import_socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            wait_owned_import(self.saved)
            if self.import_device is not None:
                path, inode = self.import_device
                deadline = time.monotonic() + 5
                while path.exists() and path.stat().st_ino == inode and time.monotonic() < deadline:
                    time.sleep(.02)
                require(not path.exists() or path.stat().st_ino != inode,
                        "detach_failed", "Owned USB/IP shutdown has not completed; no numeric port was detached")
                self.import_device = None
            self.import_socket.close()
            self.import_socket = None
            name = self.saved["importName"]
            self.saved["importName"] = None
            self.saved["importToken"] = None
            self.saved["importDevice"] = None
            self.saved["expectedImportPath"] = None
            atomic_json(JOURNAL, self.saved)
            notify("FDSTOREREMOVE=1\nFDNAME=" + name)
        self.bus_id = None

    def close(self):
        self.active = False
        self.detach()  # On failure, PID 1 retains grabs; do not restore physical access.
        if self.fd >= 0 and self.saved.get("isolationVerified"):
            try:
                neutralize(self.fd, self.neutral_sequence)
            except OSError as error:
                if error.errno not in (errno.ENODEV, errno.EIO, errno.ENXIO):
                    raise
        for fd in self.grabs:
            release_grab(fd)
            os.close(fd)
        self.grabs.clear()
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1
        restore_journal(set(self.grab_names))
        for name in self.grab_names:
            notify("FDSTOREREMOVE=1\nFDNAME=" + name)
        self.grab_names.clear()


def preflight():
    require(os.geteuid() == 0 and hasattr(os, "pidfd_open"), "setup_needed", "A root system service with pidfd support is required")
    for name in ("getfacl", "setfacl", "udevadm", "pkcheck"):
        tool(name)
    require(int(os.environ.get("FDSTORE", "0")) >= 33 and os.environ.get("NOTIFY_SOCKET"),
            "setup_needed", "Install and start the MadSense system service with descriptor storage")
    require(any(Path("/sys/devices/platform").glob("vhci_hcd.*/status")),
            "setup_needed", "The vhci_hcd kernel module is not loaded")
    require(not JOURNAL.exists() and not RULE.exists(), "recovery_failed", "A previous lease still requires cleanup")


def serve():
    require(os.geteuid() == 0, "not_authorized", "The broker service requires root")
    RUN.mkdir(mode=0o755, parents=True, exist_ok=True)
    require(RUN.stat().st_uid == 0 and not RUN.stat().st_mode & 0o022, "setup_needed", "Unsafe runtime directory")
    restored = recover_imports(main_process=True)
    restore_journal(restored)
    for name in restored:
        notify("FDSTOREREMOVE=1\nFDNAME=" + name)
    if SOCKET.exists():
        require(stat.S_ISSOCK(SOCKET.lstat().st_mode) and SOCKET.lstat().st_uid == 0, "setup_needed", "Unsafe broker socket path")
        SOCKET.unlink()
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM | socket.SOCK_CLOEXEC)
    listener.bind(str(SOCKET))
    os.chmod(SOCKET, 0o666)  # Authorization is per authenticated peer, before any privileged mutation.
    listener.listen(8)
    listener.setblocking(False)
    clients = {}
    owner = None
    lease = None
    attachment = None
    stopping = False
    next_check = time.monotonic()
    next_watchdog = time.monotonic()
    next_attach = 0
    def stop(_signal, _frame):
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    def queue(client, value):
        queue_packet(client, value)
    def end_lease(reason=None):
        nonlocal lease, owner, attachment
        if lease is not None:
            if reason and owner in clients:
                try:
                    queue(clients[owner], {"event": "isolation_lost", "reason": reason})
                except BrokerError:
                    clients[owner]["closed"] = True
                clients[owner]["failed"] = True
            current, lease, owner = lease, None, None
            try:
                if attachment is not None:
                    steps, _, _ = attachment
                    attachment = None
                    steps.close()
                current.close()
            except Exception as error:
                raise RecoveryRequired("Lease cleanup requires systemd restart recovery: " + str(error)) from error
    notify("READY=1")
    try:
        while not stopping:
            if time.monotonic() >= next_watchdog:
                notify("WATCHDOG=1")
                next_watchdog = time.monotonic() + 5
            readable = [listener, *clients]
            if lease is not None:
                readable += [lease.fd, lease.pidfd]
            ready, writable, _ = select.select(readable, [sock for sock, value in clients.items() if value["out"]], [],
                                               .02 if attachment is not None else .2)
            for sock in writable:
                try:
                    count = sock.send(clients[sock]["out"])
                    advance_output(clients[sock], count)
                except OSError:
                    clients[sock]["closed"] = True
            if lease is not None and (lease.pidfd in ready or time.monotonic() >= next_check):
                try:
                    lease.verify()
                except (BrokerError, OSError) as error:
                    end_lease(str(error))
                next_check = time.monotonic() + .5
            if lease is not None and lease.fd in ready:
                try:
                    data = os.read(lease.fd, MAX_REPORT)
                    require(data, "isolation_lost", "Physical HID stream closed")
                    queue(clients[owner], {"event": "input", "data": base64.b64encode(data).decode(),
                                          "brokerReadMonotonicNs": time.monotonic_ns()})
                except BlockingIOError:
                    pass
                except (BrokerError, OSError) as error:
                    end_lease(str(error))
            if attachment is not None and time.monotonic() >= next_attach:
                steps, client, req_id = attachment
                try:
                    next(steps)
                except StopIteration:
                    attachment = None
                    queue(client, {"id": req_id, "ok": True, "result": {}})
                except (BrokerError, OSError, ValueError) as error:
                    attachment = None
                    queue(client, {"id": req_id, "ok": False, "error": {
                        "code": getattr(error, "code", "operation_failed"), "message": str(error)[:512]}})
                next_attach = time.monotonic() + .02
            for endpoint, client in list(clients.items()):
                if endpoint in ready:
                    try:
                        chunk = endpoint.recv(MAX_LINE + 1)
                        if not chunk:
                            client["closed"] = True
                        else:
                            client["lastActivity"] = time.monotonic()
                        client["in"].extend(chunk)
                        while b"\n" in client["in"]:
                            line, _, rest = client["in"].partition(b"\n")
                            client["in"] = bytearray(rest)
                            req_id = 0
                            try:
                                value = request(line)
                                req_id, op = value["id"], value["op"]
                                require(not client["failed"], "isolation_lost", "Reconnect before requesting a new lease")
                                result = {}
                                if op == "preflight":
                                    recovery_pending = lease is None and (JOURNAL.exists() or RULE.exists())
                                    try:
                                        if lease is None:
                                            preflight()
                                        result = {"available": True, "detail": "Privilege broker is available; acquisition still verifies physical isolation"}
                                    except BrokerError as error:
                                        result = {"available": False, "detail": str(error)}
                                    result.update(leaseActive=lease is not None, recoveryPending=recovery_pending)
                                elif op == "acquire":
                                    require(lease is None, "busy", "Another lease already owns the broker")
                                    preflight()
                                    lease = Lease(client["pid"], client["uid"], client["start"], client["pidfd"], endpoint)
                                    owner = endpoint
                                    try:
                                        result = lease.acquire(value["path"])
                                    except Exception:
                                        end_lease()
                                        raise
                                else:
                                    require(lease is not None and owner is endpoint and lease.active,
                                            "no_lease", "This connection does not own an active lease")
                                    lease.alive()
                                    if op == "write":
                                        result = {"written": os.write(lease.fd, value["decoded"])}
                                        if len(value["decoded"]) == 78 and value["decoded"][0] == 0x31:
                                            lease.neutral_sequence = ((value["decoded"][1] >> 4) + 1) & 15
                                    elif op == "get_feature":
                                        result = {"data": base64.b64encode(lease.read_feature(value["reportId"], value["size"])).decode()}
                                    elif op == "send_feature":
                                        lease.send_feature(value["decoded"])
                                    elif op == "attach":
                                        require(attachment is None, "busy", "An import is already in progress")
                                        attachment = (lease.attach_steps(value["port"], value["busId"]), client, req_id)
                                        next_attach = 0
                                        continue  # Reply when cooperative import completes.
                                    elif op == "detach":
                                        require(attachment is None, "busy", "An import is still in progress")
                                        require(lease.bus_id == value["busId"], "not_attached", "This lease does not own that export")
                                        lease.detach()
                                    elif op == "release":
                                        end_lease()
                                queue(client, {"id": req_id, "ok": True, "result": result})
                            except (BrokerError, OSError, ValueError) as error:
                                queue(client, {"id": req_id, "ok": False, "error": {
                                    "code": getattr(error, "code", "operation_failed"), "message": str(error)[:512]}})
                        require(len(client["in"]) <= MAX_LINE, "invalid_request", "Request line exceeds the limit")
                    except (BrokerError, OSError):
                        client["closed"] = True
                if owner is not endpoint and time.monotonic() - client["lastActivity"] >= 10:
                    client["closed"] = True
                if client["closed"] or (client["failed"] and not client["out"]):
                    if owner is endpoint:
                        end_lease()
                    endpoint.close()
                    os.close(client["pidfd"])
                    del clients[endpoint]
            # Reap EOF/expired peers before applying admission limits. A newly
            # connected caller must not lose its slot to an already closed peer.
            if listener in ready:
                endpoint, _address = listener.accept()
                endpoint.setblocking(False)
                endpoint.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
                pid, uid, _gid = struct.unpack("3i", endpoint.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                if len(clients) >= 8 or sum(client["uid"] == uid for client in clients.values()) >= 2:
                    endpoint.close()
                else:
                    pidfd = -1
                    try:
                        pidfd = os.pidfd_open(pid, 0)
                        start = process_start(pid)
                        clients[endpoint] = {"in": bytearray(), "out": bytearray(), "pid": pid, "uid": uid,
                                             "inputEnds": deque(), "sent": 0,
                                             "lastActivity": time.monotonic(),
                                             "start": start, "pidfd": pidfd, "failed": False, "closed": False}
                    except (BrokerError, OSError, ValueError):
                        if pidfd >= 0:
                            os.close(pidfd)
                        endpoint.close()
    finally:
        end_lease()
        for endpoint, client in clients.items():
            endpoint.close()
            os.close(client["pidfd"])
        listener.close()
        SOCKET.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--recover", action="store_true")
    parser.add_argument("--neutral-fd", type=int)
    parser.add_argument("--neutral-sequence", type=int, default=0)
    args = parser.parse_args()
    require(sum((args.serve, args.recover, args.neutral_fd is not None)) == 1,
            "invalid_request", "Select exactly one service operation")
    require(sys.platform == "linux" and os.geteuid() == 0, "not_authorized", "Run only as the installed Linux system service")
    if args.neutral_fd is not None:
        import fcntl
        require(args.neutral_fd >= 3 and 0 <= args.neutral_sequence <= 15,
                "invalid_request", "Invalid reset descriptor or sequence")
        require(stat.S_ISCHR(os.fstat(args.neutral_fd).st_mode), "reset_failed", "Reset descriptor is not a device")
        raw = bytearray(8)
        fcntl.ioctl(args.neutral_fd, ioctl_code(IO_READ, 3, 8), raw, True)
        bus, vendor, product = struct.unpack("Ihh", raw)
        require(bus == 5 and vendor & 0xFFFF == 0x054C and product & 0xFFFF in (0x0CE6, 0x0DF2),
                "reset_failed", "Reset descriptor is not a physical Bluetooth DualSense")
        write_neutral(args.neutral_fd, args.neutral_sequence)
    elif args.serve:
        serve()
    else:
        restored = recover_imports()
        restore_journal(restored)
        for name in restored:
            notify("FDSTOREREMOVE=1\nFDNAME=" + name)


if __name__ == "__main__":
    try:
        main()
    except (BrokerError, OSError, ValueError) as error:
        print("MadSense broker: " + str(error), file=sys.stderr)
        sys.exit(1)
