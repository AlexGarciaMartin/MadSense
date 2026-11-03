"""Optional GE/UMU primitive for the explicit owned-VM consumer experiment.

The separate opt-in hook uses ConsumerSession's ordinary account,
exact virtual device grants and mandatory process/ACL cleanup. Never changes
AppArmor, sysctls, host Steam, production settings or existing Wine assertions.
"""
import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import tarfile

from ge_umu_acquire import (LOCK, MAX_ARCHIVE_BYTES, MAX_ENTRIES, digest, load_lock,
                            require, safe_name, verify_bundle)


WRITE_MARKER_PROGRAM = """import os, sys
fd = os.open(sys.argv[1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
with os.fdopen(fd, 'wb') as stream:
    stream.write(b'ok\\n')
"""

# Execute as the existing ordinary account. Never read paths chosen by the
# runtime as root; directory/file descriptors reject symlinks and replacements.
# Logs are diagnostic only and bounded, unlike private runtime/prefix payloads.
READ_PROTON_LOGS_PROGRAM = r'''import base64,hashlib,json,os,re,stat,sys
assert os.getuid()!=0 and os.geteuid()==os.getuid()
fd=os.open(sys.argv[1],os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
try:
    parent=os.fstat(fd);assert stat.S_ISDIR(parent.st_mode) and parent.st_uid==os.getuid()
    names=sorted(x for x in os.listdir(fd) if re.fullmatch(r'steam-[A-Za-z0-9_-]{1,80}\.log',x))
    assert len(names)<=4,'Too many Proton log files'
    result=[];remaining=2*1024*1024
    for name in names:
        item=os.open(name,os.O_RDONLY|os.O_NONBLOCK|os.O_NOFOLLOW,dir_fd=fd)
        try:
            before=os.fstat(item)
            assert stat.S_ISREG(before.st_mode) and before.st_uid==os.getuid() and before.st_nlink==1
            limit=min(1024*1024,remaining)
            data=b''
            while len(data)<limit:
                chunk=os.read(item,min(65536,limit-len(data)))
                if not chunk:break
                data+=chunk
            after=os.fstat(item)
            assert (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)==(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns),'Log changed during snapshot'
            remaining-=len(data)
            result.append(dict(name=name,bytes=before.st_size,capturedBytes=len(data),truncated=before.st_size>len(data),
                sha256=hashlib.sha256(data).hexdigest(),dataBase64=base64.b64encode(data).decode()))
        finally:os.close(item)
    print(json.dumps(dict(schema=1,uid=os.getuid(),logs=result)))
finally:os.close(fd)
'''


def capture_proton_logs(session, label):
    """Keep bounded UID-read diagnostics; failures never replace the PE result."""
    try:
        require(re.fullmatch(r"[A-Za-z0-9_-]{1,100}", label), "Unsafe Proton diagnostic label")
        _, output = session.command(label + "-proton-logs",
            ["python3", "-c", READ_PROTON_LOGS_PROGRAM, session.root / "home"], timeout=10)
        value = json.loads(output)
        require(value.get("schema") == 1 and value.get("uid") == session.uid and isinstance(value.get("logs"), list),
                "Unexpected Proton log snapshot identity")
        require(len(value["logs"]) <= 4, "Too many Proton log snapshots")
        total = 0
        for index, record in enumerate(value["logs"]):
            data = base64.b64decode(record.pop("dataBase64"), validate=True)
            total += len(data)
            require(len(data) <= 1024*1024 and total <= 2*1024*1024
                    and record["capturedBytes"] == len(data) and record["sha256"] == hashlib.sha256(data).hexdigest(),
                    "Proton log snapshot bytes differ from their bounded digest")
            target = session.output / f"{label}-proton-log-{index}.log"
            with target.open("xb") as stream:
                stream.write(data)
            record["evidenceFile"] = target.name
        session.result.setdefault("protonLogSnapshots", []).append(dict(label=label, snapshot=value,
            stdoutSha256=hashlib.sha256(output).hexdigest(), runtimeCacheCollected=False))
    except Exception as error:
        session.result.setdefault("protonLogSnapshots", []).append(dict(label=label,
            unavailable=f"{type(error).__name__}: {error}", runtimeCacheCollected=False))
    try:
        session.save()
    except Exception:
        # Saving diagnostics is secondary to the actual operation and cleanup.
        pass


