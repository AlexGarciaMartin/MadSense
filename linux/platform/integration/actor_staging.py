"""Executable test-actor staging; called only after the owned-guest root guard."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess


def stage_probe(source, destination, executable):
    """Copy a regular publish tree with explicit read/traverse/execute modes.

    The caller owns the fresh parent. Published source modes and the caller's
    umask must not determine whether the separate ordinary actor can load it.
    """
    if Path(executable).name != executable:
        raise ValueError("A single executable basename is required")
    source, destination = Path(source), Path(destination)
    entries = [source, *sorted(source.rglob("*"))]
    for path in entries:
        mode = path.lstat().st_mode
        if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise ValueError("Publish tree contains a link or special file: " + str(path))
    if not (source / executable).is_file():
        raise ValueError("Missing actor executable")
    destination.mkdir(mode=0o755)
    destination.chmod(0o755)
    for path in entries[1:]:
        target = destination / path.relative_to(source)
        if path.is_dir():
            target.mkdir(mode=0o755)
            target.chmod(0o755)
        else:
            shutil.copyfile(path, target)
            target.chmod(0o755 if target == destination / executable else 0o644)
    return destination / executable


def verify_actor_execution(root, executable, account, evidence):
    """Prove executable staging under the actor UID before any device mutation."""
    root, executable, evidence = Path(root), Path(executable), Path(evidence)
    result = dict(actorUid=account.pw_uid, actorGid=account.pw_gid, result="failed")
    smoke = root / "execution-smoke"
    try:
        for label, path in (("root", root), ("publish", executable.parent), ("executable", executable)):
            info = path.lstat()
            result[label] = dict(path=str(path), uid=info.st_uid, gid=info.st_gid,
                                 mode=oct(stat.S_IMODE(info.st_mode)), device=info.st_dev, inode=info.st_ino)
            if info.st_uid != 0 or stat.S_IMODE(info.st_mode) & 0o022:
                raise RuntimeError("Actor program staging is not root-owned and immutable to the actor")
        result["mountInfo"] = Path("/proc/self/mountinfo").read_text()
        result["filesystemFlags"] = {str(path): os.statvfs(path).f_flag for path in (root, Path("/run"))}
        result["noExecFlag"] = os.ST_NOEXEC
        if os.statvfs(root).f_flag & os.ST_NOEXEC:
            raise RuntimeError("Actor executable staging is on a noexec mount")
        # This smoke has no application behavior and opens no controller nodes.
        # The actual self-contained apphost and its runtime are exercised next.
        shutil.copyfile("/usr/bin/true", smoke)
        smoke.chmod(0o755)
        result["smokeSha256"] = hashlib.sha256(smoke.read_bytes()).hexdigest()
        answer = subprocess.run([str(smoke)], user=account.pw_uid, group=account.pw_gid,
            extra_groups=[], cwd=root, capture_output=True, timeout=10)
        result.update(exitCode=answer.returncode, stdout=answer.stdout.decode(errors="replace"),
                      stderr=answer.stderr.decode(errors="replace"))
        if answer.returncode != 0:
            raise RuntimeError("Ordinary actor cannot execute its staged program")
        result["result"] = "passed"
    except BaseException as error:
        result["reason"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        smoke.unlink(missing_ok=True)
        evidence.write_text(json.dumps(result, indent=2) + "\n")
