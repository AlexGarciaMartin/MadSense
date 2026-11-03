"""Owned-VM consumer lifecycle. Never installed with the production helper.

run_suite is called while the core fixture and its attributed virtual USB device
are alive. Device grants, accounts, processes and files belong to this invocation.
"""
import binascii
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import struct
import subprocess
import sys
import tempfile
import time
import uuid


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def base_acl(text):
    """Accept base ACLs only, so adding a mask cannot activate latent grants."""
    rows = [line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")]
    values = {}
    for row in rows:
        fields = row.split(":")
        require(len(fields) == 3 and fields[0] in ("user", "group", "other") and not fields[1],
                "Consumer grants require a base ACL with no named/default/mask entries")
        permission = fields[2]
        require(len(permission) == 3 and all(c in (allowed, "-") for c, allowed in zip(permission, "rwx")),
                "Malformed base ACL permissions")
        require(fields[0] not in values, "Duplicate ACL entry")
        values[fields[0]] = permission
    require(set(values) == {"user", "group", "other"}, "Incomplete base ACL")
    return "".join(f"{name}::{values[name]}\n" for name in ("user", "group", "other"))


def bt_state(data):
    require(len(data) == {0x31: 78, 0x32: 142, 0x35: 334, 0x36: 398}.get(data[0] if data else -1),
            "Unexpected synthetic Bluetooth output framing")
    require(struct.unpack_from("<I", data, len(data) - 4)[0] == binascii.crc32(b"\xa2" + data[:-4]),
            "Synthetic Bluetooth output CRC mismatch")
    if data[0] == 0x31:
        return data[3:50]
    if data[0] == 0x36 and data[11:13] == b"\x90\x3f":
        return data[13:60]
    return None


def applies_rumble(payload):
    # VirtualDualSenseController.ApplyRumbleModeSelector translates the selected
    # legacy/V2 encoding. Unselected motor bytes are ignored by the physical pad.
    return bool(payload[0] & 2 and (payload[0] & 1 or payload[38] & 4))


def output_window(rows, start, end, left=0x12, right=0x56, rgb=True):
    """Verify packets in a single process window, never a previous consumer's marker."""
    matched, zero, led = None, None, None
    selected = []
    for row in rows:
        if row.get("kind") != "output" or not start <= row.get("monotonicNs", -1) <= end:
            continue
        data = bytes.fromhex(row["hex"])
        payload = bt_state(data)
        selected.append(row)
        if payload is None:
            continue
        if applies_rumble(payload) and payload[3] == left and payload[2] == right:
            matched = row["monotonicNs"]
            zero = None
        elif applies_rumble(payload) and matched is not None and payload[2:4] == b"\0\0":
            zero = row["monotonicNs"]
        if payload[1] & 4 and payload[44:47] == bytes.fromhex("123456"):
            led = row["monotonicNs"]
    require(matched is not None, "No nonzero rumble marker reached the synthetic physical writer")
    require(zero is not None, "No later zero rumble state reached the synthetic physical writer")
    require(not rgb or led is not None, "No RGB marker reached the synthetic physical writer")
    return dict(outputDeliveryVerified=True, rumbleMarkerMonotonicNs=matched,
                zeroStateMonotonicNs=zero, rgbMarkerMonotonicNs=led, observedPackets=len(selected),
                physicalActuationVerified=False)


def expected_motors(ready, observer):
    if observer.get("path") == ready.get("hidrawPath") and observer.get("sdlMajor") in (2, 3):
        firmware = bytes.fromhex(ready["features"]["20"])
        require(len(firmware) >= 46, "Firmware feature required for SDL rumble interpretation")
        version = int.from_bytes(firmware[44:46], "little")
        # SDL2 2.30.0 uses the version comparison directly; SDL3 3.2.28
        # assumes current firmware when the reported version is zero.
        if version < 0x0224 and (version or observer["sdlMajor"] == 2):
            return 0x09, 0x2b
    return 0x12, 0x56


def read_rows(path):
    require(path.stat().st_size <= 32 * 1024 * 1024, "Fixture evidence exceeded 32 MiB")
    return [json.loads(line) for line in path.read_text().split("\n")[:-1] if line]


def bounded_bytes(path, limit=4 * 1024 * 1024):
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    require(len(data) <= limit, f"{path.name} exceeded its evidence limit")
    return data


def process_identity(pid):
    try:
        directory = Path("/proc", str(pid))
        fields = dict(line.split(":", 1) for line in (directory / "status").read_text().splitlines() if ":" in line)
        ids = tuple(int(value) for value in fields["Uid"].split())
        start = (directory / "stat").read_text().rsplit(")", 1)[1].split()[19]
        return ids, start
    except (OSError, KeyError, IndexError, ValueError):
        return None


class ConsumerSession:
    def __init__(self, ready_path, evidence, physical_parent):
        self.ready_path = Path(ready_path).resolve()
        self.output = Path(evidence).resolve()
        self.physical_parent = Path(physical_parent).resolve()
        self.ready = json.loads(self.ready_path.read_text())
        self.root = None
        self.user = "madsense-cons-" + uuid.uuid4().hex[:12]
        self.uid = self.gid = None
        self.closed = False
        self.grants, self.processes, self.logs = [], [], []
        self.result = dict(schema=1, result="failed", consumers=[], cases=[], cleanup=[], grants=self.grants,
                           physicalDevices=False, gamesValidated=False, physicalLatencyValidated=False,
                           protonValidated=False)

    def save(self):
        (self.output / "result.json").write_text(json.dumps(self.result, indent=2) + "\n")

    def root_command(self, argv, input=None):
        return subprocess.run([str(x) for x in argv], input=input, capture_output=True, text=True,
                              timeout=20, check=True)

    def __enter__(self):
        require(sys.platform == "linux" and os.geteuid() == 0, "Owned Linux guest root orchestrator required")
        # Import only in the Linux runtime so pure evidence tests remain portable.
        from run_consumers import validate_ready
        import pwd
        self.validate = lambda: validate_ready(self.ready)
        token, parent, vendor, product, nodes = self.validate()
        self.parent = parent
        self.parent_identity = (parent.stat().st_dev, parent.stat().st_ino)
        require(str(self.physical_parent).startswith("/sys/devices/virtual/misc/uhid/")
                and self.physical_parent.exists() and not self.physical_parent.is_relative_to(parent),
                "Caller must identify its separate synthetic UHID fixture")
        self.output.mkdir(parents=True, exist_ok=True)
        require(not any(self.output.iterdir()), "Fresh consumer evidence directory required")
        self.result.update(ready=self.ready, user=self.user, scope="owned-core-consumers")
        self.save()
        try:
            self.root = Path(tempfile.mkdtemp(prefix="madsense-consumers-", dir="/run"))
            self.root.chmod(0o755)
            home = self.root / "home"
            self.root_command(["useradd", "--no-create-home", "--user-group", "--home-dir", home,
                               "--shell", "/usr/sbin/nologin", self.user])
            account = pwd.getpwnam(self.user)
            self.uid, self.gid = account.pw_uid, account.pw_gid
            require(self.uid > 0 and self.gid > 0, "Ordinary consumer UID/GID required")
            for directory in (home, self.root / "runtime"):
                directory.mkdir(mode=0o700)
                os.chown(directory, self.uid, self.gid)
            self.env = {"PATH": "/usr/bin:/bin", "HOME": str(home), "LANG": "C.UTF-8",
                        "USER": self.user, "LOGNAME": self.user,
                        "XDG_RUNTIME_DIR": str(self.root / "runtime"),
                        "MADSENSE_CONSUMER_ISOLATED": token}
            self.card = None
            for candidate in Path("/sys/class/sound").glob("card[0-9]*"):
                if (candidate / "device").resolve().is_relative_to(parent):
                    require(self.card is None, "Multiple ALSA cards under the owned virtual USB parent")
                    self.card = int(candidate.name[4:])
            if self.card is not None:
                for candidate in Path("/sys/class/sound").glob("*"):
                    if (candidate.name.startswith((f"controlC{self.card}", f"pcmC{self.card}D"))
                            and (candidate / "device").resolve().is_relative_to(parent)):
                        node = Path("/dev/snd", candidate.name)
                        if node.exists():
                            nodes.append(node)
            for node in nodes:
                self.grant(node)
            self.audit_access(nodes)
            self.result["account"] = dict(uid=self.uid, gid=self.gid, supplementaryGroups=[])
            self.result["grantedNodes"] = [str(node) for node in nodes]
            self.save()
            return self
        except BaseException:
            self.close()
            raise

    def grant(self, node):
        value = node.lstat()
        require(stat.S_ISCHR(value.st_mode), "Only exact character device grants are allowed")
        original = base_acl(self.root_command(["getfacl", "--absolute-names", "--omit-header", "--numeric", "--physical", node]).stdout)
        record = dict(path=str(node), dev=value.st_dev, inode=value.st_ino, rdev=value.st_rdev,
                      uid=value.st_uid, gid=value.st_gid, mode=stat.S_IMODE(value.st_mode), acl=original)
        entries = dict(line.split("::") for line in original.splitlines())
        mask = "rwx" if "x" in entries["group"] else "rw-"
        record["grantedAcl"] = (f"user::{entries['user']}\nuser:{self.uid}:rw-\ngroup::{entries['group']}\n"
                                f"mask::{mask}\nother::{entries['other']}\n")
        self.grants.append(record)  # Restore even if setfacl reports a partial failure.
        self.save()
        self.root_command(["setfacl", "--no-mask", "--modify", f"u:{self.uid}:rw-,m::{mask}", node])
        observed = self.root_command(["getfacl", "--absolute-names", "--omit-header", "--numeric", "--physical", node]).stdout.strip() + "\n"
        record["observedAfterGrant"] = observed; self.save()
        require(observed == record["grantedAcl"], "Consumer grant differs from the expected narrow ACL")

    def audit_access(self, allowed):
        sensitive = sorted({str(path) for pattern in ("/dev/hidraw*", "/dev/input/event*", "/dev/snd/controlC*", "/dev/snd/pcm*")
                            for path in Path("/").glob(pattern.lstrip("/"))})
        program = "import json,os,sys; print(json.dumps({p:[os.access(p,os.R_OK),os.access(p,os.W_OK)] for p in json.load(sys.stdin)}))"
        completed = subprocess.run(["python3", "-c", program], input=json.dumps(sensitive), text=True,
                                   capture_output=True, timeout=10, user=self.uid, group=self.gid,
                                   extra_groups=[], env=self.env)
        require(completed.returncode == 0, "Consumer access audit failed")
        observed = json.loads(completed.stdout)
        permitted = {str(path) for path in allowed}
        for path, access in observed.items():
            require(access == [True, True] if path in permitted else not any(access),
                    f"Consumer has unexpected access to {path}: {access}")
        (self.output / "access-audit.json").write_text(json.dumps(observed, indent=2) + "\n")

    def check_device(self):
        self.validate()
        value = self.parent.stat()
        require((value.st_dev, value.st_ino) == self.parent_identity, "Virtual USB parent was replaced")

    def stage(self, source):
        source = Path(source).resolve()
        target = self.root / source.name
        require(not target.exists(), "Consumer executable name collision")
        shutil.copyfile(source, target); target.chmod(0o755)
        return target

    def start(self, label, argv, env=None, pass_fds=()):
        stdout = (self.output / f"{label}.stdout").open("xb")
        stderr = (self.output / f"{label}.stderr").open("xb")
        self.logs.extend((stdout, stderr))
        process = subprocess.Popen([str(x) for x in argv], stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                                   user=self.uid, group=self.gid, extra_groups=[], cwd=self.root / "home",
                                   env=self.env | (env or {}), start_new_session=True, pass_fds=pass_fds)
        self.processes.append(process)
        return process

    def command(self, label, argv, env=None, timeout=45, required=True):
        self.check_device()
        start = time.monotonic_ns()
        process = self.start(label, argv, env)
        deadline = time.monotonic() + timeout
        fault = None
        try:
            while process.poll() is None:
                require(time.monotonic() < deadline, f"{label} exceeded {timeout}s")
                for extension in ("stdout", "stderr"):
                    require((self.output / f"{label}.{extension}").stat().st_size <= 4 * 1024 * 1024,
                            f"{label} exceeded its evidence limit")
                time.sleep(.05)
        except BaseException as error:
            fault = error
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL); process.wait(timeout=2)
        ended = time.monotonic_ns()
        record = dict(label=label, exitCode=process.returncode, startedMonotonicNs=start, endedMonotonicNs=ended,
                      argv=[str(x) for x in argv])
        self.result["consumers"].append(record); self.save()
        if fault is not None:
            record["executionError"] = str(fault); self.save(); raise fault
        try:
            stdout = bounded_bytes(self.output / f"{label}.stdout")
            stderr = bounded_bytes(self.output / f"{label}.stderr")
        except RuntimeError as error:
            record["executionError"] = str(error); self.save(); raise
        require(not required or process.returncode == 0,
                f"{label} failed ({process.returncode}): {stderr.decode(errors='replace')[-400:]}")
        self.check_device()
        return record, stdout

    def owned_processes(self):
        if self.uid is None:
            return []
        found = []
        for path in Path("/proc").iterdir():
            if path.name.isdigit():
                identity = process_identity(int(path.name))
                if identity and all(uid == self.uid for uid in identity[0]):
                    found.append((int(path.name), identity))
        return found

    def close(self):
        if self.closed:
            return self.result.get("cleanupErrors", [])
        errors = []
        # This UID was created solely for this session; Wine daemons may have
        # left the original process group. Recheck UID and start time per signal.
        for sig, grace in ((signal.SIGTERM, 2), (signal.SIGKILL, 2)):
            for pid, identity in self.owned_processes():
                if process_identity(pid) == identity:
                    try:
                        os.kill(pid, sig)
                    except ProcessLookupError:
                        pass
            deadline = time.monotonic() + grace
            while self.owned_processes() and time.monotonic() < deadline:
                for process in self.processes:
                    process.poll()
                time.sleep(.05)
        if self.owned_processes():
            errors.append("Consumer UID still has live processes; account and private files retained")
        for process in self.processes:
            process.poll()
        for stream in self.logs:
            stream.close()
        for record in reversed(self.grants):
            try:
                node = Path(record["path"])
                value = node.lstat()
                require((value.st_dev, value.st_ino, value.st_rdev, value.st_uid, value.st_gid) ==
                        tuple(record[key] for key in ("dev", "inode", "rdev", "uid", "gid")),
                        "Granted node identity changed; no ACL restoration to a replacement")
                observed = self.root_command(["getfacl", "--absolute-names", "--omit-header", "--numeric", "--physical", node]).stdout.strip() + "\n"
                record["observedBeforeRestore"] = observed; self.save()
                require(observed in (record["acl"], record["grantedAcl"]),
                        "Consumer node ACL changed outside this grant; no overwrite")
                self.root_command(["setfacl", "--set-file=-", node], input=record["acl"])
                restored = self.root_command(["getfacl", "--absolute-names", "--omit-header", "--numeric", "--physical", node]).stdout
                record["observedAfterRestore"] = restored; self.save()
                require(base_acl(restored) == record["acl"] and stat.S_IMODE(node.stat().st_mode) == record["mode"],
                        "Consumer ACL restoration did not match its snapshot")
                self.result["cleanup"].append(dict(path=str(node), aclRestored=True))
            except BaseException as error:
                errors.append(str(error))
        if self.uid is not None and not self.owned_processes() and not errors:
            try:
                self.root_command(["userdel", self.user])
                # userdel normally removes its empty private group; remove it
                # explicitly only if that same newly created name/gid remains.
                import grp
                try:
                    group = grp.getgrnam(self.user)
                except KeyError:
                    group = None
                if group is not None:
                    require(group.gr_gid == self.gid and not group.gr_mem, "Consumer private group changed")
                    self.root_command(["groupdel", self.user])
            except BaseException as error:
                errors.append(str(error))
        if self.root is not None and not self.owned_processes() and not errors:
            require(self.root.parent == Path("/run") and self.root.name.startswith("madsense-consumers-"),
                    "Unexpected consumer private directory")
            shutil.rmtree(self.root)
        if errors:
            self.result["cleanupErrors"] = errors
            self.result["result"] = "failed"
            self.result["accountProcessesAndFilesRemoved"] = False
            self.result["privateStateRetainedAfterCleanupFailure"] = True
        else:
            self.result["accountProcessesAndFilesRemoved"] = True
        self.closed = True
        self.save()
        return errors

    def __exit__(self, kind, value, traceback):
        if value is not None:
            self.result["error"] = f"{kind.__name__}: {value}"
            self.result["result"] = "failed"
        errors = self.close()
        if errors and value is None:
            raise RuntimeError("Consumer cleanup failed: " + "; ".join(errors))


