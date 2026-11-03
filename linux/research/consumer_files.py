"""Private configuration writes executed only by the disposable consumer UID."""
import json
import os
from pathlib import Path

from consumer_session import require


# The inherited pipe carries cookie/configuration bytes; argv, environment and
# command evidence never contain those bytes. All path traversal and writes
# happen after ConsumerSession has dropped UID/GID and supplementary groups.
WRITE_PROGRAM = r'''import json, os, pathlib, stat, sys
uid, gid = os.geteuid(), os.getegid()
if uid == 0 or gid == 0:
    raise RuntimeError("Private configuration writer requires ordinary UID/GID")
home, relative, pipe, operation = sys.argv[1:]
parts = pathlib.PurePosixPath(relative).parts
if not parts or pathlib.PurePosixPath(relative).is_absolute() or any(p in (".", "..") for p in parts):
    raise RuntimeError("Invalid private relative path")
with os.fdopen(int(pipe), "rb") as source:
    data = source.read(4097)
if len(data) > 4096 or operation not in ("create", "replace"):
    raise RuntimeError("Invalid private content or operation")
flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
directory = os.open(home, flags)
try:
    identity = os.fstat(directory)
    if (identity.st_uid, identity.st_gid) != (uid, gid):
        raise RuntimeError("Private home ownership changed")
    for part in parts[:-1]:
        try:
            os.mkdir(part, 0o700, dir_fd=directory)
        except FileExistsError:
            pass
        child = os.open(part, flags, dir_fd=directory)
        identity = os.fstat(child)
        if (identity.st_uid, identity.st_gid) != (uid, gid):
            os.close(child)
            raise RuntimeError("Private directory ownership changed")
        os.fchmod(child, 0o700)
        os.close(directory)
        directory = child
    flags = os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
    if operation == "create":
        flags |= os.O_CREAT | os.O_EXCL
    target = os.open(parts[-1], flags, 0o600, dir_fd=directory)
    try:
        identity = os.fstat(target)
        if not stat.S_ISREG(identity.st_mode) or identity.st_nlink != 1 or (identity.st_uid, identity.st_gid) != (uid, gid):
            raise RuntimeError("Private file is not one exclusively owned regular inode")
        if operation == "replace" and stat.S_IMODE(identity.st_mode) != 0o600:
            raise RuntimeError("Private file permissions changed")
        os.fchmod(target, 0o600)
        os.ftruncate(target, 0)
        with os.fdopen(target, "wb", closefd=False) as stream:
            stream.write(data)
    finally:
        os.close(target)
finally:
    os.close(directory)
print(json.dumps(dict(bytes=len(data), uid=uid, gid=gid, mode="0600", operation=operation)))
'''


def write_private_file(session, path, content, *, replace=False):
    home = session.root / "home"
    path = Path(path)
    require(path.is_absolute() and path.is_relative_to(home), "Private file must be inside the owned home")
    relative = path.relative_to(home)
    require(relative.parts and not any(part in (".", "..") for part in relative.parts), "Invalid private file path")
    data = content if isinstance(content, bytes) else content.encode("utf-8")
    require(len(data) <= 4096, "Private configuration exceeds its 4096-byte budget")
    read_fd, write_fd = os.pipe()
    try:
        # One nonblocking write into a new empty pipe is bounded by PIPE_BUF.
        os.set_blocking(write_fd, False)
        require(os.write(write_fd, data) == len(data), "Incomplete private configuration pipe")
        os.close(write_fd); write_fd = None
        sequence = getattr(session, "private_file_sequence", 0) + 1
        session.private_file_sequence = sequence
        operation = "replace" if replace else "create"
        _, output = session.command(f"private-config-{sequence}",
            ["python3", "-c", WRITE_PROGRAM, home, relative.as_posix(), str(read_fd), operation],
            pass_fds=(read_fd,), timeout=10)
        report = json.loads(output)
        require(report == dict(bytes=len(data), uid=session.uid, gid=session.gid, mode="0600", operation=operation),
                "Private configuration writer returned unexpected ownership or size")
    finally:
        os.close(read_fd)
        if write_fd is not None:
            os.close(write_fd)
