"""Pure validation and identity checks for the privileged Linux broker."""
import base64
import binascii
import json
import os
import re
import stat
import struct
import zlib
from pathlib import Path

MAX_LINE = 16384
MAX_REPORT = 4096
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
              "attach": {"host", "port", "busId"}, "detach": {"busId"}, "release": set()}
    op = value.get("op")
    require(type(op) is str and op in fields, "invalid_request", "Unsupported operation")
    require(set(value) == {"id", "op"} | fields[op], "invalid_request", "Unexpected or missing fields")
    if op == "acquire":
        require(type(value["path"]) is str and re.fullmatch(r"/dev/hidraw[0-9]+", value["path"]),
                "invalid_request", "A physical hidraw node is required")
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


def foreign_hidraw_owners(target_rdev, own_pid, proc=Path("/proc")):
    """Permission denial must precede this scan. Never treat inaccessible processes as clear."""
    targets = set(target_rdev) if isinstance(target_rdev, (tuple, list, set, frozenset)) else {target_rdev}
    owners = []
    for entry in proc.iterdir():
        if not entry.name.isdecimal() or int(entry.name) == own_pid:
            continue
        try:
            start = process_start(int(entry.name), proc)
            fds = list((entry / "fd").iterdir())
            for fd in fds:
                try:
                    info = fd.stat()
                except FileNotFoundError:
                    continue  # A descriptor was closed during enumeration.
                if stat.S_ISCHR(info.st_mode) and info.st_rdev in targets:
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
