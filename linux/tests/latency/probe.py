"""Descriptor-scoped C probe for an already Ready, isolated application lease."""
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import subprocess
import sys
import time

from analyze import summarize

LINUX = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(LINUX / "research"))
sys.path.insert(0, str(LINUX / "platform/integration"))
from ci_environment import validate_environment
from production_fixture import fixture_identity_matches, input_report


def require(value, message):
    if not value:
        raise RuntimeError(message)


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def metrics(status):
    diagnostic = status["diagnostic"]
    start = diagnostic.find('{\n  "scope":')
    require(start >= 0, "Application diagnostics omit proxy counters")
    value, _ = json.JSONDecoder().raw_decode(diagnostic[start:])
    return value


def process_sample(pid):
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        status = Path(f"/proc/{pid}/status").read_text().splitlines()
        return dict(pid=pid, startTicks=int(fields[19]), userTicks=int(fields[11]), systemTicks=int(fields[12]),
                    rssPages=int(fields[21]), pageBytes=os.sysconf("SC_PAGE_SIZE"), clockTicksPerSecond=os.sysconf("SC_CLK_TCK"),
                    monotonicNs=time.monotonic_ns(), threads=int(next(x.split()[1] for x in status if x.startswith("Threads:"))))
    except (FileNotFoundError, ProcessLookupError):
        return dict(pid=pid, gone=True, monotonicNs=time.monotonic_ns())