def wait_delivery(session, record, rgb, left=0x12, right=0x56):
    path = Path(session.ready["uhidEventsPath"])
    deadline = time.monotonic() + 2
    last = None
    while time.monotonic() < deadline:
        try:
            delivery = output_window(read_rows(path), record["startedMonotonicNs"], time.monotonic_ns(), left, right, rgb)
            record["delivery"] = delivery
            session.save()
            return
        except RuntimeError as error:
            last = error
            time.sleep(.05)
    raise last or RuntimeError("Missing output delivery evidence")


def clear_sony_state(session, label):
    if session.ready["vendorId"] != 0x054c:
        return
    report = bytearray(48); report[0] = 2; report[1] = 3; report[2] = 4
    code = ("import os,sys; p=sys.argv[1]; data=bytes.fromhex(sys.argv[2]); "
            "fd=os.open(p,os.O_RDWR|os.O_NONBLOCK|os.O_NOFOLLOW); "
            "n=os.write(fd,data); os.close(fd); assert n==len(data)")
    record, _ = session.command(label, ["python3", "-c", code, session.ready["hidrawPath"], report.hex()])
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        for row in read_rows(Path(session.ready["uhidEventsPath"])):
            if row.get("kind") == "output" and row["monotonicNs"] >= record["startedMonotonicNs"]:
                payload = bt_state(bytes.fromhex(row["hex"]))
                if (payload is not None and applies_rumble(payload) and payload[2:4] == b"\0\0"
                        and payload[1] & 4 and payload[44:47] == bytes(3)):
                    record["baselineClearObserved"] = True; session.save(); return
        time.sleep(.05)
    raise RuntimeError("Synthetic physical state did not clear before the next consumer")


