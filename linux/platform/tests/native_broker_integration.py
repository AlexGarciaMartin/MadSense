#!/usr/bin/env python3
"""Native broker experiment; ROOT inside the explicitly owned QEMU test VM only.

The synthetic Bluetooth bus/VID/PID identify a UHID fixture, not physical hardware.
No policy change in this file belongs in the production installer.
"""
import argparse
import base64
import errno
import hashlib
import json
import os
from pathlib import Path
import select
import signal
import socket
import stat
import struct
import subprocess
import sys
import threading
import time
import uuid

SERVICE = "madsense-broker.service"
BROKER = "/run/madsense/broker.sock"
EVIOCGRAB = 0x40044590
# Generic gamepad: report 1, eight buttons and X/Y; report 0x31, 77 output bytes.
# This is intentionally not the real DualSense descriptor or firmware protocol.
DESCRIPTOR = bytes.fromhex(
    "05010905a101850105091901290815002501750195088102"
    "050109300931150026ff00750895028102"
    "0600ff09318531150026ff007508954d9102c0")


def wait_for(callback, seconds=15):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = callback()
        if value:
            return value
        time.sleep(.05)
    raise TimeoutError(callback.__name__)


def require(value, message):
    if not value:
        raise AssertionError(message)


def actor_main():
    """Ordinary-user process. Its private stdin carries only test operations."""
    endpoint = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    endpoint.connect(BROKER)
    pending = bytearray()
    events, opened, relays = [], {}, []
    counter = 0
    def messages(seconds):
        if not select.select([endpoint], [], [], seconds)[0]:
            return []
        data = endpoint.recv(65536)
        if not data:
            raise EOFError("broker disconnected")
        pending.extend(data)
        rows = []
        while b"\n" in pending:
            line, _, tail = pending.partition(b"\n")
            pending[:] = tail
            rows.append(json.loads(line))
        return rows
    def relay_loop(listener, destination):
        accepted, _ = listener.accept()
        with accepted, socket.create_connection(("127.0.0.1", destination), 5) as upstream:
            while True:
                ready, _, _ = select.select([accepted, upstream], [], [], 1)
                for source in ready:
                    chunk = source.recv(65536)
                    if not chunk:
                        return
                    (upstream if source is accepted else accepted).sendall(chunk)
    try:
        for line in sys.stdin:
            command = json.loads(line)
            try:
                op = command["testOp"]
                if op == "rpc":
                    counter += 1
                    endpoint.sendall((json.dumps({"id": counter, **command["request"]}) + "\n").encode())
                    answer = None
                    deadline = time.monotonic() + 15
                    while answer is None and time.monotonic() < deadline:
                        for row in messages(.25):
                            if row.get("id") == counter:
                                answer = row
                            else:
                                events.append(row)
                    if answer is None:
                        raise TimeoutError("broker RPC")
                    result = answer
                elif op == "events":
                    for row in messages(.1):
                        events.append(row)
                    result, events = events, []
                elif op == "open":
                    fd = os.open(command["path"], os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
                    opened[fd] = fd
                    result = {"fd": fd}
                elif op == "close":
                    os.close(opened.pop(command["fd"]))
                    result = {}
                elif op == "relay":
                    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    listener.bind(("127.0.0.1", 0))
                    listener.listen(1)
                    relays.append(listener)
                    threading.Thread(target=relay_loop, args=(listener, command["port"]), daemon=True).start()
                    result = {"port": listener.getsockname()[1]}
                elif op == "eof":
                    endpoint.close()
                    result = {}
                elif op == "quit":
                    break
                else:
                    raise ValueError("unknown test operation")
                print(json.dumps({"testOk": True, "result": result}), flush=True)
            except BaseException as error:
                print(json.dumps({"testOk": False, "error": type(error).__name__,
                                  "errno": getattr(error, "errno", None), "detail": str(error)}), flush=True)
    finally:
        for fd in opened:
            os.close(fd)
        endpoint.close()
        for listener in relays:
            listener.close()


class Actor:
    def __init__(self, username, output, label, actor_script):
        import pwd
        account = pwd.getpwnam(username)
        self.stderr = (output / f"actor-{label}.stderr").open("wb")
        self.process = subprocess.Popen([sys.executable, str(actor_script), "--actor"],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.stderr,
                                        text=True, bufsize=1, user=account.pw_uid,
                                        group=account.pw_gid, extra_groups=[])
    def call(self, test_op, **fields):
        self.process.stdin.write(json.dumps({"testOp": test_op, **fields}) + "\n")
        self.process.stdin.flush()
        require(bool(select.select([self.process.stdout], [], [], 20)[0]), "actor timed out")
        line = self.process.stdout.readline()
        require(line, "actor exited unexpectedly")
        return json.loads(line)
    def rpc(self, op, expected=None, **fields):
        outer = self.call("rpc", request={"op": op, **fields})
        require(outer["testOk"], f"actor RPC failed: {outer}")
        result = outer["result"]
        if expected is None:
            require(result.get("ok") is True, f"{op} rejected: {result}")
        else:
            require(result.get("ok") is False and result.get("error", {}).get("code") == expected,
                    f"{op} expected {expected}: {result}")
        return result
    def close(self):
        if self.process.poll() is None:
            self.process.stdin.close()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
        self.stderr.close()


class Uhid:
    def __init__(self, label):
        self.label = label
        self.fd = os.open("/dev/uhid", os.O_RDWR | os.O_NONBLOCK | os.O_CLOEXEC)
        self.outputs = []
        self.running = True
        self.write_lock = threading.Lock()
        payload = (label.encode().ljust(128, b"\0") + b"madsense-ci".ljust(64, b"\0")
                   + label.encode().ljust(64, b"\0") + struct.pack("=HHIIII", len(DESCRIPTOR), 5, 0x054C, 0x0CE6, 1, 0)
                   + DESCRIPTOR.ljust(4096, b"\0"))
        self.send(11, payload)
        self.thread = threading.Thread(target=self.read, daemon=True)
        self.thread.start()
        def parent():
            for path in Path("/sys/bus/hid/devices").glob("0005:054C:0CE6.*"):
                if f"HID_UNIQ={label}" in (path / "uevent").read_text().splitlines():
                    return path.resolve()
            return None
        self.parent = wait_for(parent)
        def nodes():
            found = []
            for directory, prefix, pattern in (("hidraw", "/dev", "hidraw*"), ("input", "/dev/input", "event*"),
                                                ("input", "/dev/input", "js*"), ("input", "/dev/input", "mouse*")):
                for path in Path("/sys/class", directory).glob(pattern):
                    if (path / "device").resolve().is_relative_to(self.parent):
                        found.append(Path(prefix, path.name))
            return sorted(found) if any("event" in p.name for p in found) and any("hidraw" in p.name for p in found) else None
        self.nodes = wait_for(nodes)
        require((self.parent / "driver").resolve().name == "hid-generic", "fixture must use generic HID test driver")
        self.hidraw = next(path for path in self.nodes if path.name.startswith("hidraw"))
        self.events = [path for path in self.nodes if path.name.startswith("event")]
    def send(self, kind, data=b""):
        packet = struct.pack("=I", kind) + data
        with self.write_lock:
            require(os.write(self.fd, packet) == len(packet), "short UHID event")
    def input(self, pressed=False):
        data = bytes([1, 1 if pressed else 0, 128, 128])
        self.send(12, struct.pack("=H", len(data)) + data)
    def read(self):
        while self.running:
            if not select.select([self.fd], [], [], .1)[0]:
                continue
            packet = os.read(self.fd, 4376).ljust(4376, b"\0")
            kind = struct.unpack_from("=I", packet)[0]
            if kind == 6:
                size = struct.unpack_from("=H", packet, 4100)[0]
                self.outputs.append(packet[4:4 + size].hex())
            elif kind == 9:
                request = struct.unpack_from("=I", packet, 4)[0]
                self.send(10, struct.pack("=IHH", request, errno.EIO, 0))
            elif kind == 13:
                request = struct.unpack_from("=I", packet, 4)[0]
                self.send(14, struct.pack("=IH", request, 0))
    def close(self):
        if self.running:
            self.running = False
            self.thread.join(timeout=1)
            self.send(1)
            os.close(self.fd)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--owned-guest-token", required=True)
    parser.add_argument("--probe", type=Path, required=True, help="Self-contained neutral exporter executable")
    parser.add_argument("--usbip", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "research"))
    from ci_environment import validate_environment
    from kernel_probe import ports
    require(os.geteuid() == 0, "native harness requires root inside its owned guest")
    scope = validate_environment(args.owned_guest_token)
    require(scope["kind"] == "owned-qemu-guest", "never run this fixture policy on the CI host or a physical system")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    require(not any(output.iterdir()), "fresh output directory required")
    result = dict(schema=1, result="failed", environment=scope, physicalDevices=False,
                  radioInitialized=False, productionPolicyBypass=False, temporaryVmTestPolicies=True, cases=[])
    actors, devices, event_fds, exporters, created_files, users = [], [], [], [], [], []
    unrelated_import = None
    actor_directory = Path("/run", "madsense-ci-native-" + str(uuid.uuid4()))
    actor_script = actor_directory / "actor.py"
    def save(name, value):
        (output / name).write_text(json.dumps(value, indent=2) + "\n")
    def command(label, argv, timeout=30, check=True):
        completed = subprocess.run([str(x) for x in argv], capture_output=True, timeout=timeout)
        (output / f"{label}.stdout").write_bytes(completed.stdout)
        (output / f"{label}.stderr").write_bytes(completed.stderr)
        if check:
            require(completed.returncode == 0, f"{label} failed: {completed.stderr.decode(errors='replace')[:300]}")
        return completed
    def fixture_file(path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x") as stream:
            stream.write(content)
        created_files.append((path, content))
    def actor(label, user="madsense-ci"):
        value = Actor(user, output, label, actor_script)
        actors.append(value)
        return value
    def snapshot(device):
        rows = {}
        for path in device.nodes:
            info = path.stat()
            rows[str(path)] = dict(uid=info.st_uid, gid=info.st_gid, mode=stat.S_IMODE(info.st_mode),
                                  dev=info.st_dev, inode=info.st_ino, rdev=info.st_rdev,
                                  acl=subprocess.check_output(["getfacl", "--omit-header", str(path)], text=True))
        return rows
    def restored(device, baseline):
        return (not Path("/run/madsense/lease.json").exists()
                and not Path("/run/udev/rules.d/72-madsense-lease.rules").exists() and snapshot(device) == baseline)
    def success(name):
        result["cases"].append({"name": name, "passed": True})
        save("result.json", result)
    def exporter(label):
        directory = output / f"exporter-{label}"
        log = (output / f"exporter-{label}.log").open("wb")
        process = subprocess.Popen([str(args.probe.resolve()), "--persona", "xbox", "--output", str(directory), "--seconds", "300"], stdout=log, stderr=log)
        exporters.append((process, directory, log))
        ready = wait_for(lambda: json.loads((directory / "ready.json").read_text()) if (directory / "ready.json").exists() else None)
        return ready
    def stored_count():
        return int(subprocess.check_output(["systemctl", "show", SERVICE, "--property=NFileDescriptorStore", "--value"], text=True).strip())
    try:
        # Ubuntu homes may be 0750; do not grant test users access to the runner's
        # home just to execute this fixture. Their script is root-owned under /run.
        actor_directory.mkdir(mode=0o755)
        actor_directory.chmod(0o755)
        fixture_file(actor_script, Path(__file__).read_text())
        actor_script.chmod(0o644)
        command("uname", ["uname", "-a"])
        command("systemd-version", ["systemctl", "--version"])
        command("effective-service", ["systemctl", "cat", SERVICE])
        save("installed-code.json", {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in (
            Path("/usr/libexec/madsense/broker.py"), Path("/usr/libexec/madsense/broker_core.py"),
            Path("/etc/systemd/system/madsense-broker.service"),
            Path("/usr/share/polkit-1/actions/org.madsense.bridge.policy"))})
        require(not Path("/run/madsense/lease.json").exists(), "pre-existing broker lease")
        require(not list(Path("/sys/bus/hid/devices").glob("0005:054C:*")), "pre-existing Sony Bluetooth HID")
        require(all(row["localBusId"] == "0-0" for row in ports().values()), "pre-existing USB/IP import")
        fixture_file(Path("/run/modprobe.d/madsense-ci-fixture.conf"), "# Owned VM test only\nblacklist hid_playstation\ninstall hid_playstation /bin/false\n")
        if Path("/sys/module/hid_playstation").exists():
            command("unload-fixture-driver", ["modprobe", "-r", "hid_playstation"])
        command("load-uhid", ["modprobe", "uhid"])
        command("load-generic", ["modprobe", "hid_generic"])
        for username in ("madsense-ci", "madsense-ci-denied"):
            command(f"create-{username}", ["useradd", "--user-group", "--no-create-home", "--shell", "/usr/sbin/nologin", username])
            users.append(username)
        fixture_file(Path("/etc/polkit-1/rules.d/10-madsense-ci-fixture.rules"),
                     '/* Owned VM test only; never package this rule. */\npolkit.addRule(function(action, subject) {\n'
                     '  if (action.id === "org.madsense.bridge.acquire" && subject.user === "madsense-ci") return polkit.Result.YES;\n});\n')
        command("reload-polkit", ["systemctl", "restart", "polkit.service"])
        command("broker-start", ["systemctl", "start", SERVICE])
        wait_for(lambda: Path(BROKER).exists())
        first, other = Uhid("madsense-ci-" + uuid.uuid4().hex[:16]), Uhid("madsense-ci-" + uuid.uuid4().hex[:16])
        devices.extend((first, other))
        save("uhid-fixtures.json", {"descriptorHex": DESCRIPTOR.hex(), "bus": 5, "vendor": 0x054C,
             "product": 0x0CE6, "parents": [str(device.parent) for device in devices]})
        command("settle-fixtures", ["udevadm", "settle", "--timeout=5"])
        fixture_file(Path("/run/udev/rules.d/71-madsense-ci-fixture.rules"),
                     "# Exact owned UHID fixtures only; production 72 final assignments override this.\n" +
                     "".join(f'SUBSYSTEM=="hidraw|input", KERNELS=="{device.parent.name}", OWNER="root", '
                             'GROUP="madsense-ci", MODE="0660", TAG-="uaccess", TAG-="seat"\n' for device in devices))
        command("reload-fixture-rules", ["udevadm", "control", "--reload-rules"])
        for index, device in enumerate(devices):
            command(f"apply-fixture-{index}", ["udevadm", "trigger", "--action=change", "--parent-match=" + str(device.parent)])
        command("settle-fixture-policy", ["udevadm", "settle", "--timeout=5"])
        original, other_original = snapshot(first), snapshot(other)
        save("nodes-before.json", {"primary": original, "other": other_original})
        observer = [os.open(node, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC) for node in first.events]
        event_fds.extend(observer)
        other_observer = os.open(other.events[0], os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        event_fds.append(other_observer)
        root_client = actor("root-rejected", "root")
        root_client.rpc("acquire", expected="not_authorized", path=str(first.hidraw))
        root_client.close()
        success("root_application_is_rejected")
        denied = actor("denied", "madsense-ci-denied")
        denied.rpc("acquire", expected="not_authorized", path=str(first.hidraw))
        denied.close()
        success("polkit_denies_unapproved_user")
        reader = actor("foreign-reader")
        opened = reader.call("open", path=str(first.hidraw))
        require(opened["testOk"], "foreign reader fixture did not open")
        owner = actor("busy-owner")
        owner.rpc("acquire", expected="physical_device_busy", path=str(first.hidraw))
        wait_for(lambda: restored(first, original))
        reader.call("close", fd=opened["result"]["fd"])
        reader.close()
        owner.close()
        success("foreign_hidraw_reader_refused_and_acl_rolled_back")
        owner = actor("normal-owner")
        owner.rpc("acquire", path=str(first.hidraw))
        require(stored_count() >= len(first.events), "input grabs absent from systemd FDSTORE")
        import fcntl
        for fd in observer:
            try:
                fcntl.ioctl(fd, EVIOCGRAB, 1)
            except OSError as error:
                require(error.errno == errno.EBUSY, "unexpected EVIOCGRAB error")
            else:
                fcntl.ioctl(fd, EVIOCGRAB, 0)
                raise AssertionError("broker did not grab every input child")
        for node in first.nodes:
            denied_open = reader = actor("access-" + node.name)
            response = denied_open.call("open", path=str(node))
            require(not response["testOk"] and response["errno"] == errno.EACCES, "ordinary user reopened isolated node")
            denied_open.close()
        require(snapshot(other) == other_original, "other controller permissions changed")
        fcntl.ioctl(other_observer, EVIOCGRAB, 1)
        fcntl.ioctl(other_observer, EVIOCGRAB, 0)
        other.input(True)
        require(select.select([other_observer], [], [], 1)[0] and os.read(other_observer, 4096),
                "unrelated controller stopped delivering input")
        for fd in observer:
            while select.select([fd], [], [], 0)[0]:
                os.read(fd, 4096)
        first.input(True)
        events = wait_for(lambda: owner.call("events")["result"])
        require(any(row.get("event") == "input" and base64.b64decode(row["data"]) == bytes([1, 1, 128, 128]) for row in events), "raw input RPC lost fixture bytes")
        require(not select.select(observer, [], [], .15)[0], "pre-existing evdev reader received grabbed input")
        owner.rpc("release")
        wait_for(lambda: restored(first, original))
        owner.close()
        success("grab_fdstore_acl_and_input_rpc_then_normal_release")
        owner = actor("eof-owner")
        owner.rpc("acquire", path=str(first.hidraw))
        owner.call("eof")
        wait_for(lambda: restored(first, original))
        owner.close()
        success("owner_socket_eof_cleanup")
        owner = actor("process-owner")
        owner.rpc("acquire", path=str(first.hidraw))
        owner.process.kill()
        owner.process.wait(timeout=3)
        wait_for(lambda: restored(first, original))
        owner.close()
        success("owner_process_exit_cleanup")
        owner = actor("permission-owner")
        owner.rpc("acquire", path=str(first.hidraw))
        os.chmod(first.events[0], 0o620)  # Root-only fault injection on this owned synthetic node.
        wait_for(lambda: restored(first, original))
        owner.close()
        success("permission_tampering_ends_lease")
        owner = actor("acl-owner")
        owner.rpc("acquire", path=str(first.hidraw))
        # Keep mode 0600; exercise the ACL-xattr monitor independently of chmod.
        command("inject-owned-acl-fault", ["setfacl", "-m", "u:madsense-ci-denied:r,m::---", first.hidraw])
        wait_for(lambda: restored(first, original))
        owner.close()
        success("named_acl_tampering_ends_lease")
        foreign_ready = exporter("unrelated")
        before_import = ports()
        command("unrelated-attach", [args.usbip, "--tcp-port", foreign_ready["port"], "attach", "--remote", "127.0.0.1", "--busid", "1-1"])
        def foreign_port():
            rows = [(port, row) for port, row in ports().items() if row != before_import[port] and row["localBusId"] != "0-0"]
            return rows[0] if len(rows) == 1 else None
        unrelated_import = wait_for(foreign_port)
        foreign_port_number, foreign_status = unrelated_import
        own_ready = exporter("owned")
        owner = actor("crash-owner")
        owner.rpc("acquire", path=str(first.hidraw))
        relay = owner.call("relay", port=own_ready["port"])
        require(relay["testOk"], "owner loopback relay failed")
        owner.rpc("attach", host="127.0.0.1", port=relay["result"]["port"], busId="1-1")
        saved = json.loads(Path("/run/madsense/lease.json").read_text())
        save("lease-before-sigkill.json", saved)
        require(stored_count() >= len(first.events) + 1, "import socket absent from FDSTORE")
        main_pid = int(subprocess.check_output(["systemctl", "show", SERVICE, "--property=MainPID", "--value"], text=True))
        require(main_pid > 1, "invalid broker PID")
        outputs_before_crash = len(first.outputs)
        os.kill(main_pid, signal.SIGKILL)
        wait_for(lambda: not Path(f"/proc/{main_pid}").exists(), seconds=5)
        retained_samples = 0
        deadline = time.monotonic() + .3
        while time.monotonic() < deadline:
            active_imports = [row for port, row in ports().items()
                              if port != foreign_port_number and row["localBusId"] != "0-0"]
            if not active_imports:
                break
            for fd in observer:
                try:
                    fcntl.ioctl(fd, EVIOCGRAB, 1)
                except OSError as error:
                    require(error.errno == errno.EBUSY, "unexpected retained-grab error")
                else:
                    fcntl.ioctl(fd, EVIOCGRAB, 0)
                    raise AssertionError("SIGKILL released grab while the virtual import remained")
            retained_samples += 1
            time.sleep(.02)
        require(retained_samples > 0, "no post-SIGKILL/pre-recovery retained-grab observation")
        result["retainedGrabSamples"] = retained_samples
        wait_for(lambda: restored(first, original), seconds=25)
        wait_for(lambda: subprocess.run(["systemctl", "is-active", "--quiet", SERVICE]).returncode == 0)
        require(ports().get(foreign_port_number) == foreign_status, "unrelated imported device was changed")
        require(snapshot(other) == other_original, "other controller changed during crash recovery")
        require(all(row["localBusId"] == "0-0" or port == foreign_port_number for port, row in ports().items()), "owned import survived recovery")
        for fd in observer:
            fcntl.ioctl(fd, EVIOCGRAB, 1)
            fcntl.ioctl(fd, EVIOCGRAB, 0)
        owner.close()
        fixture = json.loads(Path(__file__).with_name("neutral-reports.json").read_text())
        reset_expected = [next(row["hex"] for row in fixture if row["kind"] == kind and row["sequence"] == index)
                          for index, kind in enumerate(("rumble_legacy", "rumble_v2", "triggers_off"))]
        wait_for(lambda: len(first.outputs) >= outputs_before_crash + 3)
        save("uhid-output-reports.json", first.outputs)
        require(first.outputs[outputs_before_crash:outputs_before_crash + 3] == reset_expected,
                "crash recovery did not emit exact independently checked neutral-report fixtures")
        success("sigkill_fdstore_import_shutdown_grab_release_and_other_device_preserved")
        owner = actor("disconnect-owner")
        owner.rpc("acquire", path=str(first.hidraw))
        prior_parent = str(first.parent)
        first.close()
        wait_for(lambda: not Path("/run/madsense/lease.json").exists())
        replacement = Uhid("madsense-ci-reconnected-" + uuid.uuid4().hex[:8])
        devices.append(replacement)
        require(str(replacement.parent) != prior_parent, "new synthetic HID unexpectedly reused the same sysfs parent")
        require(not Path("/run/madsense/lease.json").exists(), "broker silently reacquired a new device")
        owner.close()
        success("disconnect_does_not_reacquire_replacement")
        result["result"] = "passed"
    except BaseException as error:
        result["reason"] = f"{type(error).__name__}: {error}"
    finally:
        for value in actors:
            value.close()
        command("broker-stop", ["systemctl", "stop", SERVICE], check=False)
        command("broker-journal", ["journalctl", "-u", SERVICE, "--no-pager", "-o", "short-monotonic"], check=False)
        command("broker-properties", ["systemctl", "show", SERVICE], check=False)
        if unrelated_import:
            port, row = unrelated_import
            if ports().get(port) == row:
                command("unrelated-detach", [args.usbip, "detach", "--port", port], check=False)
            elif ports().get(port, {}).get("localBusId") != "0-0":
                result["cleanupError"] = "unrelated fixture port changed; no numeric detach sent"
                result["result"] = "failed"
        for process, directory, log in exporters:
            (directory / "stop").touch()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
            log.close()
        for fd in event_fds:
            os.close(fd)
        for device in devices:
            device.close()
        for path, content in reversed(created_files):
            if path.exists() and path.read_text() == content:
                path.unlink()
            else:
                result["cleanupError"] = "test fixture policy changed unexpectedly"
                result["result"] = "failed"
        if actor_directory.exists() and not any(actor_directory.iterdir()):
            actor_directory.rmdir()
        command("reload-udev-cleanup", ["udevadm", "control", "--reload-rules"], check=False)
        for username in reversed(users):
            command(f"delete-{username}", ["userdel", username], check=False)
        command("reload-polkit-cleanup", ["systemctl", "restart", "polkit.service"], check=False)
        if Path("/run/madsense/lease.json").exists() or Path("/run/udev/rules.d/72-madsense-lease.rules").exists():
            result["cleanupError"] = "production recovery journal/rule remains; left intact for inspection"
            result["result"] = "failed"
        save("ports-after.json", ports())
        save("result.json", result)
        print(json.dumps(result), flush=True)
    return 0 if result["result"] == "passed" else 1


if __name__ == "__main__":
    if sys.argv[1:] == ["--actor"]:
        actor_main()
    else:
        raise SystemExit(main())
