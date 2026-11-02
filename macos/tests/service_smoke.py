"""Exercise the packaged process protocol on macOS without a controller."""
import json
import queue
import subprocess
import sys
import threading
import time


def start(executable):
    process = subprocess.Popen([executable, "--service"], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, bufsize=1)
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


def expect(messages, state):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        value = messages.get(timeout=max(0.01, deadline - time.monotonic()))
        print(json.dumps(value), flush=True)
        assert "invalid" not in value, "stdout must contain only JSON lines"
        assert "eof" not in value, "backend exited before the expected state"
        if value.get("state") == state:
            return value
    raise AssertionError(f"Timed out waiting for {state}")


def command(process, value):
    process.stdin.write(value + "\n")
    process.stdin.flush()


def main(executable):
    process, messages = start(executable)
    try:
        initial = expect(messages, "waiting")
        assert initial["capabilities"]["hardwareValidated"] is False
        command(process, "pause")
        expect(messages, "paused")
        duplicate = subprocess.run([executable, "--service"], input="", text=True,
                                   capture_output=True, timeout=15)
        assert duplicate.returncode != 0, "a second bridge acquired the instance lock"
        assert any(json.loads(line).get("state") == "error" for line in duplicate.stdout.splitlines())
        command(process, "diagnose")
        expect(messages, "diagnostic")
        command(process, "resume")
        expect(messages, "waiting")
        command(process, "quit")
        expect(messages, "stopped")
        assert process.wait(timeout=15) == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)

    process, messages = start(executable)
    try:
        expect(messages, "waiting")
        process.stdin.close()
        expect(messages, "stopped")
        assert process.wait(timeout=15) == 0, "parent pipe closure did not stop backend"
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
    print("PASS service pause/resume, diagnostics, single instance, quit, and parent EOF")


if __name__ == "__main__":
    main(sys.argv[1])