def run_suite(ready_path, binaries, wine_exe, fixture_state_callback, evidence, physical_parent):
    """Fixture callback accepts 'south' or 'neutral'; it must not replace the device."""
    with ConsumerSession(ready_path, evidence, physical_parent) as session:
        session.result["wineRequested"] = wine_exe is not None
        programs = ["consumer_sdl2", "consumer_sdl3"]
        if session.ready["vendorId"] == 0x045e:
            programs.insert(0, "ff_probe")
        staged = {name: session.stage(Path(binaries) / name) for name in programs}
        failures = []
        try:
            for state in ("south", "neutral"):
                fixture_state_callback(state)
                time.sleep(.1)
                for name, executable in staged.items():
                    if name == "ff_probe" and state == "south":
                        continue
                    label = f"{name}-{state}"
                    try:
                        clear_sony_state(session, label + "-clear")
                        ready = session.ready
                        argv = [executable, ready["ownedGuestToken"], ready["usbParent"], ready["evdevPath"]]
                        if name != "ff_probe":
                            argv += [ready.get("hidrawPath") or "-", f"{ready['vendorId']:04x}", f"{ready['productId']:04x}", state]
                        record, stdout = session.command(label, argv, required=False)
                        observed = json.loads(stdout)
                        record["evidence"] = observed
                        record["binarySha256"] = hashlib.sha256(executable.read_bytes()).hexdigest()
                        require(record["exitCode"] == 0 and observed.get("result") == "passed", label + " API assertions failed")
                        left, right = expected_motors(ready, observed)
                        wait_delivery(session, record, rgb=bool(observed.get("capabilities", {}).get("rgbLed")), left=left, right=right)
                        session.result["cases"].append(dict(name=label, passed=True))
                    except Exception as error:
                        session.check_device()
                        failures.append(f"{label}: {error}")
                        session.result["cases"].append(dict(name=label, passed=False, error=str(error)))
                    session.save()
            if wine_exe is not None:
                from run_wine_consumer import run_wine_suite
                try:
                    run_wine_suite(session, wine_exe, fixture_state_callback)
                except Exception as error:
                    session.check_device()
                    failures.append(f"wine: {error}")
            require(not failures, "; ".join(failures))
            session.result["result"] = "passed"
        finally:
            fixture_state_callback("neutral")
        session.save()
    return session.result