def extract_payload(archive, destination):
    """Only verified binary payloads, into a new directory, with data filtering."""
    require(platform.system() == "Linux", "Payload extraction is restricted to the Linux guest")
    destination = Path(destination)
    require(not destination.exists() and not destination.is_symlink(), "Fresh payload directory required")
    destination.mkdir(mode=0o755)
    entries, total = 0, 0
    with tarfile.open(archive, "r|*") as source:
        for member in source:
            safe_name(member.name)
            entries += 1
            total += member.size
            require(entries <= MAX_ENTRIES and total <= MAX_ARCHIVE_BYTES, "Payload expansion limit exceeded")
            require(member.isfile() or member.isdir() or member.issym() or member.islnk(),
                    "Special file in payload")
            source.extract(member, destination, filter="data")


def private_environment(home, proton):
    home, proton = Path(home), Path(proton)
    require(home.is_absolute() and proton.is_absolute(), "Absolute private runtime paths required")
    return dict(PROTONPATH=str(proton), WINEPREFIX=str(home / "ge-prefix"),
        UMU_FOLDERS_PATH=str(home / "ge-umu"), GAMEID="umu-default", STORE="none",
        XDG_CONFIG_HOME=str(home / ".config"), XDG_DATA_HOME=str(home / ".local/share"),
        XDG_CACHE_HOME=str(home / ".cache"), XDG_STATE_HOME=str(home / ".local/state"),
        UMU_RUNTIME_UPDATE="0", UMU_HTTP_TIMEOUT="2", UMU_HTTP_RETRIES="0",
        # PROTON_LOG=1 redirects the PE's stdout into the Proton log. Functional
        # JSON must remain on the original pipe, separate from bounded logs.
        PROTONFIXES_DISABLE="1", PROTON_LOG="0", PROTON_LOG_DIR=str(home),
        MADSENSE_CONSUMER_SCOPE_LAYOUT="pressure-vessel",
        UMU_LOG="debug")


def offline_command(launcher, program, arguments):
    # No host networking or protection changes. The additional namespace keeps
    # even an upstream download regression from reaching the network. If this
    # normal unprivileged operation is unavailable, retain failure and stop.
    # GE's Unix-path umu.exe shim starts console PEs with CREATE_NEW_CONSOLE
    # and bInheritHandles=FALSE. Our observers need their ordinary redirected
    # stdout; the supported Windows-path branch still runs through UMU/Proton
    # and the same pressure-vessel runtime, but launches Wine directly.
    path = str(program)
    require(path.startswith("/") and str(PurePosixPath(path)) == path
            and ".." not in PurePosixPath(path).parts and PurePosixPath(path).suffix.lower() == ".exe"
            and not any(character in path for character in '\\:"<>|?*')
            and all(ord(character) >= 32 and ord(character) != 127 for character in path),
            "An unambiguous absolute Linux PE path is required")
    windows_path = "Z:" + path.replace("/", "\\")
    return ["unshare", "--user", "--map-current-user", "--net", "--",
            "python3", str(launcher), windows_path, *map(str, arguments)]


def change_owner_tree(root, uid, gid):
    # Do not dereference payload symlinks while assigning the private runtime.
    os.chown(root, uid, gid, follow_symlinks=False)
    for directory, folders, files in os.walk(root, followlinks=False):
        for name in folders + files:
            os.chown(Path(directory) / name, uid, gid, follow_symlinks=False)


