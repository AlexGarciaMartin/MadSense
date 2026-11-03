#!/usr/bin/env python3
"""Small hosted-runner KVM grant/QMP/stop/restore experiment, with no guest image.

Run as the ordinary hosted runner. Creates only an owned empty QEMU machine;
no physical device passthrough, networking, disks, modules or udev mutations.
"""
import argparse
import json
import os
from pathlib import Path
import stat
import subprocess
import time

from kvm_acceleration import AcceleratedQemu, DEVICE, acl_text, hosted, node_identity, registered_node, require


def snapshot():
    captured = dict(monotonicNs=time.monotonic_ns())
    fd = -1
    try:
        info = registered_node()
        fd = os.open(DEVICE, os.O_PATH | os.O_CLOEXEC | os.O_NOFOLLOW)
        opened = os.fstat(fd)
        require(node_identity(opened) == node_identity(info), "KVM snapshot identity changed")
        captured.update(identity=node_identity(opened), mode=stat.S_IMODE(opened.st_mode), acl=acl_text(fd))
        answer = subprocess.run(["udevadm", "info", "--query=property", "--name=/dev/kvm"],
                                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=5)
        captured["udev"] = dict(exitCode=answer.returncode, stdout=answer.stdout[:8192], stderr=answer.stderr[-2000:])
    except Exception as error:
        captured["error"] = repr(error)
    finally:
        if fd >= 0:
            os.close(fd)
    return captured


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--seconds", type=int, default=10, choices=range(1, 31), metavar="1..30")
    args = parser.parse_args()
    hosted()
    require(os.geteuid() != 0, "Run the smoke as the ordinary hosted runner")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    require(not any(output.iterdir()), "Fresh KVM smoke output directory required")
    result = dict(schema=1, result="failed", scope="hosted-empty-owned-QEMU",
                  physicalDevicePassthrough=False, hostKernelModified=False, snapshots={})
    owner = AcceleratedQemu(output, result)
    try:
        result["snapshots"]["before"] = snapshot()
        mode = owner.start(lambda accelerator: ["qemu-system-x86_64", "-machine", f"q35,accel={accelerator}",
            "-cpu", "host" if accelerator == "kvm" else "max", "-m", "128", "-smp", "1", "-nodefaults",
            "-display", "none", "-monitor", "none", "-serial", "none", "-no-reboot"])
        result["snapshots"]["afterQmp"] = snapshot()
        for second in range(args.seconds):
            time.sleep(1)
            require(owner.process.poll() is None, "Owned QEMU exited during the smoke")
            result["snapshots"][f"running-{second + 1}"] = snapshot()
        result["finalQmp"] = owner.qmp.execute("query-kvm")
        result["result"] = "passed" if mode == "kvm" else "not-applicable"
    except Exception as error:
        result["error"] = repr(error)
        raise
    finally:
        try:
            owner.close()  # Must reap its exact QEMU before any restoration.
        finally:
            result["snapshots"]["afterCleanup"] = snapshot()
            (output / "result.json").write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
