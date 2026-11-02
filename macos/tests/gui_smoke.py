"""Bounded native AppKit startup/shutdown test; snapshots only the app's view."""
import os
import pathlib
import signal
import subprocess
import sys

app = pathlib.Path(sys.argv[1]).resolve()
output = pathlib.Path(sys.argv[2]).resolve()
output.mkdir(parents=True, exist_ok=True)
environment = dict(os.environ, MADSENSE_SMOKE_SCREENSHOT=str(output / "ui.png"))
with (output / "gui.stdout").open("w") as stdout, (output / "gui.stderr").open("w") as stderr:
    process = subprocess.Popen([str(app), "--smoke-test"], stdout=stdout, stderr=stderr,
                               env=environment, start_new_session=True)
    try:
        try:
            result = process.wait(timeout=25)
        except subprocess.TimeoutExpired:
            subprocess.run(["sample", str(process.pid), "2", "-file", str(output / "gui-sample.stdout")],
                           timeout=8, check=False, capture_output=True)
            raise
        assert result == 0, f"GUI exit status {result}; see gui.stderr"
        assert (output / "ui.png").is_file(), "The live window snapshot was not produced"
        remaining = subprocess.run(["pgrep", "-f", str(app.parent.parent / "Resources/backend/MadSense.Backend") + " --service"],
                                   text=True, capture_output=True, timeout=5)
        assert remaining.returncode == 1, "Backend remained running after normal GUI termination"
        print("PASS native GUI startup, live window rendering, normal shutdown and backend release")
    finally:
        # This test created its own process group, including any backend child.
        # Never signal another application's process group or search by generic name.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)