def prepare(session, cache):
    """Verify, stage and pv-verify. Caller must own an entered ConsumerSession."""
    from consumer_session import ConsumerSession
    require(isinstance(session, ConsumerSession) and session.root is not None and not session.closed
            and session.uid > 0 and session.gid > 0 and os.geteuid() == 0,
            "Entered owned-VM ConsumerSession required")
    require(platform.machine() == "x86_64" and platform.system() == "Linux", "Pinned payload is Linux x86_64")
    session.check_device()
    lock = load_lock()
    verified = verify_bundle(cache, lock)
    cache = Path(cache)
    for binary in ("python3", "unshare"):
        require(shutil.which(binary), f"Missing guest prerequisite: {binary}")
    session.result["geUmuSession"] = dict(label=lock["label"], assets=verified, lockSha256=digest(LOCK),
        distributionWine=False, officialValveProton=False, runtimeExecutionConfirmed=False,
        consumerAssertionsPassed=False, configuredProtonfixesDisabled=True, networkNamespace=True)
    session.save()
    payload = session.root / "ge-payload"
    extract_payload(cache / "GE-Proton11-11-x86_64.tar.gz", payload)
    proton = payload / lock["geRoot"]
    manifest = (proton / "toolmanifest.vdf").read_text()
    require(f'"require_tool_appid" "{lock["runtimeAppId"]}"' in manifest, "GE requires another runtime")
    version = (proton / "version").read_text().strip()
    require(version.split()[-1] == lock["geVersion"], "GE version file mismatch")
    launcher_root = session.root / "umu-launcher"
    extract_payload(cache / "umu-launcher-1.4.4-zipapp.tar", launcher_root)
    launcher = launcher_root / "umu/umu-run"
    home = Path(session.env["HOME"])
    unpacked = session.root / "runtime-unpacked"
    extract_payload(cache / "SteamLinuxRuntime_4.tar.xz", unpacked)
    # Read trusted payload metadata before moving it into the user's writable
    # tree or executing any payload process under that user.
    versions = (unpacked / "SteamLinuxRuntime_4/VERSIONS.txt").read_text()
    require(f"steamrt4\t{lock['runtimeBuild']}\t" in versions, "Runtime VERSIONS mismatch")
    folders = home / "ge-umu/umu"
    folders.mkdir(parents=True)
    runtime = folders / "steamrt4"
    (unpacked / "SteamLinuxRuntime_4").rename(runtime)
    unpacked.rmdir()
    change_owner_tree(home / "ge-umu", session.uid, session.gid)
    env = private_environment(home, proton)
    # Do not fabricate UMU's install marker. First run the same mtree check its
    # pinned installer performs; only successful verification may mark complete.
    verify = runtime / "pressure-vessel/bin/pv-verify"
    session.command("ge-runtime-pv-verify", [verify, "--quiet", "--minimized-runtime",
                                           runtime / lock["runtimePlatform"] / "files"], timeout=60)
    # This directory is now user-controlled. Never write/chown through it as
    # root, even after a successful verifier. Existing files or links fail.
    session.command("ge-runtime-install-marker", ["python3", "-c", WRITE_MARKER_PROGRAM,
                                                  runtime / ".installed.ok"], timeout=10)
    session.result["geUmuSession"].update(geVersionFile=version, runtimeVersions=versions,
        runtimeMtreeVerified=True, environment=env, sourceArchivesRetained=True,
        functionalOutput="launcher-stdout-pipe", protonFileLoggingRequested=False,
        scopeMarkerLayout="pressure-vessel-readonly-host-etc", scopeMarkerCopied=False)
    session.save()
    # The version option does not run Proton; preserve that distinction.
    _, output = session.command("ge-umu-launcher-version", ["python3", launcher, "--version"], env=env)
    require(lock["umuVersion"] in output.decode("utf-8", errors="replace"), "UMU version output mismatch")
    session.command("ge-private-network-namespace", ["unshare", "--user", "--map-current-user", "--net",
        "--", "python3", "-c",
        "import os,socket; assert os.geteuid()!=0; s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); "
        "s.settimeout(2);\ntry: s.connect(('1.1.1.1',53))\n"
        "except OSError as e: assert e.errno==101; print('owned network namespace: no route')\n"
        "else: raise RuntimeError('Unexpected network route')"], timeout=10)
    session.check_device()
    return dict(launcher=launcher, environment=env, proton=proton, runtime=runtime, lock=lock)


def run_consumer(session, prepared, pe_path, arguments, *, label="ge-umu-consumer", timeout=90):
    """Return raw PE evidence for the caller's independent existing assertions.

This deliberately does not turn exit zero or missing output into compatibility.
The calling suite must still join MAC/HID/ContainerId, virtual node visibility,
actual input/output and PCM markers, and always close its ConsumerSession.
"""
    program = Path(pe_path).resolve(strict=True)
    require(program.is_relative_to(session.root) and program.is_file(), "Stage the owned PE inside ConsumerSession")
    with program.open("rb") as stream:
        require(stream.read(2) == b"MZ", "Expected the built Windows PE consumer")
    require(1 <= timeout <= 120, "Consumer timeout exceeds budget")
    command = offline_command(prepared["launcher"], program, arguments)
    session.result["geUmuSession"].update(consumerLaunchPath=command[7],
        consumerLaunchPathKind="windows-z-drive", unixConsoleShimRequested=False)
    session.save()
    try:
        row, output = session.command(label, command,
                                      env=prepared["environment"], timeout=timeout, required=False)
    finally:
        capture_proton_logs(session, label)
    session.result["geUmuSession"].update(consumerLaunchAttempted=True, lastConsumerExit=row["exitCode"],
        consumerPeSha256=digest(program), lastRawOutputSha256=hashlib.sha256(output).hexdigest())
    session.save()
    return row, output
