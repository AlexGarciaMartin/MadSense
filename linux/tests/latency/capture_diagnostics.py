"""Bounded, read-only evidence collected after an owned guest's timed window.

Collection failure is diagnostic evidence, never a cleanup ACK or benchmark gate.
No process other than an exact child started by this module is signalled.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time


LIMIT = 1024 * 1024
TIMEOUT = 10
COMMANDS = (
    ("broker-journal", ("journalctl", "--boot", "--unit=madsense-broker.service",
                        "--no-pager", "--output=json", "--lines=400")),
    ("udev-journal", ("journalctl", "--boot", "--unit=systemd-udevd.service",
                      "--no-pager", "--output=json", "--lines=400")),
    ("kernel-journal", ("journalctl", "--boot", "--dmesg", "--no-pager",
                        "--output=json", "--lines=400")),
    ("broker-state", ("systemctl", "show", "madsense-broker.service",
                       "--property=MainPID", "--property=ActiveState",
                       "--property=SubState", "--property=Result")),
)


def _owned_guest(token):
    if sys.platform != "linux" or os.geteuid() != 0 or not token:
        raise RuntimeError("Root in the explicitly owned QEMU guest is required")
    # Import only when called; isolated payload imports do not execute a guard.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "research"))
    from ci_environment import validate_environment
    environment = validate_environment(token)
    if environment.get("kind") != "owned-qemu-guest":
        raise RuntimeError("Diagnostics require the explicitly owned QEMU guest")
    return environment


def _capture_command(output, label, argv, *, timeout=TIMEOUT, limit=LIMIT):
    """Drain both pipes concurrently with bounded memory and an exact child handle."""
    record = dict(argv=list(argv), startedMonotonicNs=time.monotonic_ns(),
                  startedRealtimeNs=time.time_ns(), timeoutSeconds=timeout,
                  limitBytesPerStream=limit, timedOut=False, outputLimitReached=False,
                  childReaped=False, errors=[])
    buffers = {name: bytearray() for name in ("stdout", "stderr")}
    truncated = {name: False for name in buffers}
    exceeded = threading.Event()
    process = None
    readers = []

    def drain(name, stream):
        try:
            while data := stream.read1(65536):
                remaining = limit - len(buffers[name])
                buffers[name].extend(data[:remaining])
                if len(data) > remaining:
                    truncated[name] = True
                    exceeded.set()
        except OSError as error:
            record["errors"].append(f"{name}: {error}")
        finally:
            stream.close()

    try:
        process = subprocess.Popen(list(argv), stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   close_fds=True)
        record["childPid"] = process.pid
        for name in buffers:
            thread = threading.Thread(target=drain, args=(name, getattr(process, name)), daemon=True)
            readers.append(thread)
            thread.start()
        deadline = time.monotonic() + timeout
        while process.poll() is None:
            if exceeded.wait(min(0.02, max(0, deadline - time.monotonic()))):
                record["outputLimitReached"] = True
                process.kill()
                break
            if time.monotonic() >= deadline:
                record["timedOut"] = True
                process.kill()
                break
        record["returncode"] = process.wait(timeout=2)
        record["childReaped"] = True
    except (OSError, subprocess.SubprocessError) as error:
        record["errors"].append(str(error))
    finally:
        if process is not None and process.poll() is None:
            try:
                process.kill()
                record["returncode"] = process.wait(timeout=2)
                record["childReaped"] = True
            except (OSError, subprocess.SubprocessError) as error:
                record["errors"].append(f"child cleanup: {error}")
        for thread in readers:
            thread.join(timeout=2)
        if any(thread.is_alive() for thread in readers):
            # The fixed commands have no descendants. Still retain a bounded
            # failure rather than blocking cleanup if an unexpected pipe stays open.
            record["errors"].append("A diagnostic stream did not close after child exit")
        record["outputLimitReached"] |= exceeded.is_set()
        record["endedMonotonicNs"] = time.monotonic_ns()

    for name, data in buffers.items():
        contents = bytes(data)
        path = output / f"{label}.{name}"
        with path.open("xb") as stream:
            stream.write(contents)
        path.chmod(0o644)
        record[name] = dict(path=path.name, size=len(contents),
                            sha256=hashlib.sha256(contents).hexdigest(), truncated=truncated[name])
    record["complete"] = (record.get("returncode") == 0 and record["childReaped"]
                          and not record["timedOut"] and not record["outputLimitReached"]
                          and not record["errors"])
    return record


def capture_diagnostics(output, owned_guest_token):
    """Return capture status; never replace the experiment's original result."""
    result = dict(schema=1, readOnly=True, afterTimedWindow=True,
                  diagnosticsAreCausalProof=False, complete=False, commands={}, errors=[])
    created = False
    try:
        result["environment"] = _owned_guest(owned_guest_token)
        output = Path(output)
        # The coordinator supplies a fresh path outside the ordinary actor tree.
        # Exclusive creation prevents overwriting prior or symlinked evidence.
        output.mkdir(mode=0o700)
        created = True
        for label, argv in COMMANDS:
            try:
                result["commands"][label] = _capture_command(output, label, argv)
            except Exception as error:
                result["errors"].append(f"{label}: {type(error).__name__}: {error}")
        result["complete"] = (len(result["commands"]) == len(COMMANDS)
                              and all(item["complete"] for item in result["commands"].values())
                              and not result["errors"])
        with (output / "result.json").open("x") as stream:
            json.dump(result, stream, indent=2)
            stream.write("\n")
        (output / "result.json").chmod(0o644)
    except Exception as error:
        result["complete"] = False
        result["errors"].append(f"{type(error).__name__}: {error}")
    finally:
        if created:
            try:
                # The host collector uses the guest's ordinary SSH account.
                # Publish only our exclusively created evidence after capture.
                output.chmod(0o755)
            except OSError as error:
                result["complete"] = False
                result["errors"].append(f"publish diagnostics: {error}")
    return result
