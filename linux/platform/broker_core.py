"""Pure validation and identity checks for the privileged Linux broker."""
import base64
import binascii
import json
import os
import re
import stat
import struct
import time
import zlib
from pathlib import Path

MAX_LINE = 16384
MAX_REPORT = 4096
INPUT_FLOW_VERSION = 1
INPUT_WINDOW = 8
MAX_INPUT_SEQUENCE = 2**63 - 1
INPUT_CREDIT_TIMEOUT_NS = 5_000_000_000
DEVICE_PATH = re.compile(r"/dev/(?:hidraw[0-9]+|input/(?:event|js|mouse)[0-9]+)\Z")
HID_NAME = re.compile(r"0005:054[Cc]:(?:0[Cc][Ee]6|0[Dd][Ff]2)\.[0-9A-Fa-f]+\Z")
BUS_ID = re.compile(r"([1-9][0-9]{0,4})-1\Z")


def neutral_report(kind, sequence):
    """Exact zero-only variants of the shared OutputReport.ForBluetooth encoder."""
    require(kind in ("rumble_legacy", "rumble_v2", "triggers_off") and integer(sequence, 0, 15),
            "invalid_request", "Invalid neutral report variant")
    data = bytearray(78)
    data[0], data[1], data[2] = 0x31, sequence << 4, 0x10
    if kind == "rumble_legacy":
        data[3] = 3
    elif kind == "rumble_v2":
        data[3], data[41] = 2, 4
    else:
        data[3], data[13], data[24] = 12, 5, 5
    data[74:] = struct.pack("<I", zlib.crc32(b"\xa2" + data[:74]))
    return bytes(data)


class BrokerError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def require(condition, code, message):
    if not condition:
        raise BrokerError(code, message)


def integer(value, low, high):
    return type(value) is int and low <= value <= high


class InputCredits:
    """Eight issued reports, released only by this lease's cumulative consumer ACK."""
    def __init__(self, lease_id, clock=None):
        require(type(lease_id) is str and re.fullmatch(r"[0-9a-f]{32}", lease_id),
                "invalid_ack", "Invalid input lease identity")
        self.lease_id = lease_id
        self.clock = clock or time.monotonic_ns
        self.issued = self.acknowledged = 0
        self.blocked_count = self.blocked_ns = self.max_blocked_ns = 0
        self.blocked_since = None

    def can_read(self):
        return self.issued - self.acknowledged < INPUT_WINDOW

    def check_timeout(self):
        require(self.blocked_since is None or self.clock() - self.blocked_since < INPUT_CREDIT_TIMEOUT_NS,
                "client_stalled", "Input credits remained exhausted for five seconds; kernel input backlog is unverified")

    def reserve(self):
        require(self.can_read() and self.issued < MAX_INPUT_SEQUENCE,
                "client_stalled", "Input credit window or sequence limit reached")
        self.issued += 1
        if not self.can_read():
            self.blocked_since = self.clock()
            self.blocked_count += 1
        return self.issued

    def acknowledge(self, lease_id, through):
        require(lease_id == self.lease_id and integer(through, 1, self.issued)
                and through >= self.acknowledged, "invalid_ack", "Invalid input credit acknowledgement")
        if through == self.acknowledged:
            return  # Idempotent duplicate; never creates additional credit.
        if self.blocked_since is not None:
            elapsed = self.clock() - self.blocked_since
            self.blocked_ns += elapsed
            self.max_blocked_ns = max(self.max_blocked_ns, elapsed)
            self.blocked_since = None
        self.acknowledged = through

    def metrics(self):
        elapsed = 0 if self.blocked_since is None else self.clock() - self.blocked_since
        return {"creditBlockedCount": self.blocked_count, "creditBlockedNs": self.blocked_ns + elapsed,
                "creditMaxBlockedNs": max(self.max_blocked_ns, elapsed)}


