"""Build-source overlay for attribution, never a production source mutation.

The caller supplies a fresh directory. Only committed source is exported;
specific hooks are inserted once and the original/changed hashes are retained.
The normal app and functional actors must still come from the untouched checkout.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[3]
PREFIX = "vendor/DualSenseClient/"
TRACE = "DualSenseClient.Emulation.Research.ReportTrace"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError("Research hook no longer has one exact source match: " + old[:100])
    return text.replace(old, new, 1)


def overlays():
    return {
        PREFIX + "DualSenseClient.Controllers/Devices/DualSenseDevice.cs": [
            ("InputReport report = new InputReport(data, offset, usbRaw);",
             "InputReport report = new InputReport(data, offset, usbRaw);\n"
             f"        if (usbRaw is not null) {TRACE}.Record(1, usbRaw);")],
        PREFIX + "DualSenseClient.Emulation/Core/VirtualHidDevice.cs": [
            ("            _rawInputReport = snapshot;",
             f"            _rawInputReport = snapshot;\n            {TRACE}.Record(2, snapshot);")],
        PREFIX + "DualSenseClient.Emulation/UsbIp/UsbIpServer.cs": [
            ("CompleteInterruptLocked(endpoint, generation);", "CompleteInterruptLocked(endpoint, generation, onlyIfFresh);"),
            ("private void CompleteInterruptLocked(InterruptEndpoint endpoint, long generation)",
             "private void CompleteInterruptLocked(InterruptEndpoint endpoint, long generation, bool onlyIfFresh)"),
            ("                int actualLength = Math.Min(count, payload.Length);\n"
             "                SendRetSubmit(transfer.SeqNum, UsbIpStatus.Success, payload.AsMemory(0, actualLength));\n"
             "                Volatile.Write(ref endpoint.LastDeliveredGeneration, generation);",
             "                int actualLength = Math.Min(count, payload.Length);\n"
             f"                {TRACE}.Record(3, payload.AsSpan(0, actualLength), generation, onlyIfFresh ? (byte)2 : (byte)1);\n"
             "                SendRetSubmit(transfer.SeqNum, UsbIpStatus.Success, payload.AsMemory(0, actualLength));\n"
             f"                {TRACE}.Record(4, payload.AsSpan(0, actualLength), generation, onlyIfFresh ? (byte)2 : (byte)1);\n"
             "                Volatile.Write(ref endpoint.LastDeliveredGeneration, generation);")],
        "linux/tests/app-integration/Program.cs": [
            ("        long queued = 0;", f"        {TRACE}.Start(output);\n        long queued = 0;"),
            ("                await bridge.DisposeAsync();",
             f"                await bridge.DisposeAsync();\n                {TRACE}.StopAndSave();")],
    }


def stage(destination, root=ROOT):
    destination = Path(destination).resolve()
    root = Path(root).resolve()
    if destination == root or destination.is_relative_to(root) or root.is_relative_to(destination):
        raise ValueError("The overlay must be outside the source checkout.")
    if destination.exists():
        raise ValueError("Fresh overlay directory required.")
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    data = subprocess.check_output(["git", "archive", "--format=tar", revision], cwd=root)
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        for entry in archive.getmembers():
            if not (entry.isfile() or entry.isdir()):
                raise ValueError("The source overlay does not accept links or special entries.")
        destination.mkdir(parents=True)
        archive.extractall(destination, filter="data")
    changes = {}
    for relative, edits in overlays().items():
        path = destination / relative
        original = path.read_bytes()
        text = original.decode("utf-8").replace("\r\n", "\n")
        for old, new in edits:
            text = replace_once(text, old, new)
        path.write_text(text, encoding="utf-8", newline="\n")
        changes[relative] = dict(originalSha256=digest(original), instrumentedSha256=digest(path.read_bytes()))
    # The recorder is taken from the same committed archive, never an unstaged
    # developer file. Default SDK globs add it only to the copied Emulation tree.
    recorder = destination / "linux/tests/latency/trace/ReportTrace.cs"
    target = destination / PREFIX / "DualSenseClient.Emulation/ResearchReportTrace.cs"
    if target.exists():
        raise ValueError("Research recorder collides with an existing source file.")
    target.write_bytes(recorder.read_bytes())
    changes[target.relative_to(destination).as_posix()] = dict(originalSha256=None,
        instrumentedSha256=digest(target.read_bytes()), copiedFrom=recorder.relative_to(destination).as_posix())
    value = dict(schema=1, sourceCommit=revision, kind="instrumented-research-copy",
                 productionBinary=False, sourceArchiveSha256=digest(data), changes=changes)
    (destination / "research-trace-overlay.json").write_text(json.dumps(value, indent=2) + "\n")
    return value


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    print(json.dumps(stage(options.output), indent=2))
