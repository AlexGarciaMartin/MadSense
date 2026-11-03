"""A fresh ordinary production AppProbe and real-playstation UHID fixture."""
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import uuid

LINUX = Path(__file__).resolve().parents[2]
for relative in ("research", "platform/tests", "platform/integration"):
    sys.path.insert(0, str(LINUX / relative))
from consumer_session import require
import actor_files


class AppActor:
    def __init__(self, token, probe, output):
        self.token, self.probe, self.output = token, probe, Path(output)
        self.user = "madsense-hot-"+uuid.uuid4().hex[:12]
        self.private = Path("/opt", self.user)
        self.actor = self.private / "actor"
        self.process = self.fixture = self.log = self.baseline = None
        self.files, self.commands, self.account_created = [], 0, False
        self.cleanup = dict(confirmed=False)

    def command(self, label, argv):
        completed = subprocess.run([str(x) for x in argv], capture_output=True, timeout=30)
        (self.output / (label+".stdout")).write_bytes(completed.stdout)
        (self.output / (label+".stderr")).write_bytes(completed.stderr)
        require(completed.returncode == 0, f"{label} failed: {completed.stderr[-400:]!r}")
        return completed

    def create(self, path, content):
        with path.open("x") as stream: stream.write(content)
        self.files.append((path, content))

    def snapshot(self):
        rows = []
        for subsystem, pattern, directory in (("hidraw", "hidraw*", "/dev"), ("input", "event*", "/dev/input")):
            for entry in self.owned_nodes(self.fixture.parent, subsystem, pattern):
                path = Path(directory, entry.name); info = path.lstat()
                require(stat.S_ISCHR(info.st_mode), "Fixture changed node type")
                rows.append((str(path), info.st_dev, info.st_ino, info.st_rdev, info.st_uid, info.st_gid,
                    stat.S_IMODE(info.st_mode), subprocess.check_output(["getfacl", "--absolute-names",
                    "--numeric", "--omit-header", path], text=True)))
        return sorted(rows)

    def idle(self):
        return (not Path("/run/madsense/lease.json").exists()
            and not Path("/run/udev/rules.d/72-madsense-lease.rules").exists()
            and all(row["localBusId"] == "0-0" for row in self.ports().values())
            and (self.baseline is None or self.snapshot() == self.baseline))

    def status(self):
        require(self.process.poll() is None and self.fixture.error is None, "Actor or fixture exited")
        value = actor_files.call(self.actor, self.uid, self.gid, "read", dict(name="status.json"))
        if value is None: return None
        return value if value["lastQueuedCommand"] >= self.commands else None

    def state(self, predicate, seconds=45):
        return self.wait_for(lambda: value if (value := self.status()) and predicate(value["state"]) else None, seconds)

    def action(self, operation):
        self.commands += 1
        actor_files.call(self.actor, self.uid, self.gid, "command", dict(sequence=self.commands, operation=operation))

    def resume(self):
        self.action("resume"); self.fixture.input_enabled = True
        status = self.state(lambda state: state["ready"])
        imports = [row for row in self.ports().values() if row["localBusId"] != "0-0"]
        require(len(imports) == 1, "Expected exactly one owned import")
        parent = Path("/sys/bus/usb/devices", imports[0]["localBusId"]).resolve(strict=True)
        require(parent.is_relative_to(Path("/sys/devices/platform")) and "vhci_hcd" in str(parent), "Expected VHCI parent")
        require(int((parent / "idVendor").read_text(), 16) == 0x054c
            and int((parent / "idProduct").read_text(), 16) == 0x0ce6, "Expected real app's DualSense persona")
        lease = json.loads(Path("/run/madsense/lease.json").read_text())
        require(lease["physicalPath"] == str(self.fixture.hidraw), "Lease belongs to another physical fixture")
        imported = lease.get("importDevice", {})
        require(imported.get("path") == str(parent) and lease.get("expectedImportPath") == str(parent)
            and (imported.get("dev"), imported.get("inode")) == (parent.stat().st_dev, parent.stat().st_ino),
            "Virtual parent is not the exact journal-owned import")
        raw = self.wait_for(lambda: self.owned_nodes(parent, "hidraw", "hidraw*"), 10)
        require(len(raw) == 1, "Virtual HID identity is ambiguous")
        fields = dict(line.split("=", 1) for line in (raw[0] / "device/uevent").read_text().splitlines() if "=" in line)
        mac = fields.get("HID_UNIQ", "").lower()
        require(mac.startswith("02:d5:") and mac != "02:fa:11:22:33:44", "Virtual HID MAC is not owned")
        self.last_identity = dict(usbParent=str(parent), hidrawSysfs=str(raw[0]), virtualMac=mac,
            physicalPath=lease["physicalPath"], usbSerial=(parent / "serial").read_text().strip() if (parent / "serial").exists() else None)
        return parent, status

    def pause(self):
        self.action("pause")
        status = self.state(lambda state: state["paused"] and not state["releasing"])
        self.wait_for(self.idle, 25)
        return status

    def __enter__(self):
        import pwd
        from ci_environment import validate_environment
        require(validate_environment(self.token)["kind"] == "owned-qemu-guest", "Owned QEMU required")
        from kernel_probe import owned_nodes, ports, wait_for
        from production_fixture import SyntheticDualSense
        from actor_staging import stage_probe, verify_actor_execution
        self.owned_nodes, self.ports, self.wait_for = owned_nodes, ports, wait_for
        self.output.mkdir()
        try:
            require(self.idle() and not list(Path("/sys/class/sound").glob("card[0-9]*")), "Guest must be idle without sound hardware")
            require(not list(Path("/sys/bus/hid/devices").glob("000[35]:054C:*")), "Unexpected Sony device")
            try: pwd.getpwnam(self.user)
            except KeyError: pass
            else: raise RuntimeError("Refusing existing actor account")
            self.command("create-actor", ["useradd", "--system", "--user-group", "--no-create-home", "--shell", "/usr/sbin/nologin", self.user])
            self.account_created = True; account = pwd.getpwnam(self.user)
            self.uid, self.gid = account.pw_uid, account.pw_gid
            self.private.mkdir(mode=0o755); self.private.chmod(0o755)
            exe = stage_probe(self.probe, self.private / "probe", "MadSense.Linux.AppProbe")
            verify_actor_execution(self.private, exe, account, self.output / "actor-staging.json")
            self.actor.mkdir(mode=0o700); os.chown(self.actor, account.pw_uid, account.pw_gid)
            self.create(Path("/etc/polkit-1/rules.d", "45-"+self.user+".rules"),
                'polkit.addRule(function(action, subject) { if (action.id === "org.madsense.bridge.acquire" '
                f'&& subject.user === "{self.user}") return polkit.Result.YES; }});\n')
            self.command("restart-polkit", ["systemctl", "restart", "polkit.service"])
            for module in ("uhid", "hid_playstation"):
                self.command("load-"+module, ["modprobe", module])
            directory = self.output / "fixture"; directory.mkdir()
            self.fixture = SyntheticDualSense("madsense-app-"+uuid.uuid4().hex[:12], directory)
            self.create(Path("/run/udev/rules.d", "71-"+self.user+".rules"),
                f'SUBSYSTEM=="hidraw|input", KERNELS=="{self.fixture.parent.name}", OWNER="root", '
                f'GROUP="{self.user}", MODE="0660", TAG-="uaccess", TAG-="seat"\n')
            self.command("reload-rules", ["udevadm", "control", "--reload-rules"])
            self.command("trigger-fixture", ["udevadm", "trigger", "--action=change", "--parent-match="+str(self.fixture.parent)])
            self.command("settle-fixture", ["udevadm", "settle", "--timeout=5"])
            self.baseline = self.snapshot()
            (self.output / "fixture-before.json").write_text(json.dumps(self.baseline, indent=2))
            self.log = (self.output / "actor.log").open("xb")
            self.process = subprocess.Popen([exe, "--owned-guest-token", self.token, "--device", str(self.fixture.hidraw),
                "--fixture-label", self.fixture.label, "--output", str(self.actor)], stdout=self.log, stderr=self.log,
                cwd=self.private, user=account.pw_uid, group=account.pw_gid, extra_groups=[],
                env={"PATH":"/usr/bin:/bin", "HOME":str(self.actor), "LANG":"C.UTF-8"})
            self.state(lambda state: state["paused"])
            return self
        except BaseException:
            self.close(); raise

    def close(self):
        try:
            if self.process is not None and self.process.poll() is None:
                self.action("exit")
                try: self.process.wait(timeout=25)
                except subprocess.TimeoutExpired:
                    self.process.terminate()
                    try: self.process.wait(timeout=10)
                    except subprocess.TimeoutExpired: self.process.kill(); self.process.wait(timeout=5)
            if self.baseline is not None: self.wait_for(self.idle, 25)
            if self.baseline is not None:
                (self.output / "fixture-restored.json").write_text(json.dumps(self.snapshot(), indent=2))
            if self.process is not None:
                stopped = actor_files.call(self.actor, self.uid, self.gid, "read", dict(name="stopped.json"))
                require(self.process.returncode == 0 and stopped.get("cleanupConfirmed") is True, "Actor cleanup ACK missing")
            if self.actor.exists(): actor_files.save_snapshot(self.actor, self.uid, self.gid, self.output / "app")
            if self.fixture is not None: self.fixture.close()
            for path, content in reversed(self.files):
                require(path.is_file() and not path.is_symlink() and path.read_text() == content, "Test policy changed externally")
                path.unlink()
            if self.files:
                self.command("restore-rules", ["udevadm", "control", "--reload-rules"])
                self.command("restore-polkit", ["systemctl", "restart", "polkit.service"])
            if self.account_created: self.command("delete-actor", ["userdel", self.user])
            if self.private.exists():
                require(self.private.parent == Path("/opt") and self.private.name == self.user, "Unexpected actor private path")
                shutil.rmtree(self.private)
            self.cleanup["confirmed"] = True
        except BaseException as error:
            self.cleanup["error"] = str(error)
            if self.actor.exists():
                try: actor_files.save_snapshot(self.actor, self.uid, self.gid, self.output / "app")
                except BaseException as snapshot_error: self.cleanup["snapshotError"] = str(snapshot_error)
        finally:
            if self.log is not None: self.log.close()
            (self.output / "actor-cleanup.json").write_text(json.dumps(self.cleanup, indent=2)+"\n")
        return self.cleanup

    def __exit__(self, kind, error, traceback):
        result = self.close()
        if not result["confirmed"] and error is None: raise RuntimeError("Actor cleanup failed: "+str(result))