def request(line):
    require(len(line) <= MAX_LINE, "invalid_request", "Request exceeds the line limit")
    try:
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate key")
                result[key] = value
            return result
        value = json.loads(line, object_pairs_hook=unique)
    except (ValueError, UnicodeError, RecursionError):
        raise BrokerError("invalid_request", "Invalid JSON object") from None
    require(type(value) is dict and integer(value.get("id"), 0, 2**63 - 1),
            "invalid_request", "An integer request id is required")
    fields = {"preflight": set(), "acquire": {"path"}, "write": {"data"},
              "get_feature": {"reportId", "size"}, "send_feature": {"data"},
              "attach": {"host", "port", "busId"}, "detach": {"busId"}, "release": set(),
              "input_ack": {"leaseId", "through"}}
    op = value.get("op")
    require(type(op) is str and op in fields, "invalid_request", "Unsupported operation")
    expected = {"id", "op"} | fields[op]
    if op == "acquire" and "inputFlowVersion" in value:
        expected.add("inputFlowVersion")
    require(set(value) == expected, "invalid_ack" if op == "input_ack" else "invalid_request", "Unexpected or missing fields")
    if op == "acquire":
        require(type(value["path"]) is str and re.fullmatch(r"/dev/hidraw[0-9]+", value["path"]),
                "invalid_request", "A physical hidraw node is required")
        require("inputFlowVersion" not in value or integer(value["inputFlowVersion"], INPUT_FLOW_VERSION, INPUT_FLOW_VERSION),
                "invalid_request", "Unsupported input flow-control version")
    if op == "input_ack":
        require(value["id"] == 0 and type(value["leaseId"]) is str
                and re.fullmatch(r"[0-9a-f]{32}", value["leaseId"])
                and integer(value["through"], 1, MAX_INPUT_SEQUENCE),
                "invalid_ack", "Invalid input credit acknowledgement fields")
    if op in ("write", "send_feature"):
        value["decoded"] = report(value["data"])
    if op == "get_feature":
        require(integer(value["reportId"], 0, 255) and integer(value["size"], 1, MAX_REPORT),
                "invalid_request", "Feature report bounds are invalid")
    if op in ("attach", "detach"):
        match = BUS_ID.fullmatch(value["busId"]) if type(value["busId"]) is str else None
        require(match and 1 <= int(match[1]) <= 65535,
                "invalid_request", "Invalid exported bus id")
    if op == "attach":
        require(value["host"] == "127.0.0.1" and integer(value["port"], 1024, 65535),
                "invalid_request", "Only an unprivileged IPv4 loopback exporter is supported")
    return value


