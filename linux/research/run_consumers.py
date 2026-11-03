#!/usr/bin/env python3
"""Read exact virtual-node contract, then run observers as an ordinary account.

The owning guest harness creates that account and grants access only to its
virtual nodes. This runner changes no ACLs, services, drivers or USB/IP ports.
Output delivery remains a separate assertion against exporter/fixture capture.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import shutil
import stat
import subprocess
import tempfile
import time
from ci_environment import validate_environment


def validate_ready(data):
    token = data["ownedGuestToken"]
    environment = validate_environment(token)
    if environment["kind"] != "owned-qemu-guest":
        raise RuntimeError("Consumer experiment requires owned hardware-free QEMU")
    parent = Path(data["usbParent"])
    if (not parent.is_absolute() or parent != parent.resolve()
            or not str(parent).startswith("/sys/devices/platform/vhci_hcd.")
            or parent.name != data["localBusId"]):
        raise RuntimeError("Exact newly imported VHCI USB parent required")
    vendor, product = data["vendorId"], data["productId"]
    if (vendor, product) not in ((0x054c, 0x0ce6), (0x054c, 0x0df2), (0x045e, 0x028e)):
        raise RuntimeError("Unsupported consumer persona")
    if (int((parent / "idVendor").read_text(), 16) != vendor
            or int((parent / "idProduct").read_text(), 16) != product):
        raise RuntimeError("USB identity changed")
    nodes = []
    for key, subsystem, prefix, directory in (
            ("evdevPath", "input", "event", "/dev/input"),
            ("hidrawPath", "hidraw", "hidraw", "/dev")):
        value = data.get(key)
        if value is None and key == "hidrawPath" and vendor == 0x045e:
            continue
        node = Path(value)
        if (node.parent != Path(directory) or not node.name.startswith(prefix)
                or not node.name[len(prefix):].isdigit()
                or not stat.S_ISCHR(node.lstat().st_mode)
                or not (Path("/sys/class", subsystem, node.name) / "device").resolve().is_relative_to(parent)):
            raise RuntimeError(f"{key} is not an exact character node below the owned USB device")
        nodes.append(node)
    return token, parent, vendor, product, nodes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ready", type=Path, required=True)
    parser.add_argument("--binaries", type=Path, required=True)
    parser.add_argument("--user", required=True, help="Existing isolated consumer account, with no fixture access")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--state", choices=("neutral", "south"), default="neutral")
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise RuntimeError("Root orchestrator required to drop all supplementary groups")
    account = pwd.getpwnam(args.user)
    if not account.pw_uid or not account.pw_gid:
        raise RuntimeError("Consumer account must have nonzero UID/GID")
    data = json.loads(args.ready.read_text())
    token, parent, vendor, product, nodes = validate_ready(data)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise RuntimeError("Fresh output directory required")
    result = dict(schema=1, result="failed", ready=data, consumers=[],
                  outputDeliveryVerified=False, gamesValidated=False, physicalLatencyValidated=False)
    # /home/ubuntu may be 0750. A root-owned, traversable copy avoids granting
    # access to the repository or depending on the consumer's home permissions.
    try:
        with tempfile.TemporaryDirectory(prefix="madsense-consumers-", dir="/run") as directory:
            stage = Path(directory)
            stage.chmod(0o755)
            names = ["consumer_sdl2", "consumer_sdl3"]
            if vendor == 0x045e:
                names.insert(0, "ff_probe")
            for name in names:
                target = stage / name
                shutil.copyfile(args.binaries / name, target)
                target.chmod(0o755)
                argv = [str(target), token, str(parent), data["evdevPath"]]
                if name != "ff_probe":
                    argv += [data.get("hidrawPath") or "-", f"{vendor:04x}", f"{product:04x}", args.state]
                # Each consumer opens independently, with only the named account's
                # primary group. No root-equivalent inherited capabilities/groups.
                started = time.monotonic_ns()
                completed = subprocess.run(argv, user=account.pw_uid, group=account.pw_gid,
                                           extra_groups=[], cwd=stage, stdin=subprocess.DEVNULL,
                                           capture_output=True, timeout=30,
                                           env={"PATH": "/usr/bin:/bin", "HOME": directory, "LANG": "C.UTF-8"})
                (output / f"{name}.stdout").write_bytes(completed.stdout)
                (output / f"{name}.stderr").write_bytes(completed.stderr)
                record = dict(name=name, exitCode=completed.returncode,
                              binarySha256=hashlib.sha256(target.read_bytes()).hexdigest(),
                              startedMonotonicNs=started, endedMonotonicNs=time.monotonic_ns())
                result["consumers"].append(record)
                try:
                    record["evidence"] = json.loads(completed.stdout)
                except (ValueError, UnicodeDecodeError):
                    raise RuntimeError(f"{name} did not produce evidence JSON")
                if completed.returncode or record["evidence"]["result"] != "passed":
                    raise RuntimeError(f"{name} failed")
                validate_ready(data)
            result["result"] = "passed"
    except Exception as error:
        result["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        (output / "result.json").write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
