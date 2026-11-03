#!/usr/bin/env python3
"""Optional timing experiment, after functional gates, in the same owned VM.

Each rate gets a fresh UHID fixture, ordinary account and real AppProbe instance.
This does not replace or contribute a passed case to any functional result.
"""
import argparse
import json
import os
from pathlib import Path
import pwd
import secrets
import shutil
import stat
import subprocess
import sys
import uuid

from probe import build, metrics, require, run, save, validate_environment

LINUX = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(LINUX / "platform/tests"))
sys.path.insert(0, str(LINUX / "platform/integration"))
sys.path.insert(0, str(LINUX / "tests/app-integration"))
from kernel_probe import owned_nodes, ports, wait_for
from actor_staging import stage_probe, verify_actor_execution
from production_fixture import SyntheticDualSense
from gates import verify_paths


def load(path):
    return json.loads(path.read_text()) if path.exists() else None


def case(args, output, executable, rate, *, trace_directory=None):
    output.mkdir()
    result = dict(rateHz=rate, result="invalid", cleanupConfirmed=False)
    user = "madsense-lat-" + uuid.uuid4().hex[:12]
    private = Path("/opt", user)
    actor = private / "actor"
    fixture = process = log = baseline = None
    created_user, root_created, commands = False, False, 0
    files = []
    nonce = 0x8000 + secrets.randbelow(0x8000)

    def command(label, argv):
        completed = subprocess.run([str(item) for item in argv], capture_output=True, timeout=30)
        (output / (label + ".stdout")).write_bytes(completed.stdout)
        (output / (label + ".stderr")).write_bytes(completed.stderr)
        require(completed.returncode == 0, f"{label} failed: {completed.stderr.decode(errors='replace')[-500:]}")
        return completed

    def create(path, content):
        with path.open("x") as stream:
            stream.write(content)
        files.append((path, content))

    def snapshot():
        rows = []
        for subsystem, pattern, directory in (("hidraw", "hidraw*", "/dev"), ("input", "event*", "/dev/input")):
            for entry in owned_nodes(fixture.parent, subsystem, pattern):
                path = Path(directory, entry.name)
                info = path.lstat()
                require(stat.S_ISCHR(info.st_mode), "Fixture node changed type")
                rows.append(dict(path=str(path), device=info.st_dev, inode=info.st_ino, rdev=info.st_rdev,
                    uid=info.st_uid, gid=info.st_gid, mode=stat.S_IMODE(info.st_mode),
                    acl=subprocess.check_output(["getfacl", "--absolute-names", "--numeric", "--omit-header", path], text=True)))
        return sorted(rows, key=lambda row: row["path"])

    def idle():
        return (not Path("/run/madsense/lease.json").exists()
                and not Path("/run/udev/rules.d/72-madsense-lease.rules").exists()
                and all(row["localBusId"] == "0-0" for row in ports().values())
                and (baseline is None or snapshot() == baseline))

    def status():
        require(process is not None and process.poll() is None, "Application actor exited")
        require(fixture.error is None, f"Fixture failed: {fixture.error}")
        value = load(actor / "status.json")
        return value if value and value["lastQueuedCommand"] >= commands else None

    def action(operation):
        nonlocal commands
        commands += 1
        target = actor / "commands" / f"{commands:06}.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(dict(operation=operation)))
        temporary.replace(target)

    def state(predicate, seconds=45):
        return wait_for(lambda: value if (value := status()) and predicate(value["state"]) else None, seconds)

    try:
        require(idle(), "Previous session has pending lease/rule/import")
        require(not list(Path("/sys/bus/hid/devices").glob("000[35]:054C:*")), "Unexpected Sony device before fixture")
        try:
            pwd.getpwnam(user)
        except KeyError:
            command("create-actor", ["useradd", "--system", "--no-create-home", "--shell", "/usr/sbin/nologin", user])
            created_user = True
        else:
            raise RuntimeError("Refusing existing test account")
        account = pwd.getpwnam(user)
        private.mkdir(mode=0o755)
        root_created = True
        private.chmod(0o755)
        actor_executable = stage_probe(trace_directory or args.probe_directory, private / "probe", "MadSense.Linux.AppProbe")
        verify_actor_execution(private, actor_executable, account, output / "actor-staging.json")
        actor.mkdir(mode=0o700)
        os.chown(actor, account.pw_uid, account.pw_gid)
        create(Path("/etc/polkit-1/rules.d", "45-" + user + ".rules"),
               'polkit.addRule(function(action, subject) { if (action.id === "org.madsense.bridge.acquire" '
               f'&& subject.user === "{user}") return polkit.Result.YES; }});\n')
        command("restart-polkit", ["systemctl", "restart", "polkit.service"])
        command("load-uhid", ["modprobe", "uhid"])
        command("load-playstation", ["modprobe", "hid_playstation"])
        fixture_dir = output / "fixture"
        fixture_dir.mkdir()
        fixture = SyntheticDualSense("madsense-app-" + uuid.uuid4().hex[:12], fixture_dir)
        create(Path("/run/udev/rules.d", "71-" + user + ".rules"),
               f'SUBSYSTEM=="hidraw|input", KERNELS=="{fixture.parent.name}", OWNER="root", '
               f'GROUP="{user}", MODE="0660", TAG-="uaccess", TAG-="seat"\n')
        command("reload-rules", ["udevadm", "control", "--reload-rules"])
        command("trigger-fixture", ["udevadm", "trigger", "--action=change", "--parent-match=" + str(fixture.parent)])
        command("settle-fixture", ["udevadm", "settle", "--timeout=5"])
        baseline = snapshot()
        save(output / "fixture-before.json", dict(parent=str(fixture.parent), hidraw=str(fixture.hidraw), nodes=baseline))
        log = (output / "actor.log").open("xb")
        process = subprocess.Popen([actor_executable, "--owned-guest-token", args.owned_guest_token,
            "--device", str(fixture.hidraw), "--fixture-label", fixture.label, "--output", str(actor)],
            stdout=log, stderr=log, cwd=private, user=account.pw_uid, group=account.pw_gid, extra_groups=[],
            env={**{key: value for key, value in os.environ.items() if key != "MADSENSE_TRACE_NONCE"},
                 "HOME": str(actor), "DOTNET_CLI_HOME": str(actor),
                 **({"MADSENSE_TRACE_NONCE": str(nonce)} if trace_directory is not None else {})})
        state(lambda current: current["paused"])
        action("resume")
        fixture.input_enabled = True
        initial = state(lambda current: current["ready"])
        rows = [row for row in ports().values() if row["localBusId"] != "0-0"]
        require(len(rows) == 1, "Expected one attributed virtual import")
        usb = Path("/sys/bus/usb/devices", rows[0]["localBusId"]).resolve(strict=True)
        raw = wait_for(lambda: owned_nodes(usb, "hidraw", "hidraw*"))
        require(len(raw) == 1, "Ambiguous virtual hidraw interfaces")
        before_metrics = metrics(initial)
        save(output / "metrics-before.json", before_metrics)
        require(before_metrics["metrics"]["InputOverflows"] == 0, "Session overflowed before timing started")
        broker_pid = int(command("broker-pid", ["systemctl", "show", "--value", "--property=MainPID", "madsense-broker.service"]).stdout)
        require(broker_pid > 1, "No active broker")
        result = run(fixture, usb, Path("/dev", raw[0].name), output, executable,
                     process.pid, broker_pid, args.owned_guest_token, rate, nonce=nonce)
        # Freeze the session after the finite C run, including an overflow that
        # may have initiated recovery/reconnect. It is not a functional failure.
        action("pause")
        final = state(lambda current: current["paused"] and not current["releasing"])
        result["metricsAfter"] = metrics(final)
        result["metricsBefore"] = before_metrics
        if result["metricsAfter"]["metrics"]["InputOverflows"] > 0:
            result["result"] = "transport-limit"
        wait_for(idle, 25)
        save(output / "fixture-restored.json", snapshot())
        action("exit")
        require(process.wait(timeout=30) == 0, "Application disposal failed")
        stopped = load(actor / "stopped.json")
        require(stopped and stopped.get("cleanupConfirmed") is True and idle(), "Missing fresh helper/kernel/ACL cleanup confirmation")
        if trace_directory is not None:
            from trace_analysis import analyze as analyze_trace
            with (actor / "report-trace.csv").open(newline="") as trace_stream, (output / "samples.csv").open(newline="") as sample_stream:
                result["attribution"] = analyze_trace(trace_stream, load(actor / "report-trace.json"),
                                                     sample_stream, load(output / "probe.json"))
        else:
            require(not (actor / "report-trace.csv").exists() and not (actor / "report-trace.json").exists(),
                    "Control unexpectedly contains instrumented evidence")
        result["cleanupConfirmed"] = True
    except BaseException as error:
        result["result"] = "invalid"
        result["reason"] = f"{type(error).__name__}: {error}"
    finally:
        safe_to_remove = False
        try:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait(timeout=5)
            if fixture is not None and baseline is not None:
                wait_for(idle, 25)  # Keep fixture alive for authoritative reset.
            safe_to_remove = True
        except BaseException as error:
            result.update(result="invalid", cleanupConfirmed=False, cleanupError=str(error))
        try:
            # Preserve snapshots even if recovery failed. In that case retain
            # the fixture/account/policies inside this disposable VM for diagnosis.
            if actor.exists():
                shutil.copytree(actor, output / "app")
                for path in [output / "app", *(output / "app").rglob("*")]:
                    path.chmod(0o755 if path.is_dir() else 0o644)
            if log is not None:
                log.close()
        except BaseException as error:
            result.update(result="invalid", cleanupConfirmed=False, evidenceError=str(error))
        try:
            require(safe_to_remove, "Retaining owned fixture and test setup after incomplete recovery")
            if fixture is not None:
                fixture.close()
            for path, content in reversed(files):
                require(path.is_file() and path.read_text() == content, "Test policy changed externally")
                path.unlink()
            if files:
                command("restore-rules", ["udevadm", "control", "--reload-rules"])
                command("restore-polkit", ["systemctl", "restart", "polkit.service"])
            if created_user:
                command("delete-actor", ["userdel", user])
            if root_created and private.exists() and private.parent == Path("/opt") and private.name == user:
                shutil.rmtree(private)
        except BaseException as error:
            result.update(result="invalid", cleanupConfirmed=False)
            result.setdefault("cleanupError", str(error))
        save(output / "result.json", result)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--owned-guest-token", required=True)
    parser.add_argument("--baseline-result", type=Path, required=True)
    parser.add_argument("--core-result", type=Path, required=True)
    parser.add_argument("--app-result", type=Path, required=True)
    parser.add_argument("--upgrade-result", type=Path, required=True)
    parser.add_argument("--probe-directory", type=Path, required=True)
    parser.add_argument("--trace-probe-directory", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    require(os.geteuid() == 0, "Root guest observer required")
    scope = validate_environment(args.owned_guest_token)
    require(scope["kind"] == "owned-qemu-guest", "Owned QEMU guest required")
    # Existing verifiers plus idle upgrade/removal are checked before any new
    # account, module, fixture or lease. A zero SSH exit is not sufficient.
    gate_records = verify_paths(args.baseline_result, args.core_result, args.app_result,
                               args.upgrade_result, args.owned_guest_token)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    require(not any(output.iterdir()), "Fresh evidence directory required")
    result = dict(schema=1, result="invalid", environment=scope, functionalGate=False,
                  physicalDevices=False, radioInitialized=False, applicationControllerExercised=True,
                  applicationUiExercised=False, physicalLatencyValidated=False, temporaryVmTestPolicies=True,
                  functionalGates=gate_records, cases=[])
    try:
        executable = build(output)
        if args.trace_probe_directory is None:
            schedule = [(f"{rate}hz", rate, False) for rate in (100, 1000)]
        else:
            from trace_schedule import schedule as paired_schedule
            schedule = paired_schedule()
            result["attributionExperiment"] = dict(kind="paired-control-and-instrumented-copy",
                cases=[name for name, _, _ in schedule], productionTuning=False,
                sourceOverlay=load(args.trace_probe_directory / "research-trace-overlay.json"))
            require(result["attributionExperiment"]["sourceOverlay"], "Missing source overlay provenance")
        for name, rate, instrumented in schedule:
            measured = case(args, output / name, executable, rate,
                            trace_directory=args.trace_probe_directory if instrumented else None)
            if args.trace_probe_directory is not None:
                measured.update(caseName=name, instrumented=instrumented)
                save(output / name / "result.json", measured)
            result["cases"].append(measured)
            require(measured["result"] != "invalid" and measured["cleanupConfirmed"], "Invalid timing evidence or unconfirmed cleanup")
        result["result"] = next((value for value in ("no-samples", "transport-limit", "generator-limit")
                                 if any(item["result"] == value for item in result["cases"])), "measured")
    except BaseException as error:
        result["reason"] = f"{type(error).__name__}: {error}"
    finally:
        save(output / "result.json", result)
    return 0 if result["result"] != "invalid" else 1


if __name__ == "__main__":
    raise SystemExit(main())