def report(value):
    require(type(value) is str and len(value) <= ((MAX_REPORT + 2) // 3) * 4,
            "invalid_request", "Report exceeds the size limit")
    try:
        result = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        raise BrokerError("invalid_request", "Invalid base64 report") from None
    require(1 <= len(result) <= MAX_REPORT, "invalid_request", "Report size is invalid")
    return result


def process_start(pid, proc=Path("/proc")):
    # comm may contain spaces and parentheses. Fields after its final ')' begin at state (3).
    tail = (proc / str(pid) / "stat").read_text().rpartition(")")[2].split()
    require(len(tail) > 19, "identity_failed", "Process identity is unavailable")
    return int(tail[19])


def foreign_device_owners(target_rdev, own_pid, proc=Path("/proc"), *, writable_targets=(),
                          force_feedback_targets=(), systemd_grabs=(), systemd_start=None):
    """Find existing hidraw/force-feedback users and other evdev output writers.

    Permission denial must precede this scan. EVIOCGRAB routes input events but
    does not exclude writes from another already-open evdev file. A read-only
    force-feedback descriptor can own effects started through a now-closed
    writer, and can still change them. Only our read-only PID 1 stored grabs
    are exempt after the store barrier, within the explicit root-trusted scope.
    Never treat inaccessible process/descriptor metadata as clear.
    """
    targets = set(target_rdev) if isinstance(target_rdev, (tuple, list, set, frozenset)) else {target_rdev}
    writable_targets = set(writable_targets)
    force_feedback_targets = set(force_feedback_targets)
    relevant_targets = targets | writable_targets | force_feedback_targets
    stored = {tuple(node[key] for key in ("dev", "inode", "rdev")) for node in systemd_grabs}

    def inspect_descriptor(entry, fd, start):
        # A proc FD number is not a stable handle: close/reuse can race either
        # stat or fdinfo. Reclassify its current node instead of treating a
        # successful second stat as proof that the original metadata vanished.
        stage = "node"
        def failure(message):
            return BrokerError("isolation_unverified",
                               f"{message} (pid={entry.name}, fd={fd.name}, stage={stage})")
        def same_process():
            if process_start(int(entry.name), proc) != start:
                raise failure("Process identity changed during descriptor inspection")
        last_race = stage
        for attempt in range(3):
            try:
                if attempt:
                    stage = "process-before-retry"
                    same_process()
                stage = "node"
                info = fd.stat()
                relevant = stat.S_ISCHR(info.st_mode) and info.st_rdev in relevant_targets
                if not relevant:
                    if attempt:
                        stage = "process-after-retry"
                        same_process()
                    return False
                stage = "process-before-metadata"
                same_process()
                conflict = info.st_rdev in targets
                if not conflict:
                    stage = "fdinfo"
                    with (entry / "fdinfo" / fd.name).open() as metadata:
                        text = metadata.read(8193)
                    flags = re.findall(r"^flags:[ \t]*([0-7]+)[ \t]*$", text, re.MULTILINE)
                    if len(text) > 8192 or len(flags) != 1:
                        raise failure("Cannot determine an existing evdev descriptor's access mode")
                    access = int(flags[0], 8) & 3  # Linux O_ACCMODE, including on portable tests.
                    trusted_grab = (int(entry.name) == 1 and start == systemd_start and access == 0
                                    and (info.st_dev, info.st_ino, info.st_rdev) in stored)
                    conflict = ((info.st_rdev in force_feedback_targets and not trusted_grab) or access in (1, 2))
                stage = "node-after-metadata"
                after = fd.stat()
                if not stat.S_ISCHR(after.st_mode) or identity(after) != identity(info):
                    last_race = stage
                    continue
                stage = "process-after-metadata"
                same_process()
                return conflict
            except (FileNotFoundError, ProcessLookupError):
                last_race = stage
                try:
                    stage = "node-recheck"
                    replacement = fd.stat()
                except (FileNotFoundError, ProcessLookupError):
                    return False  # The descriptor has actually gone.
                except (PermissionError, OSError, ValueError):
                    raise failure("Cannot inspect descriptor metadata") from None
                try:
                    stage = "process-recheck"
                    same_process()
                except (FileNotFoundError, ProcessLookupError):
                    if entry.exists():
                        raise failure("A live process identity is unavailable") from None
                    return False
                except (PermissionError, OSError, ValueError):
                    raise failure("Cannot inspect descriptor process identity") from None
                if not stat.S_ISCHR(replacement.st_mode) or replacement.st_rdev not in relevant_targets:
                    # This successful stat has already classified the new node,
                    # and the process identity was checked afterwards. Retain
                    # that evidence instead of racing another unrelated close.
                    return False
                # A live replacement must undergo the entire classification,
                # including access mode and identity checks, on the next pass.
            except (PermissionError, OSError, ValueError):
                raise failure("Cannot inspect descriptor metadata") from None
        stage = last_race
        raise failure("Descriptor metadata did not stabilize after three inspections")

    owners = []
    for entry in proc.iterdir():
        if not entry.name.isdecimal() or int(entry.name) == own_pid:
            continue
        try:
            start = process_start(int(entry.name), proc)
            fds = list((entry / "fd").iterdir())
            for fd in fds:
                if inspect_descriptor(entry, fd, start):
                    owners.append((int(entry.name), start, fd.name))
        except (FileNotFoundError, ProcessLookupError):
            if entry.exists():
                raise BrokerError("isolation_unverified", "A live process could not be inspected") from None
        except (PermissionError, OSError, ValueError):
            raise BrokerError("isolation_unverified", "Cannot inspect every process descriptor table") from None
    return owners


def identity(info):
    return {"dev": info.st_dev, "inode": info.st_ino, "rdev": info.st_rdev}


def same_node(info, saved):
    return stat.S_ISCHR(info.st_mode) and identity(info) == {key: saved.get(key) for key in ("dev", "inode", "rdev")}


def validate_journal(value, boot):
    require(type(value) is dict and value.get("version") == 1 and type(value.get("nodes")) is list,
            "recovery_failed", "Invalid recovery journal")
    require(len(value["nodes"]) <= 32, "recovery_failed", "Recovery journal exceeds device bounds")
    for node in value["nodes"]:
        require(type(node) is dict and type(node.get("path")) is str and DEVICE_PATH.fullmatch(node["path"]),
                "recovery_failed", "Invalid journal device path")
        require(all(integer(node.get(key), 0, 2**64 - 1) for key in ("dev", "inode", "rdev", "uid", "gid", "mode"))
                and node["mode"] <= 0o7777 and type(node.get("acl")) is str and len(node["acl"]) <= 8192,
                "recovery_failed", "Invalid journal node identity")
    grabs = value.get("storedGrabs", [])
    require(type(grabs) is list and len(grabs) <= 32, "recovery_failed", "Invalid stored grab manifest")
    for item in grabs:
        require(type(item) is dict and type(item.get("name")) is str
                and re.fullmatch(r"madsense-grab-[0-9a-f]{32}-[0-9]{1,2}", item["name"])
                and all(integer(item.get(key), 0, 2**64 - 1) for key in ("dev", "inode", "rdev")),
                "recovery_failed", "Invalid stored grab identity")
    imported = value.get("importName")
    require(imported is None or (type(imported) is str and re.fullmatch(r"madsense-import-[0-9a-f]{32}", imported)),
            "recovery_failed", "Invalid stored import name")
    return value.get("boot") == boot
