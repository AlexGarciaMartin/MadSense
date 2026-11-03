"""Test the packaged read-only helper and real AppKit diagnostic flow.

No controller, audio engine, HID output, or virtual device is exercised. The
backend uses its actual --start-paused protocol, including on a hardware runner.
"""
import json
from datetime import datetime
import os
import pathlib
import re
import signal
import subprocess
import sys


def main():
    app = pathlib.Path(sys.argv[1]).resolve()
    output = pathlib.Path(sys.argv[2]).resolve()
    output.mkdir(parents=True, exist_ok=True)
    helper = app.parent / "MadSenseNativeProbe"
    backend = app.parent.parent / "Resources/backend/MadSense.Backend"
    assert helper.is_file(), "Packaged read-only helper is missing"

    for name, command in (
        ("result-parser", [str(app), "--inventory-parser-self-test"]),
        ("probe-parser", [str(helper), "--self-test"]),
    ):
        result = subprocess.run(command, capture_output=True, text=True, timeout=10)
        (output / f"native-inventory-{name}.stdout").write_text(result.stdout)
        (output / f"native-inventory-{name}.stderr").write_text(result.stderr)
        assert result.returncode == 0, f"{name}: {result.stderr}"
        assert "PASS" in result.stdout and "No hardware exercised." in result.stdout, name
        print(f"PASS {name}: packaged parser self-test only; no hardware", flush=True)

    cases = [(language, language, False, False) for language in ("en", "es", "fr", "de", "it", "pt")]
    cases += [("timeout", "en", True, False), ("quit-pending", "en", True, True)]
    for name, language, stalled, quit_pending in cases:
        screenshot = output / f"native-inventory-{name}.png"
        exported = output / f"native-inventory-{name}.json"
        error_log = output / f"native-inventory-{name}.stderr"
        # Refuse stale results rather than accidentally accepting a previous run.
        assert not screenshot.exists() and not exported.exists(), f"Output already exists for {name}"
        environment = dict(os.environ, MADSENSE_SMOKE_SCREENSHOT=str(screenshot),
                           MADSENSE_SMOKE_LANGUAGE=language,
                           MADSENSE_SMOKE_DIAGNOSTIC_EXPORT=str(exported),
                           MADSENSE_SMOKE_INVENTORY_STALL="1" if stalled else "0",
                           MADSENSE_SMOKE_INVENTORY_QUIT="1" if quit_pending else "0",
                           MADSENSE_SMOKE_STALL_BACKEND="0", MADSENSE_SMOKE_PAUSED_RESTART="0")
        with (output / f"native-inventory-{name}.stdout").open("w") as stdout, error_log.open("w") as stderr:
            process = subprocess.Popen([str(app), "--smoke-test", "--native-inventory-smoke"],
                                       stdout=stdout, stderr=stderr, env=environment, start_new_session=True)
            try:
                status = process.wait(timeout=35)
                assert status == 0, f"{name}: application exit {status}; see {error_log.name}"
                log = error_log.read_text()
                assert "applicationWillTerminate" in log, f"{name}: normal AppKit termination missing"
                assert "native inventory active at termination: false" in log, f"{name}: helper still active"
                assert "backend first status: paused; requested pause=true" in log, f"{name}: backend was not paused from startup"
                assert "backend quit grace expired" not in log, f"{name}: normal backend quit needed a signal"
                if stalled:
                    assert "native inventory smoke SIGSTOP result: 0" in log, name
                    assert "native inventory sending SIGTERM to owned child:" in log, name
                    # Darwin can deliver default-action SIGTERM to a stopped
                    # Swift helper. Escalation is only needed if it survives
                    # the TERM grace period, unlike the managed backend test.
                else:
                    assert "sending SIGTERM" not in log and "sending SIGKILL" not in log, name
                if quit_pending:
                    assert "requesting Quit with helper pending" in log, name
                    assert "native inventory completed: cancelled; cleanup complete=true" in log, name
                    assert not exported.exists(), "Quit test unexpectedly exported an unfinished inventory"
                else:
                    assert "native inventory smoke export saved; backend remains paused" in log, name
                    assert screenshot.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"), name
                    record = json.loads(exported.read_text())
                    assert record["schema"] == 1 and record["hardware_validation_performed"] is False, name
                    assert record["backend_running"] is True and record["native_inventory_in_progress"] is False, name
                    bridge = record["latest_backend_diagnostic"]
                    state = record["latest_backend_status"]
                    assert bridge["value"]["bridgeState"] == "paused" and state["value"]["state"] == "paused", name
                    assert bridge["session_id"] == state["session_id"] == record["backend_session_id"], name
                    native = record["native_inventory"]
                    assert native["source"] == "MadSenseNativeProbe" and native["cleanup_complete"] is True, name
                    assert native["pipes_drained"] is True and native["output_truncated"] is False, name
                    assert native["hardware_validation_performed"] is False, name
                    assert native["outcome"] == ("timed_out" if stalled else "complete"), native
                    elapsed = (datetime.fromisoformat(native["finished_at"]) -
                               datetime.fromisoformat(native["started_at"])).total_seconds()
                    assert 0 <= elapsed <= 16, f"{name}: helper exceeded its 15-second cleanup bound (timestamps round to seconds)"
                    if stalled:
                        assert elapsed >= 9, "Timeout fired before its ten-second deadline"
                        escalated = "native inventory sending SIGKILL to owned child:" in log
                        assert native["termination_reason"] == "uncaught_signal", native
                        assert native["exit_code"] == (signal.SIGKILL if escalated else signal.SIGTERM), native
                    if not stalled:
                        inventory = native["inventory"]
                        assert inventory == json.loads(native["stdout"]), "Original structured evidence was not preserved"
                        assert inventory["mode"] == "read-only physical-controller inventory", name
                        assert inventory["observed_hardware_behavior"] is False and inventory["virtual_hid_created"] is False, name
                        assert inventory["enumeration_complete"] is True, inventory
                        assert type(inventory["physical_dualsense_found"]) in (bool, type(None)), name
                        assert all(isinstance(inventory[key], list) for key in ("hid", "usb_audio", "game_controller", "issues")), name
                for executable in (helper, backend):
                    remaining = subprocess.run(["pgrep", "-f", re.escape(str(executable))],
                                               capture_output=True, text=True, timeout=5)
                    assert remaining.returncode == 1, f"{name}: child remained running: {executable.name}"
                print(f"PASS native-inventory-{name}: real diagnostic lifecycle and bounded cleanup; hardware unverified", flush=True)
            finally:
                # Only this test's new process group; never signal a PID found
                # by name or another user's running MadSense instance.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=5)


if __name__ == "__main__":
    main()
