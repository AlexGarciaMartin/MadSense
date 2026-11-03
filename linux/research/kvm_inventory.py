#!/usr/bin/env python3
"""Read-only host inventory; does not change permissions, load modules or create a VM.

Default operation only inspects metadata. --probe-api additionally opens an
already registered KVM device and queries KVM_GET_API_VERSION, then closes it.
API availability is deliberately not reported as proof that a VM can run.
"""
import argparse
import datetime
import errno
import json
import os
from pathlib import Path
import platform
import stat
import subprocess
import sys


def failure(error):
    return {"error": str(error), "errno": getattr(error, "errno", None),
            "errnoName": errno.errorcode.get(getattr(error, "errno", None))}


def read(path):
    try:
        return {"value": Path(path).read_text().strip()}
    except OSError as error:
        return failure(error)


def command(argv):
    try:
        result = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=5)
        return {"argv": argv, "exitCode": result.returncode,
                "stdout": result.stdout[:32768], "stderr": result.stderr[:4096]}
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"argv": argv, **failure(error)}


def identity(info):
    return {"device": info.st_dev, "inode": info.st_ino, "rdev": info.st_rdev,
            "uid": info.st_uid, "gid": info.st_gid, "mode": oct(stat.S_IMODE(info.st_mode)),
            "characterDevice": stat.S_ISCHR(info.st_mode), "symlink": stat.S_ISLNK(info.st_mode)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe-api", action="store_true", help="Query the already registered KVM API; never create a VM")
    args = parser.parse_args()
    report = {"schema": 1, "capturedUtc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "platform": platform.platform(), "machine": platform.machine(),
              "permissionsChanged": False, "modulesLoaded": False, "virtualMachineCreated": False,
              "runner": {name: os.environ.get(name) for name in (
                  "RUNNER_ENVIRONMENT", "RUNNER_OS", "RUNNER_ARCH", "ImageOS", "ImageVersion",
                  "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_SHA")}}
    if sys.platform != "linux":
        report["status"] = "Linux inventory unavailable on this host"
        print(json.dumps(report, indent=2))
        return
    report["identity"] = {"uid": os.getuid(), "effectiveUid": os.geteuid(), "gid": os.getgid(),
                          "effectiveGid": os.getegid(), "supplementaryGroups": os.getgroups()}
    report["virtualization"] = command(["systemd-detect-virt"])
    report["qemuVersion"] = command(["qemu-system-x86_64", "--version"])
    report["compiledAccelerators"] = command(["qemu-system-x86_64", "-accel", "help"])
    report["processPolicy"] = {line.split(":", 1)[0]: line.split(":", 1)[1].strip()
                               for line in Path("/proc/self/status").read_text().splitlines()
                               if line.startswith(("CapEff:", "NoNewPrivs:", "Seccomp:", "Seccomp_filters:"))}
    report["securityContext"] = read("/proc/self/attr/current")
    flags = set()
    for line in Path("/proc/cpuinfo").read_text().splitlines():
        if line.startswith(("flags", "Features")):
            flags.update(line.partition(":")[2].split())
    report["cpuVirtualizationFlags"] = sorted(flags.intersection({"vmx", "svm", "hypervisor"}))
    report["modules"] = {name: Path("/sys/module", name).exists() for name in ("kvm", "kvm_intel", "kvm_amd")}
    # These parameters concern exposing nested virtualization to this host's
    # guests. They alone do not establish whether this host can run a KVM guest.
    report["nestedParameters"] = {name: read(f"/sys/module/{name}/parameters/nested") for name in ("kvm_intel", "kvm_amd")}
    report["registeredKvmDevice"] = read("/sys/class/misc/kvm/dev")
    misc = read("/proc/misc")
    report["registeredMiscKvm"] = [line.strip() for line in misc.get("value", "").splitlines()
                                    if line.split()[-1:] == ["kvm"]]
    report["acl"] = command(["getfacl", "--absolute-names", "--numeric", "--all-effective", "--physical", "/dev/kvm"])
    info = None
    try:
        info = os.lstat("/dev/kvm")
        report["deviceNode"] = identity(info)
        report["deviceNode"]["majorMinor"] = f"{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}"
    except OSError as error:
        report["deviceNode"] = failure(error)
    report["accessReadWrite"] = os.access("/dev/kvm", os.R_OK | os.W_OK)
    report["apiProbe"] = {"status": "not requested", "vmUsable": "unproven"}
    if args.probe_api:
        # Do not open a stale/static character node that might request module
        # autoload. Only query an existing root-owned registered device.
        registered = (info is not None and stat.S_ISCHR(info.st_mode) and info.st_uid == 0
                      and report["registeredKvmDevice"].get("value") == report["deviceNode"]["majorMinor"]
                      and bool(report["registeredMiscKvm"]))
        if not registered:
            report["apiProbe"] = {"status": "skipped: no matching registered root-owned KVM node", "vmUsable": "unproven"}
        else:
            fd = -1
            try:
                import fcntl
                fd = os.open("/dev/kvm", os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW)
                opened = os.fstat(fd)
                if (opened.st_dev, opened.st_ino, opened.st_rdev) != (info.st_dev, info.st_ino, info.st_rdev):
                    raise RuntimeError("KVM node identity changed while opening")
                # _IO(KVMIO, 0x00), documented at docs.kernel.org/virt/kvm/api.html.
                version = fcntl.ioctl(fd, 0xAE00, 0)
                report["apiProbe"] = {"status": "queried", "apiVersion": version, "vmUsable": "unproven"}
            except (OSError, RuntimeError) as error:
                report["apiProbe"] = {"status": "failed", **failure(error), "vmUsable": "unproven"}
            finally:
                if fd >= 0:
                    os.close(fd)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
