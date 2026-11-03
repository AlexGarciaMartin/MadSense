"""Linux AC0 iInterface only, in a fresh copy of one committed source tree."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[4]
DEVICE = "vendor/DualSenseClient/DualSenseClient.Emulation/Devices/DualSense/DualSenseDevice.cs"
HELPER = "linux/tests/audio-hotplug/export/AudioLeaseDescriptor.cs"
TARGET = "vendor/DualSenseClient/DualSenseClient.Emulation/ResearchAudioLeaseDescriptor.cs"
OLD = ": base(edge ? DualSenseEdgeDescriptorSet.Create(identity) : DualSenseDescriptorSet.Create(identity))"
NEW = ": base(DualSenseClient.Emulation.Research.AudioLeaseDescriptor.Apply(edge ? DualSenseEdgeDescriptorSet.Create(identity) : DualSenseDescriptorSet.Create(identity)))"

def digest(data): return hashlib.sha256(data).hexdigest()

def transform(source):
    if source.count(OLD) != 1: raise ValueError("Expected one exact persona descriptor construction")
    return source.replace(OLD, NEW)

def verify_overlay(value, original, helper):
    if (value.get("schema") != 1 or value.get("productionBinary") is not False
        or value.get("kind") != "audio-interface-research-copy"
        or not re.fullmatch(r"[0-9a-f]{40}", value.get("sourceCommit", ""))
        or not re.fullmatch(r"[0-9a-f]{64}", value.get("sourceArchiveSha256", ""))):
        raise ValueError("Unexpected audio overlay provenance")
    modified = transform(original.decode().replace("\r\n", "\n")).encode()
    expected = {
        DEVICE: dict(originalSha256=digest(original), instrumentedSha256=digest(modified)),
        TARGET: dict(originalSha256=None, instrumentedSha256=digest(helper), copiedFrom=HELPER)}
    if value.get("changes") != expected: raise ValueError("Audio overlay changes differ from exact committed source")
    return True

def stage(destination, root=ROOT):
    root, destination = Path(root).resolve(), Path(destination).resolve()
    if destination.exists() or root == destination or root.is_relative_to(destination) or destination.is_relative_to(root):
        raise ValueError("Fresh research copy outside checkout required")
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    archive_bytes = subprocess.check_output(["git", "archive", "--format=tar", revision], cwd=root)
    with tarfile.open(fileobj=io.BytesIO(archive_bytes)) as archive:
        if any(not (row.isfile() or row.isdir()) for row in archive): raise ValueError("Special archive entries refused")
        destination.mkdir(parents=True)
        archive.extractall(destination, filter="data")
    path = destination / DEVICE; before = path.read_bytes()
    path.write_text(transform(before.decode().replace("\r\n", "\n")), encoding="utf-8", newline="\n")
    target = destination / TARGET
    if target.exists(): raise ValueError("Existing research descriptor helper")
    target.write_bytes((destination / HELPER).read_bytes())
    result = dict(schema=1, sourceCommit=revision, sourceArchiveSha256=digest(archive_bytes),
        productionBinary=False, kind="audio-interface-research-copy", changes={
            DEVICE: dict(originalSha256=digest(before), instrumentedSha256=digest(path.read_bytes())),
            TARGET: dict(originalSha256=None, instrumentedSha256=digest(target.read_bytes()), copiedFrom=HELPER)})
    (destination / "research-audio-overlay.json").write_text(json.dumps(result, indent=2)+"\n")
    return result

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--output", type=Path, required=True)
    print(json.dumps(stage(parser.parse_args().output), indent=2))
