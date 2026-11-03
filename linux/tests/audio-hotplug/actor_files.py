"""All traversal of the app-owned tree runs as that ordinary app UID."""
import base64
import json
from pathlib import Path, PurePosixPath
import subprocess


PROGRAM = r'''import base64, json, os, stat, sys
if os.geteuid() == 0 or os.getegid() == 0:
    raise RuntimeError("App file operations require an ordinary UID/GID")
root, operation = sys.argv[1:]
request = json.load(sys.stdin)
directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
def checked(fd, directory=False):
    info = os.fstat(fd)
    if (info.st_uid, info.st_gid) != (os.geteuid(), os.getegid()):
        raise RuntimeError("App inode ownership changed")
    if directory:
        if not stat.S_ISDIR(info.st_mode): raise RuntimeError("Expected directory")
    elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 8*1024*1024:
        raise RuntimeError("Expected one bounded ordinary file")
def read_file(directory, name):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=directory)
    try:
        checked(fd)
        with os.fdopen(fd, "rb", closefd=False) as stream: value = stream.read(8*1024*1024+1)
        if len(value) > 8*1024*1024: raise RuntimeError("App file exceeds budget")
        return value
    finally: os.close(fd)
directory = os.open(root, directory_flags)
try:
    checked(directory, True)
    if operation == "command":
        child = os.open("commands", directory_flags, dir_fd=directory)
        try:
            checked(child, True)
            sequence = request["sequence"]
            if type(sequence) is not int or not 1 <= sequence <= 100: raise RuntimeError("Invalid command sequence")
            if request["operation"] not in ("resume", "pause", "exit"): raise RuntimeError("Invalid command")
            target = f"{sequence:06}.json"; temporary = target + ".tmp"
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=child)
            try:
                checked(fd)
                with os.fdopen(fd, "wb", closefd=False) as stream:
                    stream.write(json.dumps(dict(operation=request["operation"])).encode())
                    stream.flush(); os.fsync(fd)
            finally: os.close(fd)
            os.rename(temporary, target, src_dir_fd=child, dst_dir_fd=child)
            result = dict(written=target)
        finally: os.close(child)
    elif operation == "read":
        name = request["name"]
        if name not in ("status.json", "stopped.json"): raise RuntimeError("Invalid app status name")
        try: result = json.loads(read_file(directory, name))
        except FileNotFoundError: result = None
    elif operation == "snapshot":
        result = {}; total = [0]
        def walk(parent, prefix="", depth=0):
            if depth > 4: raise RuntimeError("App directory nesting exceeds budget")
            for name in sorted(os.listdir(parent)):
                info = os.stat(name, dir_fd=parent, follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    child = os.open(name, directory_flags, dir_fd=parent)
                    try: checked(child, True); walk(child, prefix+name+"/", depth+1)
                    finally: os.close(child)
                else:
                    value = read_file(parent, name); total[0] += len(value)
                    if total[0] > 32*1024*1024 or len(result) >= 1024: raise RuntimeError("App snapshot exceeds budget")
                    result[prefix+name] = base64.b64encode(value).decode()
        walk(directory)
    else: raise RuntimeError("Invalid app file operation")
finally: os.close(directory)
print(json.dumps(result))
'''


def call(path, uid, gid, operation, request):
    result = subprocess.run(["python3", "-I", "-c", PROGRAM, str(path), operation],
        input=json.dumps(request).encode(), capture_output=True, timeout=15,
        user=uid, group=gid, extra_groups=[], cwd="/", env={"PATH":"/usr/bin:/bin", "LANG":"C.UTF-8"})
    if result.returncode:
        raise RuntimeError("Ordinary app file operation failed: " + result.stderr[-600:].decode(errors="replace"))
    return json.loads(result.stdout)


def save_snapshot(path, uid, gid, output):
    values = call(path, uid, gid, "snapshot", {})
    output = Path(output); output.mkdir(exist_ok=True)
    for name, encoded in values.items():
        relative = PurePosixPath(name)
        if not relative.parts or relative.is_absolute() or any(p in (".", "..") for p in relative.parts):
            raise RuntimeError("Invalid app snapshot path")
        target = output.joinpath(*relative.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(base64.b64decode(encoded, validate=True))
