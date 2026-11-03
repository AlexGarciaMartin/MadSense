#!/usr/bin/env python3
"""Second-stage integration of production C# core, only in the owned QEMU VM.

Run after the native broker baseline. This is a test actor and fixture policy,
never part of the production installer. No application UI coverage is implied.
"""
import argparse
import binascii
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import select
import shutil
import stat
import struct
import subprocess
import sys
import time
import uuid

from production_fixture import SyntheticDualSense, features, input_report, manifest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "research"))
from ci_environment import validate_environment
from kernel_probe import contains_exact, owned_nodes, ports, sysfs_bitmap, wait_for


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def read_json(path):
    return json.loads(path.read_text()) if path.exists() else None


def valid_output(data):
    return (bool(data) and len(data) == {0x31: 78, 0x32: 142, 0x35: 334, 0x36: 398}.get(data[0]) and
            struct.unpack_from("<I", data, len(data) - 4)[0] == binascii.crc32(b"\xa2" + data[:-4]))


def state_payload(data):
    if len(data) == 78 and data[0] == 0x31:
        return data[3:50]
    if len(data) == 398 and data[0] == 0x36 and data[11:13] == b"\x90\x3f":
        return data[13:60]
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--owned-guest-token", required=True)
    parser.add_argument("--probe-directory", type=Path, required=True)
    parser.add_argument("--host-probe", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    require(os.geteuid() == 0, "Run only as root inside the owned test guest")
    scope = validate_environment(args.owned_guest_token)
    require(scope["kind"] == "owned-qemu-guest", "Owned QEMU guest required")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    require(not any(output.iterdir()), "Fresh output directory required")
    result = dict(schema=1, result="failed", scope="production-core-synthetic-UHID", environment=scope,
                  physicalDevices=False, radioInitialized=False, applicationUiExercised=False,
                  temporaryVmTestPolicies=True, cases=[])
    actor_root = Path("/run", "madsense-core-ci-" + uuid.uuid4().hex)
    actor_output = actor_root / "output"
    user = "madsense-core-ci"
    process, fixture, log, baseline, usb = None, None, None, None, None
    created_files, created_user = [], False
    started_module = Path("/sys/module/hid_playstation").exists()

    def save(name, value):
        (output / name).write_text(json.dumps(value, indent=2) + "\n")

    def command(label, argv, timeout=30, check=True):
        answer = subprocess.run([str(value) for value in argv], capture_output=True, timeout=timeout)
        (output / (label + ".stdout")).write_bytes(answer.stdout)
        (output / (label + ".stderr")).write_bytes(answer.stderr)
        require(not check or answer.returncode == 0, f"{label} failed: {answer.stderr.decode(errors='replace')[-500:]}")
        return answer

    def fixture_file(path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x") as stream:
            stream.write(content)
        created_files.append((path, content))

    def snapshot():
        nodes = []
        for group, pattern, dev in (("hidraw", "hidraw*", "/dev"), ("input", "*", "/dev/input")):
            for node in owned_nodes(fixture.parent, group, pattern):
                path = Path(dev, node.name)
                if path.exists() and stat.S_ISCHR(path.stat().st_mode):
                    value = path.stat()
                    nodes.append((str(path), value.st_uid, value.st_gid, stat.S_IMODE(value.st_mode),
                                  subprocess.check_output(["getfacl", "--omit-header", str(path)], text=True)))
        return sorted(nodes)

    def cleanup_done():
        return (not Path("/run/madsense/lease.json").exists()
                and not Path("/run/udev/rules.d/72-madsense-lease.rules").exists()
                and (usb is None or not usb.exists())
                and all(row["localBusId"] == "0-0" for row in ports().values())
                and snapshot() == baseline)

    def passed(name):
        result["cases"].append(dict(name=name, passed=True))
        save("result.json", result)

    try:
        require(not Path("/run/madsense/lease.json").exists(), "Pre-existing broker lease")
        require(not list(Path("/sys/bus/hid/devices").glob("0005:054C:*")), "Pre-existing Bluetooth Sony HID")
        require(all(row["localBusId"] == "0-0" for row in ports().values()), "Pre-existing USB/IP import")
        try:
            pwd.getpwnam(user)
        except KeyError:
            command("create-actor", ["useradd", "--system", "--no-create-home", "--shell", "/usr/sbin/nologin", user])
            created_user = True
        else:
            raise RuntimeError("Refusing to reuse a pre-existing test account")
        account = pwd.getpwnam(user)
        actor_root.mkdir(mode=0o755)
        shutil.copytree(args.probe_directory, actor_root / "probe", ignore=shutil.ignore_patterns("*.pdb"))
        actor_output.mkdir(mode=0o700)
        os.chown(actor_output, account.pw_uid, account.pw_gid)
        fixture_file(Path("/etc/polkit-1/rules.d/45-madsense-core-ci.rules"),
                     'polkit.addRule(function(action, subject) {\n'
                     'if (action.id === "org.madsense.bridge.acquire" && subject.user === "madsense-core-ci") return polkit.Result.YES;\n});\n')
        command("restart-polkit", ["systemctl", "restart", "polkit.service"])
        if started_module:
            command("unload-playstation-for-fixture", ["modprobe", "-r", "hid_playstation"])
        block = Path("/run/modprobe.d/madsense-core-ci.conf")
        fixture_file(block, "# Owned disposable QEMU fixture only\nblacklist hid_playstation\ninstall hid_playstation /bin/false\n")
        command("load-uhid", ["modprobe", "uhid"])
        fixture = SyntheticDualSense("madsense-core-" + uuid.uuid4().hex[:12], output)
        block.unlink()
        command("load-playstation-for-virtual", ["modprobe", "hid_playstation"])
        require((fixture.parent / "driver").resolve().name == "hid-generic", "Fixture unexpectedly rebound")
        fixture_file(Path("/run/udev/rules.d/71-madsense-core-ci.rules"),
                     f'SUBSYSTEM=="hidraw|input", KERNELS=="{fixture.parent.name}", OWNER="root", '
                     f'GROUP="{user}", MODE="0660", TAG-="uaccess", TAG-="seat"\n')
        command("reload-fixture-rules", ["udevadm", "control", "--reload-rules"])
        command("trigger-fixture", ["udevadm", "trigger", "--action=change", "--parent-match=" + str(fixture.parent)])
        command("settle-fixture", ["udevadm", "settle", "--timeout=5"])
        baseline = snapshot()
        save("physical-fixture.json", dict(parent=str(fixture.parent), hidraw=str(fixture.hidraw), baseline=baseline, **manifest()))
        log = (output / "core-probe.log").open("wb")
        process = subprocess.Popen([str(actor_root / "probe/MadSense.Linux.CoreProbe"),
            "--owned-guest-token", args.owned_guest_token, "--device", str(fixture.hidraw),
            "--output", str(actor_output), "--seconds", "120"], stdout=log, stderr=log,
            user=account.pw_uid, group=account.pw_gid, extra_groups=[], cwd=actor_root,
            env={**os.environ, "HOME": str(actor_output), "DOTNET_CLI_HOME": str(actor_output)})
        fixture.input_enabled = True

        def alive():
            require(process.poll() is None, "Production core exited before readiness; inspect core-probe.log")
            require(fixture.error is None, f"UHID thread failed: {fixture.error}")

        def attached():
            alive()
            rows = [row for row in ports().values() if row["localBusId"] != "0-0"]
            require(len(rows) <= 1, "Ambiguous VHCI attachment")
            if not rows:
                return None
            candidate = Path("/sys/bus/usb/devices", rows[0]["localBusId"])
            if not (candidate / "idVendor").exists():
                return None
            require(int((candidate / "idVendor").read_text(), 16) == 0x054c and
                    int((candidate / "idProduct").read_text(), 16) == 0x0ce6, "Unexpected imported persona")
            require("vhci_hcd" in str(candidate.resolve()), "Imported persona is not below VHCI")
            return candidate

        usb = wait_for(attached, 40)
        ready = wait_for(lambda: (alive(), read_json(actor_output / "ready.json"))[1], 35)
        raw = wait_for(lambda: (owned_nodes(usb, "hidraw", "hidraw*") or [None])[0])
        event = wait_for(lambda: next((node for node in owned_nodes(usb, "input", "event*")
            if (sysfs_bitmap((node / "device/capabilities/key").read_text()) >> 304) & 1), None))
        hidraw = Path("/dev", raw.name)
        require((raw / "device/driver").resolve().name == "playstation", "Virtual persona not handled by hid-playstation")
        save("consumer-ready.json", dict(ownedGuestToken=args.owned_guest_token, usbParent=str(usb.resolve()),
             localBusId=usb.name, hidrawPath=str(hidraw), evdevPath=str(Path("/dev/input", event.name)),
             vendorId=ready["vendorId"], productId=ready["productId"], actorPid=process.pid))
        require({5, 9, 0x20}.issubset(fixture.feature_requests), "Production constructor/calibration feature RPC missing")
        passed("production_hid_enumeration_features_parser_attach")
        command("host-hid", [args.host_probe, "hid", hidraw, actor_output / "hid-descriptor.bin", output / "host-hid.json"])
        observed = read_json(output / "host-hid.json")
        for key, value in ready["features"].items():
            require(observed["features"][key]["hex"].lower() == value.lower(), "Virtual feature mismatch: " + key)
        passed("kernel_virtual_descriptor_features_neutral_input")
        wait_for(lambda: any((payload := state_payload(data)) is not None and payload[44:47] == bytes.fromhex("123456")
                             for data in fixture.outputs))
        passed("kernel_rgb_output_to_production_bluetooth_writer")
        fixture.marker = True
        fd = os.open(hidraw, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        try:
            def marker_input():
                alive()
                if not select.select([fd], [], [], .1)[0]:
                    return None
                data = os.read(fd, 4096)
                if len(data) == 64 and data[1:7] == bytes([17, 229, 63, 201, 91, 173]):
                    return data
                return None
            received = wait_for(marker_input)
            expected = b"\x01" + input_report(True)[2:65]
            # Counter/timestamp move each frame. Every other payload byte is exact.
            skip = {7, *range(28, 32)}
            require(all(value == expected[index] for index, value in enumerate(received) if index not in skip),
                    "Marker input changed between physical parser and kernel USB raw report")
            save("marker-input.json", dict(received=received.hex(), expected=expected.hex(), volatileIndices=sorted(skip)))
        finally:
            os.close(fd)
            fixture.marker = False
        passed("full_bluetooth_input_through_parser_and_virtual_usb")
        card = wait_for(lambda: (owned_nodes(usb, "sound", "card[0-9]*") or [None])[0])
        number = int(card.name.removeprefix("card"))
        source = b"".join(struct.pack("<hhhh", 1000 + i % 997, -2000 - i % 991, 4000 + i % 983, -6000 - i % 977)
                          for i in range(48000))
        expected_haptics = b"".join(source[i + 4:i + 8] for i in range(0, len(source), 8))
        (output / "playback-source.pcm").write_bytes(source)
        command("alsa-playback", ["aplay", "-D", f"hw:{number},0", "-t", "raw", "-f", "S16_LE", "-r", "48000", "-c", "4", output / "playback-source.pcm"])
        result["audioIngress"] = contains_exact((actor_output / "audio-ingress.pcm").read_bytes(), source, 8, True)
        result["rearHapticsIngress"] = contains_exact((actor_output / "haptics-ingress.pcm").read_bytes(), expected_haptics, 4, True)
        wait_for(lambda: any(len(data) == 398 and data[0] == 0x36 and any(data[78:142]) for data in fixture.outputs))
        require(all(valid_output(data) for data in fixture.outputs), "Invalid physical synthetic output framing/CRC")
        passed("alsa_four_channel_ingress_and_nonzero_bluetooth_haptics")
        (actor_output / "stop").touch()
        require(process.wait(timeout=30) == 0, "Core actor failed during orderly disposal")
        wait_for(cleanup_done, 20)
        stopped = read_json(actor_output / "stopped.json")
        require(stopped and stopped["completed"] and stopped["inputs"] > 0, "Core did not confirm clean completion")
        require(stopped["metrics"]["inputOverflows"] == 0, "Production proxy input overflowed")
        passed("production_core_dispose_detach_reset_and_physical_restore")
        result["result"] = "passed"
    except BaseException as error:
        result["reason"] = f"{type(error).__name__}: {error}"
    finally:
        try:
            if process is not None and process.poll() is None:
                (actor_output / "stop").touch()
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait(timeout=5)
            if fixture is not None and baseline is not None:
                wait_for(cleanup_done, 20)
            if actor_output.exists():
                shutil.copytree(actor_output, output / "core", dirs_exist_ok=True)
                # Actor output is 0700 while active; make only the copied test
                # evidence readable to the unprivileged SSH collector.
                for item in [output / "core", *(output / "core").rglob("*")]:
                    item.chmod(0o755 if item.is_dir() else 0o644)
        except BaseException as error:
            result["cleanupError"] = str(error)
            result["result"] = "failed"
        finally:
            if log is not None:
                log.close()
            if fixture is not None:
                fixture.close()
            for path, content in reversed(created_files):
                if path.exists() and path.read_text() == content:
                    path.unlink()
            command("reload-restored-rules", ["udevadm", "control", "--reload-rules"], check=False)
            command("restart-restored-polkit", ["systemctl", "restart", "polkit.service"], check=False)
            if created_user:
                command("delete-actor", ["userdel", user], check=False)
            # actor_root was created by this invocation under /run with UUID.
            if actor_root.parent == Path("/run") and actor_root.name.startswith("madsense-core-ci-"):
                shutil.rmtree(actor_root, ignore_errors=True)
            save("result.json", result)
    return 0 if result["result"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
