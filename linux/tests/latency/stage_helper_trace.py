"""Exact committed broker copy for the optional owned-VM duration observer."""
import argparse
import io
import json
from pathlib import Path
import re
import subprocess
import tarfile

from stage_trace import ROOT, digest, replace_once

BROKER = "linux/platform/broker.py"
CORE = "linux/platform/broker_core.py"
RECORDER = "linux/tests/latency/helper_trace.py"
TARGET = "linux/platform/research_helper_trace.py"
PAYLOAD_NAMES = {"broker.py", "broker_core.py", "research_helper_trace.py", "research-helper-overlay.json"}


def edits():
    return [
        ('sys.path.insert(0, str(Path(__file__).resolve().parent))',
         'sys.path.insert(0, str(Path(__file__).resolve().parent))\n'
         'from research_helper_trace import initialize as research_initialize, measured as research_measured, dump as research_dump'),
        ('        self.audit_due = 0\n', '        self.audit_due = 0\n        research_initialize(self)\n'),
        *[(f'    def {name}(self):', f'    @research_measured("{name}")\n    def {name}(self):')
          for name in ("verify", "inventory", "check_audit", "audit_foreign", "start_audit")],
        ('                raise RecoveryRequired("Lease cleanup requires systemd restart recovery: " + str(error)) from error',
         '                raise RecoveryRequired("Lease cleanup requires systemd restart recovery: " + str(error)) from error\n'
         '            finally:\n                research_dump(current)'),
    ]


def instrument(source):
    text = source.decode("utf-8").replace("\r\n", "\n")
    for old, new in edits():
        text = replace_once(text, old, new)
    return text.encode("utf-8")


def expected_overlay(original, core, recorder, revision, archive_digest):
    changed = instrument(original)
    return dict(schema=1, kind="research-helper-copy", productionHelper=False, sourceCommit=revision,
        sourceArchiveSha256=archive_digest, decisionsChanged=False,
        changes={BROKER: dict(originalSha256=digest(original), instrumentedSha256=digest(changed)),
                 TARGET: dict(originalSha256=None, instrumentedSha256=digest(recorder), copiedFrom=RECORDER)},
        files={"broker.py": digest(changed), "broker_core.py": digest(core), "research_helper_trace.py": digest(recorder)})


def verify_transform(directory, value, original, core, recorder, *, revision, archive_digest):
    directory = Path(directory)
    expected = expected_overlay(original, core, recorder, revision, archive_digest)
    if value != expected or set(path.name for path in directory.iterdir()) != PAYLOAD_NAMES:
        raise ValueError("Helper overlay differs from the exact approved committed transformation")
    for name, data in (("broker.py", instrument(original)), ("broker_core.py", core), ("research_helper_trace.py", recorder)):
        path = directory / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size != len(data) or path.read_bytes() != data:
            raise ValueError("Helper payload is not the approved source copy: " + name)
    return expected


def verify_overlay(directory, revision, root=ROOT):
    if not re.fullmatch("[0-9a-f]{40}", revision):
        raise ValueError("Exact source commit required")
    directory, root = Path(directory), Path(root)
    metadata = directory / "research-helper-overlay.json"
    if metadata.is_symlink() or not metadata.is_file() or metadata.stat().st_size > 65536:
        raise ValueError("Invalid helper overlay metadata file")
    value = json.loads(metadata.read_text())
    sources = [subprocess.check_output(["git", "show", revision + ":" + name], cwd=root)
               for name in (BROKER, CORE, RECORDER)]
    archive = subprocess.check_output(["git", "archive", "--format=tar", revision], cwd=root)
    return verify_transform(directory, value, *sources, revision=revision, archive_digest=digest(archive))


def stage(destination, root=ROOT):
    root, destination = Path(root).resolve(), Path(destination).resolve()
    if destination.exists() or destination.is_relative_to(root) or root.is_relative_to(destination):
        raise ValueError("Fresh source-copy directory outside the checkout required")
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    data = subprocess.check_output(["git", "archive", "--format=tar", revision], cwd=root)
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        for member in archive.getmembers():
            if not (member.isfile() or member.isdir()):
                raise ValueError("Only regular committed source entries accepted")
        destination.mkdir(parents=True)
        archive.extractall(destination, filter="data")
    source = (destination / BROKER).read_bytes()
    changed = instrument(source)
    (destination / BROKER).write_bytes(changed)
    target = destination / TARGET
    if target.exists():
        raise ValueError("Research helper recorder path already exists")
    target.write_bytes((destination / RECORDER).read_bytes())
    value = expected_overlay(source, (destination / CORE).read_bytes(), target.read_bytes(), revision, digest(data))
    (destination / "research-helper-overlay.json").write_text(json.dumps(value, indent=2) + "\n")
    return value


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    print(json.dumps(stage(options.output), indent=2))
