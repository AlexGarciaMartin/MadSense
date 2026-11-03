"""Exercise the packaged process protocol on macOS without a controller."""
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time


def start(executable, environment, arguments=()):
    process = subprocess.Popen([executable, "--service", *arguments], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, bufsize=1, env=environment)
    messages = queue.Queue()

    def collect():
        try:
            for line in process.stdout:
                try:
                    messages.put(json.loads(line))
                except json.JSONDecodeError:
                    messages.put({"invalid": line})
        finally:
            messages.put({"eof": True})

    threading.Thread(target=collect, daemon=True).start()
    # Drain errors too, so a diagnostic burst cannot deadlock the tested process.
    threading.Thread(target=lambda: [sys.stderr.write(line) for line in process.stderr], daemon=True).start()
    return process, messages


def expect(messages, state, speaker=None):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        value = messages.get(timeout=max(0.01, deadline - time.monotonic()))
        print(json.dumps(value), flush=True)
        assert "invalid" not in value, "stdout must contain only JSON lines"
        assert "eof" not in value, "backend exited before the expected state"
        if value.get("state") == state and (speaker is None or value.get("speaker") == speaker):
            return value
    raise AssertionError(f"Timed out waiting for {state}")


def command(process, value):
    process.stdin.write(value + "\n")
    process.stdin.flush()


def expect_first(messages, state, speaker):
    # Unlike expect(), this must not skip an earlier waiting/connecting/error:
    # --start-paused promises that discovery cannot run before the first status.
    value = messages.get(timeout=15)
    print(json.dumps(value), flush=True)
    assert value.get("state") == state, f"First status was not {state}: {value}"
    assert value.get("speaker") == speaker
    return value


def run(executable, environment, home):
    preferences = home / "Library" / "Application Support" / "MadSense" / "preferences.json"
    preferences.parent.mkdir(parents=True)
    preferences.write_text(json.dumps({"language": "es", "future": {"enabled": True}}))
    process, messages = start(executable, environment)
    try:
        initial = expect(messages, "waiting", "high")
        assert initial["capabilities"]["hardwareValidated"] is False
        command(process, "pause")
        expect(messages, "paused", "high")
        duplicate = subprocess.run([executable, "--service"], input="", text=True,
                                   capture_output=True, timeout=15, env=environment)
        assert duplicate.returncode != 0, "a second bridge acquired the instance lock"
        assert any(json.loads(line).get("state") == "error" for line in duplicate.stdout.splitlines())
        for level in ("low", "off", "high", "off"):
            command(process, "speaker " + level)
            expect(messages, "paused", level)
            saved = json.loads(preferences.read_text())
            assert saved["speaker"] == level, "speaker preference was not persisted outside the bundle"
            assert saved["future"]["enabled"] is True and saved["language"] == "es"
        before_invalid = preferences.read_text()
        command(process, "speaker low extra")
        command(process, "diagnose")  # Command-order barrier after the rejected setting.
        diagnostic = expect(messages, "diagnostic", "off")
        assert diagnostic["bridgeState"] == "paused"
        assert diagnostic["detail"] == "Controller released."
        assert diagnostic["capabilities"]["nativeAudio"] is False
        assert preferences.read_text() == before_invalid, "invalid command changed persisted preferences"
        command(process, "resume")
        expect(messages, "waiting", "off")
        command(process, "quit")
        expect(messages, "stopped", "off")
        assert process.wait(timeout=15) == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)

    process, messages = start(executable, environment)
    try:
        expect(messages, "waiting", "off")
        command(process, "speaker high")
        expect(messages, "waiting", "high")
        process.stdin.close()
        expect(messages, "stopped", "high")
        assert process.wait(timeout=15) == 0, "parent pipe closure did not stop backend"
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
    before_restart = preferences.read_text()
    for arguments, expected_state in ((["--start-paused"], "paused"),
                                      (["--start-paused"], "paused"), ([], "waiting")):
        process, messages = start(executable, environment, arguments)
        try:
            first = expect_first(messages, expected_state, "high")
            assert first["reports"] == 0 and first["battery"] == -1
            if expected_state == "paused":
                assert first["detail"] == "Controller released."
                command(process, "resume")
                expect(messages, "waiting", "high")
            command(process, "quit")
            expect(messages, "stopped", "high")
            assert process.wait(timeout=15) == 0
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
    assert preferences.read_text() == before_restart, "startup pause must not mutate user preferences"
    print("PASS service speaker persistence, initial/restarted pause, resume, diagnostics, single instance, quit, and parent EOF")


def main(executable):
    # Exercise real per-user paths without changing the runner/user's preferences.
    with tempfile.TemporaryDirectory(prefix="madsense-service-") as temporary:
        environment = os.environ.copy()
        environment["HOME"] = temporary
        run(executable, environment, Path(temporary))


if __name__ == "__main__":
    main(sys.argv[1])
