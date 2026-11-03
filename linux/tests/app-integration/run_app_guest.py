#!/usr/bin/env python3
"""Drive the real application controller against UHID in an already owned guest.

This test actor/polkit fixture is never installed with the application or broker.
The existing native broker baseline must have passed in this exact guest first.
"""
import argparse
import json
import os
from pathlib import Path
import pwd
import shutil
import stat
import struct
import subprocess
import sys
import uuid

LINUX = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(LINUX / "research"))
sys.path.insert(0, str(LINUX / "platform/tests"))
sys.path.insert(0, str(LINUX / "platform/integration"))
from ci_environment import validate_environment
from kernel_probe import owned_nodes, ports, wait_for
from qemu_broker import verify_result
from production_fixture import SyntheticDualSense
from actor_staging import stage_probe, verify_actor_execution
from guest_diagnostics import save_broker_diagnostics
from run_core_guest import state_payload, valid_output


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def load(path):
    return json.loads(path.read_text()) if path.exists() else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--owned-guest-token", required=True)
    parser.add_argument("--baseline-result", type=Path, required=True)
    parser.add_argument("--probe-directory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    require(os.geteuid() == 0, "Root observer required inside owned guest")
    scope = validate_environment(args.owned_guest_token)
    require(scope["kind"] == "owned-qemu-guest", "Owned QEMU guest required")
    verify_result(load(args.baseline_result), args.owned_guest_token)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    require(not any(output.iterdir()), "Fresh output required")
    result = dict(schema=1, result="failed", environment=scope, applicationControllerExercised=True,
                  applicationUiExercised=False, physicalDevices=False, radioInitialized=False,
                  temporaryVmTestPolicies=True, cases=[])
    user = "madsense-app-ci"
    root = Path("/opt", "madsense-app-ci-" + uuid.uuid4().hex)
    actor = root / "output"
    process = fixture = log = account = baseline = None
    created_files, created_user, commands, fixture_number = [], False, 0, 0
    root_created = False

    def save(name, value):
        (output / name).write_text(json.dumps(value, indent=2) + "\n")

    def command(label, argv, check=True):
        answer = subprocess.run([str(item) for item in argv], capture_output=True, timeout=40)
        (output / (label + ".stdout")).write_bytes(answer.stdout)
        (output / (label + ".stderr")).write_bytes(answer.stderr)
        require(not check or answer.returncode == 0, f"{label}: {answer.stderr.decode(errors='replace')[-500:]}")
        return answer

    def fixture_file(path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x") as stream:
            stream.write(content)
        created_files.append((path, content))

    def snapshot():
        rows = []
        for group, pattern, directory in (("hidraw", "hidraw*", "/dev"), ("input", "*", "/dev/input")):
            for item in owned_nodes(fixture.parent, group, pattern):
                node = Path(directory, item.name)
                if node.exists() and stat.S_ISCHR(node.stat().st_mode):
                    info = node.stat()
                    rows.append((str(node), info.st_dev, info.st_ino, info.st_rdev, info.st_uid, info.st_gid,
                                 stat.S_IMODE(info.st_mode), subprocess.check_output(
                                     ["getfacl", "--numeric", "--omit-header", node], text=True)))
        return sorted(rows)

    def idle(restore=True):
        return (not Path("/run/madsense/lease.json").exists()
                and not Path("/run/udev/rules.d/72-madsense-lease.rules").exists()
                and all(row["localBusId"] == "0-0" for row in ports().values())
                and (not restore or snapshot() == baseline))

    def alive():
        require(process is not None and process.poll() is None, "Application actor exited; inspect actor.log")
        require(fixture is None or fixture.error is None, f"UHID fixture failed: {getattr(fixture, 'error', None)}")

    def status():
        alive()
        value = load(actor / "status.json")
        return value if value and value["lastQueuedCommand"] >= commands else None

    def action(operation, value=None):
        nonlocal commands
        commands += 1
        target = actor / "commands" / f"{commands:06}.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(dict(operation=operation, value=value)))
        temporary.replace(target)
        if operation != "exit":
            wait_for(status, 10)

    def observed_state(predicate, seconds=40):
        return wait_for(lambda: value if (value := status()) and predicate(value["state"]) else None, seconds)

    def attached(vendor, product):
        alive()
        rows = [row for row in ports().values() if row["localBusId"] != "0-0"]
        require(len(rows) <= 1, "Ambiguous imports in owned guest")
        if not rows:
            return None
        node = Path("/sys/bus/usb/devices", rows[0]["localBusId"])
        if not (node / "idVendor").exists():
            return None
        if (int((node / "idVendor").read_text(), 16), int((node / "idProduct").read_text(), 16)) != (vendor, product):
            return None  # A mode transition may still be removing its previous persona.
        require("vhci_hcd" in str(node.resolve()), "Virtual controller must belong to VHCI")
        return node.resolve()

    def ready(vendor=0x054c, product=0x0ce6):
        usb = wait_for(lambda: attached(vendor, product), 45)
        value = observed_state(lambda state: state["ready"])
        journal = load(Path("/run/madsense/lease.json"))
        require(journal and journal["physicalPath"] == str(fixture.hidraw), "Application acquired a different physical node")
        return usb, value

    def settings(volume=None, gain=None, mode=None):
        try:
            value = load(actor / "config/madsense/controllers.json")
        except json.JSONDecodeError:
            return None  # Production settings persistence writes the file in place.
        if not value or len(value["controllers"]) != 1:
            return None
        controller = value["controllers"][0]
        require(controller["mac_address"] == "02:FA:11:22:33:44", "Unexpected pairing identity")
        emulation = controller["emulation"]
        if volume is not None and emulation["forward"]["volume"] != volume:
            return None
        if gain is not None and emulation["forward"]["speaker_pre_gain"] != gain:
            return None
        if mode is not None and emulation["mode"] != mode:
            return None
        require(emulation["forward"]["strict_native_haptics"] is True, "Production strict native haptics setting missing")
        return emulation

    def metrics(value):
        diagnostic = value["diagnostic"]
        start = diagnostic.find('{\n  "scope":')
        require(start >= 0, "Application diagnostics omit proxy metrics")
        measurement, _ = json.JSONDecoder().raw_decode(diagnostic[start:])
        require(measurement["metrics"]["InputOverflows"] == 0, "Real application proxy overflowed")
        return measurement

    def passed(name, **evidence):
        result["cases"].append(dict(name=name, passed=True, **evidence))
        save("result.json", result)

    def create_fixture():
        nonlocal fixture, fixture_number, baseline
        fixture_number += 1
        require(not list(Path("/sys/bus/hid/devices").glob("000[35]:054C:*")), "Pre-existing Sony device before fixture creation")
        command(f"load-driver-{fixture_number}", ["modprobe", "hid_playstation"])
        directory = output / f"fixture-{fixture_number}"
        directory.mkdir()
        fixture = SyntheticDualSense("madsense-app-" + uuid.uuid4().hex[:12], directory)
        require((fixture.parent / "driver").resolve().name == "playstation", "Fixture must use the real Sony kernel driver")
        rule = Path("/run/udev/rules.d/71-madsense-app-ci.rules")
        fixture_file(rule, f'SUBSYSTEM=="hidraw|input", KERNELS=="{fixture.parent.name}", OWNER="root", '
                     f'GROUP="{user}", MODE="0660", TAG-="uaccess", TAG-="seat"\n')
        command(f"reload-rules-{fixture_number}", ["udevadm", "control", "--reload-rules"])
        command(f"trigger-{fixture_number}", ["udevadm", "trigger", "--action=change", "--parent-match=" + str(fixture.parent)])
        command(f"settle-{fixture_number}", ["udevadm", "settle", "--timeout=5"])
        baseline = snapshot()
        save(f"fixture-{fixture_number}-identity.json", dict(parent=str(fixture.parent), hidraw=str(fixture.hidraw), baseline=baseline))

    try:
        require(idle(False), "Pre-existing lease, recovery or import")
        try:
            pwd.getpwnam(user)
        except KeyError:
            command("create-actor", ["useradd", "--system", "--no-create-home", "--shell", "/usr/sbin/nologin", user])
            created_user = True
        else:
            raise RuntimeError("Refusing pre-existing test account")
        account = pwd.getpwnam(user)
        root.mkdir(mode=0o755)
        root_created = True
        root.chmod(0o755)
        executable = stage_probe(args.probe_directory, root / "probe", "MadSense.Linux.AppProbe")
        verify_actor_execution(root, executable, account, output / "actor-staging.json")
        actor.mkdir(mode=0o700); os.chown(actor, account.pw_uid, account.pw_gid)
        fixture_file(Path("/etc/polkit-1/rules.d/45-madsense-app-ci.rules"),
                     'polkit.addRule(function(action, subject) { if (action.id === "org.madsense.bridge.acquire" '
                     '&& subject.user === "madsense-app-ci") return polkit.Result.YES; });\n')
        command("restart-polkit", ["systemctl", "restart", "polkit.service"])
        command("load-uhid", ["modprobe", "uhid"])
        create_fixture()
        log = (output / "actor.log").open("wb")
        process = subprocess.Popen([root / "probe/MadSense.Linux.AppProbe", "--owned-guest-token", args.owned_guest_token,
            "--device", str(fixture.hidraw), "--fixture-label", fixture.label, "--output", str(actor)],
            stdout=log, stderr=log, user=account.pw_uid, group=account.pw_gid, extra_groups=[], cwd=root,
            env={**os.environ, "HOME": str(actor), "DOTNET_CLI_HOME": str(actor)})
        observed_state(lambda state: state["paused"])
        action("resume")
        wait_for(lambda: attached(0x054c, 0x0ce6), 45)
        require(not status()["state"]["ready"], "Application declared Ready without a parsed physical input report")
        fixture.input_enabled = True
        usb, _ = ready()
        initial = wait_for(lambda: settings(100, 7, "DualSense"))
        passed("production_connect_settings_and_ready_require_input", settings=initial)

        # Only kernel-observed output is examined. No virtual factory decorator
        # or duplicated application settings implementation feeds this test.
        card = wait_for(lambda: (owned_nodes(usb, "sound", "card[0-9]*") or [None])[0])
        source = b"".join(struct.pack("<hhhh", 1200, -2400, 4000, -6000) for _ in range(48000))
        pcm = output / "speaker-source.pcm"; pcm.write_bytes(source)
        for level, volume, gain in (("Alto", 100, 7), ("Bajo", 100, 2), ("Off", 0, 0)):
            action("speaker", level)
            saved = wait_for(lambda: settings(volume, gain, "DualSense"))
            start = len(fixture.outputs)
            command("play-" + level, ["aplay", "-D", f"hw:{card.name[4:]},0", "-t", "raw", "-f", "S16_LE", "-r", "48000", "-c", "4", pcm])
            wait_for(lambda: any((payload := state_payload(data)) is not None and payload[5] == volume
                                and payload[37] & 7 == gain for data in fixture.outputs[start:]), 15)
            require(all(valid_output(data) for data in fixture.outputs[start:]), "Invalid physical output CRC/framing")
            passed("speaker_" + level.lower() + "_settings_and_delivered_control", settings=saved)

        for mode, vid, pid, setting in (("Xbox", 0x045e, 0x028e, "Xbox360"), ("Auto", 0x054c, 0x0ce6, "DualSense")):
            old_usb = usb
            action("mode", mode)
            usb, _ = ready(vid, pid)
            wait_for(lambda: settings(0, 0, setting))
            require(not old_usb.exists() or old_usb == usb, "Previous virtual node remains after mode switch")
            passed("mode_" + mode.lower() + "_uses_production_settings_and_kernel_persona", vendorId=vid, productId=pid)

        action("pause")
        observed_state(lambda state: state["paused"] and not state["releasing"])
        require(idle(), "Paused was reported before kernel/permissions cleanup")
        passed("pause_confirms_virtual_removal_and_physical_permissions", metrics=metrics(status()))
        action("resume"); usb, _ = ready()
        passed("resume_reacquires_through_real_enumerator_and_broker")

        fixture.close(); fixture = None
        wait_for(lambda: idle(False), 30)
        observed_state(lambda state: not state["ready"])
        rule = Path("/run/udev/rules.d/71-madsense-app-ci.rules")
        rule.unlink()
        create_fixture(); fixture.input_enabled = True
        usb, _ = ready()
        passed("synthetic_unplug_replug_automatically_reconnects")

        action("pause")
        observed_state(lambda state: state["paused"] and not state["releasing"])
        require(idle(), "Final paused state is unconfirmed by root observer")
        action("exit")
        require(process.wait(timeout=30) == 0, "Application actor failed disposal")
        stopped = load(actor / "stopped.json")
        require(stopped and stopped["cleanupConfirmed"] and idle(), "Final cleanup lacks fresh helper and kernel confirmation")
        passed("dispose_and_fresh_helper_confirm_cleanup", metrics=metrics(stopped))
        result["result"] = "passed"
    except BaseException as error:
        result["reason"] = f"{type(error).__name__}: {error}"
    finally:
        try:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait(timeout=5)
            if fixture is not None and baseline is not None:
                wait_for(idle, 25)
        except BaseException as error:
            result["cleanupError"] = str(error); result["result"] = "failed"
        finally:
            # Preserve actor evidence even when ordered helper cleanup failed.
            if actor.exists():
                shutil.copytree(actor, output / "app", dirs_exist_ok=True)
                for item in [output / "app", *(output / "app").rglob("*")]:
                    item.chmod(0o755 if item.is_dir() else 0o644)
            if log is not None:
                log.close()
            if fixture is not None:
                fixture.close()
            for path, content in reversed(created_files):
                if path.exists() and path.read_text() == content:
                    path.unlink()
            command("restore-rules", ["udevadm", "control", "--reload-rules"], check=False)
            command("restore-polkit", ["systemctl", "restart", "polkit.service"], check=False)
            if created_user:
                command("delete-actor", ["userdel", user], check=False)
            if root_created and root.parent == Path("/opt") and root.name.startswith("madsense-app-ci-"):
                shutil.rmtree(root, ignore_errors=True)
            save_broker_diagnostics(output)
            save("result.json", result)
    return 0 if result["result"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
