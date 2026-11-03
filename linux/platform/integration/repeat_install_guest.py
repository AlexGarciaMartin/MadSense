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


def verify_removal(installer, output, token):
    """Exercise the explicit uninstaller only after the owned guest's idle gate."""
    uninstaller = installer.with_name("uninstall.sh")
    installed = Path("/usr/libexec/madsense/broker_core.py")
    original = installed.read_bytes()
    changed = original + b"\n# Owned guest uninstall preservation check.\n"
    sentinel = installed.parent / ("uninstall-preserve-" + token)
    if sentinel.exists():
        raise RuntimeError("Unexpected pre-existing uninstall sentinel")
    sentinel.write_text(token)
    cases = []
    def run(label, path):
        completed = subprocess.run(["sh", str(path)], capture_output=True, timeout=45)
        (output / f"{label}.stdout").write_bytes(completed.stdout)
        (output / f"{label}.stderr").write_bytes(completed.stderr)
        return completed
    try:
        before = pid()
        try:
            installed.write_bytes(changed)
            denied = run("uninstall-modified", uninstaller)
            if (denied.returncode == 0 or b"Installed file differs" not in denied.stderr
                    or installed.read_bytes() != changed or pid() != before):
                raise RuntimeError("Uninstaller did not preserve the modified file and running service")
            cases.append("modified_file_preserved_before_service_stop")
        finally:
            if installed.read_bytes() == changed:
                installed.write_bytes(original)
            elif installed.read_bytes() != original:
                raise RuntimeError("Installed source changed outside the owned uninstall check")
        fixed = [installed, installed.with_name("broker.py"),
                 Path("/etc/systemd/system/madsense-broker.service"),
                 Path("/usr/share/polkit-1/actions/org.madsense.bridge.policy"),
                 Path("/etc/modules-load.d/madsense.conf")]
        for number in range(2):
            completed = run(f"uninstall-{number}", uninstaller)
            if completed.returncode or any(path.exists() or path.is_symlink() for path in fixed):
                raise RuntimeError("Idle uninstaller did not remove exactly its helper files")
            if sentinel.read_text() != token:
                raise RuntimeError("Uninstaller removed or changed an unknown helper-directory file")
            state = subprocess.check_output(["systemctl", "show", SERVICE, "-p", "LoadState", "--value"], text=True).strip()
            if state != "not-found":
                raise RuntimeError("Uninstalled service definition remained loaded")
            cases.append("idle_removal_preserves_unknown_file" if number == 0 else "repeated_removal_is_idempotent")
        reinstalled = run("install-after-removal", installer)
        if reinstalled.returncode:
            raise RuntimeError("Reinstallation after explicit removal failed")
        after = preflight()
        state = after.get("result", {})
        if (after.get("ok") is not True or state.get("available") is not True
                or state.get("leaseActive") is not False or state.get("recoveryPending") is not False):
            raise RuntimeError("Reinstalled broker did not confirm idle state after removal")
        if installed.read_bytes() != original or sentinel.read_text() != token:
            raise RuntimeError("Reinstallation changed source bytes or the preserved sentinel")
        cases.append("reinstall_after_removal_confirms_idle")
        return dict(result="passed", cases=cases, after=after, afterPid=pid())
    finally:
        if sentinel.exists() and sentinel.read_text() == token:
            sentinel.unlink()


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
        result["removal"] = verify_removal(installer, output, args.owned_guest_token)
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
