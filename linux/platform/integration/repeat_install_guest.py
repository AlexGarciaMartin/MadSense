#!/usr/bin/env python3
"""Idle upgrade/reinstall check in the marked disposable QEMU guest only."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "research"))
from ci_environment import validate_environment

SERVICE = "madsense-broker.service"


def preflight():
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(5)
        client.connect("/run/madsense/broker.sock")
        client.sendall(b'{"id":1,"op":"preflight"}\n')
        buffer = bytearray()
        while b"\n" not in buffer:
            data = client.recv(4096)
            if not data or len(buffer) + len(data) > 16384:
                raise RuntimeError("Preflight ended without a bounded response")
            buffer.extend(data)
        return json.loads(buffer.partition(b"\n")[0])


def pid():
    value = int(subprocess.check_output(["systemctl", "show", SERVICE, "-p", "MainPID", "--value"], text=True))
    if value < 2:
        raise RuntimeError("Broker must be running before idle upgrade experiment")
    return value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--owned-guest-token", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise RuntimeError("Owned guest root orchestrator required")
    scope = validate_environment(args.owned_guest_token)
    if scope["kind"] != "owned-qemu-guest":
        raise RuntimeError("Owned QEMU guest required")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise RuntimeError("Fresh output directory required")
    result = dict(schema=1, result="failed", environment=scope, physicalDevices=False, radioInitialized=False, installations=[])
    installer = Path(__file__).resolve().parents[1] / "install.sh"
    try:
        for number in range(2):
            if Path("/run/madsense/lease.json").exists() or Path("/run/udev/rules.d/72-madsense-lease.rules").exists():
                raise RuntimeError("Refusing upgrade experiment with any pending physical recovery")
            before_pid, before = pid(), preflight()
            completed = subprocess.run(["sh", str(installer)], capture_output=True, timeout=45)
            (output / f"install-{number}.stdout").write_bytes(completed.stdout)
            (output / f"install-{number}.stderr").write_bytes(completed.stderr)
            if completed.returncode:
                raise RuntimeError("Idle installer rejected the owned test environment")
            deadline = time.monotonic() + 10
            while True:
                try:
                    after_pid, after = pid(), preflight()
                    break
                except (OSError, RuntimeError):
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(.05)
            record = dict(beforePid=before_pid, afterPid=after_pid, before=before, after=after)
            result["installations"].append(record)
            if before_pid == after_pid:
                raise RuntimeError("Idle upgrade did not restart the installed service")
            payload = after.get("result", {})
            if (after.get("ok") is not True or payload.get("available") is not True
                    or payload.get("leaseActive") is not False or payload.get("recoveryPending") is not False):
                raise RuntimeError("Reinstalled broker did not explicitly confirm idle preflight")
            stored = int(subprocess.check_output(["systemctl", "show", SERVICE, "-p", "NFileDescriptorStore", "--value"], text=True))
            if stored != 0:
                raise RuntimeError("Idle upgraded broker retained unexpected FDSTORE descriptors")
        result["installedCode"] = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in (
            Path("/usr/libexec/madsense/broker.py"), Path("/usr/libexec/madsense/broker_core.py"),
            Path("/etc/systemd/system/madsense-broker.service"), Path("/usr/share/polkit-1/actions/org.madsense.bridge.policy"))}
        result["result"] = "passed"
    except BaseException as error:
        result["reason"] = f"{type(error).__name__}: {error}"
    finally:
        (output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    return 0 if result["result"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