def validated_target(fixture, usb, hidraw):
    require(fixture.parent.resolve(strict=True).parent == Path("/sys/devices/virtual/misc/uhid"), "Non-UHID physical parent")
    require(fixture_identity_matches((fixture.parent / "uevent").read_text(), fixture.label), "Changed UHID identity")
    input_info = os.fstat(fixture.fd)
    require(stat.S_ISCHR(input_info.st_mode) and
            (Path("/sys/class/misc/uhid/dev").read_text().strip() == f"{os.major(input_info.st_rdev)}:{os.minor(input_info.st_rdev)}"),
            "Fixture descriptor is not UHID")
    usb = usb.resolve(strict=True)
    require(str(usb).startswith("/sys/devices/platform/vhci_hcd."), "Non-VHCI virtual parent")
    fd = os.open("/run/madsense/lease.json", os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o600
                and info.st_size <= 131072, "Unsafe lease journal")
        with os.fdopen(os.dup(fd)) as stream:
            journal = json.load(stream)
    finally:
        os.close(fd)
    require(journal.get("isolationVerified") is True and journal.get("physicalPath") == str(fixture.hidraw), "Wrong or unisolated lease")
    for key, path in (("hidParent", fixture.parent), ("importDevice", usb)):
        info = path.stat()
        require(journal.get(key) == dict(path=str(path), dev=info.st_dev, inode=info.st_ino), f"Changed {key} identity")
    require(hidraw.parent == Path("/dev") and hidraw.name.startswith("hidraw") and hidraw.name[6:].isdigit(), "Unexpected virtual node")
    sysfs = Path("/sys/class/hidraw", hidraw.name)
    require((sysfs / "device").resolve(strict=True).is_relative_to(usb), "Virtual hidraw is outside the owned import")
    target = os.open(hidraw, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        info = os.fstat(target)
        current = hidraw.lstat()
        require(stat.S_ISCHR(info.st_mode) and (current.st_dev, current.st_ino, current.st_rdev) ==
                (info.st_dev, info.st_ino, info.st_rdev) and
                (sysfs / "dev").read_text().strip() == f"{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}", "Replaced virtual node")
        return target, journal
    except BaseException:
        os.close(target)
        raise


def build(directory):
    source = Path(__file__).with_name("latency_probe.c")
    executable = directory / "latency-probe"
    command = ["cc", "-std=c11", "-O2", "-pthread", "-Wall", "-Wextra", "-Werror", str(source), "-o", str(executable)]
    result = subprocess.run(command, capture_output=True, timeout=60)
    (directory / "compile.stdout").write_bytes(result.stdout)
    (directory / "compile.stderr").write_bytes(result.stderr)
    require(result.returncode == 0, "C timing probe did not compile")
    save(directory / "build.json", dict(command=command, sourceSha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        binarySha256=hashlib.sha256(executable.read_bytes()).hexdigest(),
        compiler=subprocess.check_output(["cc", "--version"], text=True, timeout=5)))
    return executable


def run(fixture, usb, hidraw, directory, executable, actor_pid, broker_pid, token, rate, *, nonce=None):
    require(os.geteuid() == 0 and validate_environment(token)["kind"] == "owned-qemu-guest", "Owned root guest required")
    require(rate in (100, 1000), "Only initial 100/1000 Hz smoke rates are supported")
    nonce = 0x8000 + secrets.randbelow(0x8000) if nonce is None else nonce
    require(type(nonce) is int and 0x8000 <= nonce <= 0xffff, "Explicit valid per-case nonce required")
    result = dict(result="invalid", cleanupConfirmed=False)
    enabled, child, target = fixture.input_enabled, None, -1
    stdout, stderr = b"", b""
    try:
        target, journal = validated_target(fixture, usb, hidraw)
        save(directory / "lease-at-measurement.json", journal)
        template = directory / "input-template.bin"
        template.write_bytes(input_report())
        samples = directory / "samples.csv"
        fixture.input_enabled = False
        # The fixture thread keeps handling features/output, but the timed C
        # producer owns injection. A last background report is unmatched by nonce.
        with template.open("rb") as template_file, samples.open("xb") as samples_file:
            inherited = (fixture.fd, target, samples_file.fileno(), template_file.fileno())
            argv = [str(executable), "--uhid-fd", str(inherited[0]), "--virtual-fd", str(inherited[1]),
                    "--samples-fd", str(inherited[2]), "--template-fd", str(inherited[3]),
                    "--rate", str(rate), "--seconds", "10", "--nonce", str(nonce)]
            before = {name: process_sample(pid) for name, pid in (("application", actor_pid), ("broker", broker_pid), ("coordinator", os.getpid()))}
            child = subprocess.Popen(argv, pass_fds=inherited, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            stdout, stderr = child.communicate(timeout=18)
            after = {name: process_sample(value["pid"]) for name, value in before.items()}
        (directory / "probe.stdout").write_bytes(stdout)
        (directory / "probe.stderr").write_bytes(stderr)
        save(directory / "process-samples.json", dict(before=before, after=after,
             scope="whole-process CPU ticks including all live threads; coordinator includes UHID fixture thread"))
        require(child.returncode == 0, f"Timing probe exit {child.returncode}")
        raw = json.loads(stdout)
        require(raw.get("nonce") == nonce, "C producer recorded a different nonce")
        save(directory / "probe.json", raw)
        with samples.open(newline="") as stream:
            result = summarize(stream, raw)
        result["cpu"] = {}
        for name, first in before.items():
            last = after[name]
            if not first.get("gone") and not last.get("gone") and first["startTicks"] == last["startTicks"]:
                result["cpu"][name] = dict(userSeconds=(last["userTicks"] - first["userTicks"]) / first["clockTicksPerSecond"],
                    systemSeconds=(last["systemTicks"] - first["systemTicks"]) / first["clockTicksPerSecond"],
                    elapsedSeconds=(last["monotonicNs"] - first["monotonicNs"]) / 1e9)
            else:
                result["cpu"][name] = dict(unavailable="Process exited or changed identity; see raw samples")
        return result
    finally:
        if child is not None and child.poll() is None:
            child.kill(); stdout, stderr = child.communicate(timeout=5)
        if child is not None:
            (directory / "probe.stdout").write_bytes(stdout)
            (directory / "probe.stderr").write_bytes(stderr)
        if target >= 0:
            os.close(target)
        fixture.input_enabled = enabled
        save(directory / "measurement.json", result)
