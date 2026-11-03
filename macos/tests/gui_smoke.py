"""Bounded native AppKit startup/shutdown test; snapshots only the app's view."""
import os
import pathlib
import signal
import subprocess
import sys

app = pathlib.Path(sys.argv[1]).resolve()
output = pathlib.Path(sys.argv[2]).resolve()
output.mkdir(parents=True, exist_ok=True)
cases = [(language, language, False, False) for language in ("en", "es", "fr", "de", "it", "pt")]
cases.append(("stalled", "en", True, False))
cases.append(("paused-restart", "en", False, True))
for name, language, stalled, paused_restart in cases:
    screenshot = output / f"ui-{name}.png"
    error_log = output / f"gui-{name}.stderr"
    environment = dict(os.environ, MADSENSE_SMOKE_SCREENSHOT=str(screenshot),
                       MADSENSE_SMOKE_LANGUAGE=language,
                       MADSENSE_SMOKE_STALL_BACKEND="1" if stalled else "0",
                       MADSENSE_SMOKE_PAUSED_RESTART="1" if paused_restart else "0")
    with (output / f"gui-{name}.stdout").open("w") as stdout, error_log.open("w") as stderr:
        process = subprocess.Popen([str(app), "--smoke-test"], stdout=stdout, stderr=stderr,
                                   env=environment, start_new_session=True)
        try:
            try:
                result = process.wait(timeout=25)
            except subprocess.TimeoutExpired:
                subprocess.run(["sample", str(process.pid), "2", "-file", str(output / f"gui-{name}-sample.stdout")],
                               timeout=8, check=False, capture_output=True)
                raise
            assert result == 0, f"GUI exit status {result}; see {error_log.name}"
            assert screenshot.is_file(), "The live window snapshot was not produced"
            log = error_log.read_text()
            assert "applicationWillTerminate" in log, "Normal AppKit termination was not observed"
            if stalled:
                assert "SIGSTOP" in log and "sending SIGKILL" in log, "The real stalled backend did not exercise bounded forced cleanup"
            elif paused_restart:
                assert "paused restart verified" in log, "Automatic recovery did not preserve the requested pause from its first real status"
                assert "sending SIGKILL" not in log, "Paused recovery unexpectedly required forced backend termination"
            else:
                assert "sending SIGTERM" not in log and "sending SIGKILL" not in log, "Normal quit required forced backend termination"
            remaining = subprocess.run(["pgrep", "-f", str(app.parent.parent / "Resources/backend/MadSense.Backend") + " --service"],
                                       text=True, capture_output=True, timeout=5)
            assert remaining.returncode == 1, "Backend remained running after normal GUI termination"
            print(f"PASS {name}: native GUI startup, live window rendering, {'stalled child cleanup' if stalled else 'normal shutdown'} and backend release", flush=True)
        finally:
            # This test created its own process group, including any backend child.
            # Never signal another application's process group or search by generic name.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=5)

# These explicitly labelled fixtures only verify rendering of long localized
# pause/error states. They do not create a controller or validate hardware.
for state in ("paused", "blocked"):
    for language in ("en", "es", "fr", "de", "it", "pt"):
        name = f"fixture-{state}-{language}"
        screenshot = output / f"ui-{name}.png"
        error_log = output / f"gui-{name}.stderr"
        environment = dict(os.environ, MADSENSE_SMOKE_SCREENSHOT=str(screenshot),
                           MADSENSE_SMOKE_LANGUAGE=language, MADSENSE_VISUAL_FIXTURE=state)
        with (output / f"gui-{name}.stdout").open("w") as stdout, error_log.open("w") as stderr:
            result = subprocess.run([str(app), "--smoke-test", "--visual-fixture"],
                                    stdout=stdout, stderr=stderr, env=environment, timeout=10)
        log = error_log.read_text()
        assert result.returncode == 0 and screenshot.is_file(), f"Missing visual fixture: {name}"
        assert "visual fixture only:" in log and "no backend or hardware" in log, name
        assert "backend startup requested" not in log, "Visual fixture unexpectedly started a bridge"
        assert "applicationWillTerminate" in log and "snapshot saved:" in log, name
        print(f"PASS {name}: translated visual fixture only; no hardware", flush=True)
