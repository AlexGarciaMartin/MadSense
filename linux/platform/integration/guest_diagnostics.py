"""Read-only evidence retained after a synthetic actor succeeds or fails."""
import json
from pathlib import Path
import subprocess
import time


def save_broker_diagnostics(output):
    output = Path(output)
    commands = {
        "journal": ["journalctl", "--boot", "--unit=madsense-broker.service", "--no-pager", "--output=short-monotonic"],
        "properties": ["systemctl", "show", "madsense-broker.service", "--property=MainPID,NFileDescriptorStore,Result,ActiveState,SubState"],
    }
    result = dict(monotonicNs=time.monotonic_ns(), commands={})
    for name, command in commands.items():
        try:
            answer = subprocess.run(command, capture_output=True, timeout=10)
            for suffix, value in (("stdout", answer.stdout), ("stderr", answer.stderr)):
                (output / f"broker-final-{name}.{suffix}").write_bytes(value)
            result["commands"][name] = dict(exitCode=answer.returncode)
        except Exception as error:
            result["commands"][name] = dict(error=f"{type(error).__name__}: {error}")
    (output / "broker-final.json").write_text(json.dumps(result, indent=2) + "\n")
