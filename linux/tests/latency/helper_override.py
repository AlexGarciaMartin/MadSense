"""Temporary ExecStart override, only after idle functional gates in owned QEMU."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import time
import uuid

SERVICE = "madsense-broker.service"
INSTALLED = Path("/usr/libexec/madsense")
UNIT = Path("/etc/systemd/system") / SERVICE
DROPINS = UNIT.with_name(SERVICE + ".d")
SANDBOX = ("User", "Group", "Type", "NoNewPrivileges", "ProtectHome", "ProtectSystem", "ReadWritePaths",
           "PrivateTmp", "RestrictAddressFamilies", "LockPersonality", "RestrictSUIDSGID", "CapabilityBoundingSet",
           "RestrictNamespaces", "FileDescriptorStoreMax", "FileDescriptorStorePreserve", "NotifyAccess")
PROPERTIES = (*SANDBOX, "MainPID", "ActiveState", "SubState", "DropInPaths", "NFileDescriptorStore", "ExecStart", "ExecStopPost")
NAMES = ("broker.py", "broker_core.py", "research_helper_trace.py")


def require(value, message):
    if not value:
        raise RuntimeError(message)


def read_regular(path, *, root_owned=False, limit=1024*1024):
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open(path, flags)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= limit, "Unsafe helper source file")
        if root_owned:
            require(info.st_uid == 0 and info.st_gid == 0 and info.st_mode & 0o022 == 0, "Helper source is not root protected")
        with os.fdopen(os.dup(fd), "rb") as stream:
            data = stream.read(limit + 1)
        require(len(data) <= limit and len(data) == info.st_size, "Helper source changed size")
        return data, dict(dev=info.st_dev, inode=info.st_ino, mode=stat.S_IMODE(info.st_mode),
                          uid=info.st_uid, gid=info.st_gid, sha256=hashlib.sha256(data).hexdigest())
    finally:
        os.close(fd)


def validate_sources(directory, overlay):
    require(overlay.get("schema") == 1 and overlay.get("kind") == "research-helper-copy"
            and overlay.get("productionHelper") is False and overlay.get("decisionsChanged") is False,
            "Unsupported helper source overlay")
    require(set(overlay.get("files", {})) == set(NAMES), "Helper copy has unexpected files")
    result = {}
    for name in NAMES:
        data, identity = read_regular(Path(directory) / name)
        require(identity["sha256"] == overlay["files"][name], "Helper copy digest differs: " + name)
        result[name] = data
    return result


def check_directory(path):
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and info.st_gid == 0
            and info.st_mode & 0o022 == 0, "Unsafe root helper directory")


class HelperOverride:
    def __init__(self, source, output, token):
        # This guard precedes all filesystem or service mutation.
        require(sys.platform == "linux" and os.geteuid() == 0, "Root Linux guest required")
        from capture_diagnostics import _owned_guest
        self.scope = _owned_guest(token)
        self.source, self.output = Path(source), Path(output)
        self.output.mkdir(mode=0o755)
        self.output.chmod(0o755)
        self.overlay = json.loads((self.source / "research-helper-overlay.json").read_text())
        self.data = validate_sources(self.source, self.overlay)
        from stage_helper_trace import verify_transform
        root = Path(__file__).resolve().parents[3]
        verify_transform(self.source, self.overlay, (root / "linux/platform/broker.py").read_bytes(),
            (root / "linux/platform/broker_core.py").read_bytes(), (root / "linux/tests/latency/helper_trace.py").read_bytes(),
            revision=self.overlay["sourceCommit"], archive_digest=self.overlay["sourceArchiveSha256"])
        suffix = uuid.uuid4().hex
        self.directory = Path("/opt", "madsense-helper-trace-" + suffix)
        self.dropin = DROPINS / ("90-madsense-research-" + suffix + ".conf")
        self.dropin_bytes = ("[Service]\nExecStart=\nExecStart=/usr/bin/python3 -I "
                             + str(self.directory / "broker.py") + " --serve\n").encode()
        self.created_directory = self.created_dropin = self.created_dropin_dir = False
        self.service_touched = False
        self.dropin_identity = None
        self.original = {}
        self.record = dict(schema=1, environment=self.scope, sourceOverlay=self.overlay,
            originalFunctionalGatesUseOriginalHelper=True, timingUsesObservedHelper=True,
            observedHelperCoreCyclePassed=False, originalServiceRestored=False,
            copiedDirectoryRemoved=False, cleanupErrors=[], commands=[])

    def save(self):
        (self.output / "service.json").write_text(json.dumps(self.record, indent=2) + "\n")

    def command(self, *argv, required=True):
        completed = subprocess.run(argv, capture_output=True, timeout=45)
        index = len(self.record["commands"])
        self.record["commands"].append(dict(argv=list(argv), exitCode=completed.returncode))
        for stream in ("stdout", "stderr"):
            data = getattr(completed, stream)
            require(len(data) <= 1024*1024, "Service command output exceeds evidence bound")
            (self.output / f"command-{index:03d}.{stream}").write_bytes(data)
        self.save()
        require(not required or completed.returncode == 0, "Service command failed: " + " ".join(argv))
        return completed

    def properties(self):
        output = self.command("systemctl", "show", SERVICE, "--property=" + ",".join(PROPERTIES)).stdout.decode()
        values = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
        require(set(PROPERTIES).issubset(values), "Missing service property, including fdstore count")
        return values

    def idle(self, *, allow_stopped=False):
        from repeat_install_guest import preflight
        from kernel_probe import ports
        require(not Path("/run/madsense/lease.json").exists()
                and not Path("/run/udev/rules.d/72-madsense-lease.rules").exists()
                and all(row["localBusId"] == "0-0" for row in ports().values()), "Lease/recovery/import still exists")
        properties = self.properties()
        require(properties["NFileDescriptorStore"] == "0", "PID 1 still retains helper descriptors")
        if allow_stopped and properties["MainPID"] == "0" and properties["ActiveState"] in ("inactive", "failed"):
            return properties
        require(properties["ActiveState"] == "active" and int(properties["MainPID"]) > 1, "Helper is not active")
        reply = preflight()
        require(reply.get("ok") is True and reply.get("result", {}).get("available") is True
                and reply["result"].get("leaseActive") is False and reply["result"].get("recoveryPending") is False,
                "Fresh helper preflight does not confirm idle")
        return properties

    def wait_idle(self, *, allow_stopped=False):
        deadline = time.monotonic() + 10
        while True:
            try:
                return self.idle(allow_stopped=allow_stopped)
            except (RuntimeError, OSError):
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.05)

    def process(self, properties, expected_path):
        pid = int(properties["MainPID"])
        fields = Path(f"/proc/{pid}/stat").read_text().rpartition(")")[2].split()
        require(len(fields) > 19, "MainPID start identity unavailable")
        expected = [b"/usr/bin/python3", b"-I", str(expected_path).encode(), b"--serve"]
        require(Path(f"/proc/{pid}/cmdline").read_bytes().rstrip(b"\0").split(b"\0") == expected,
                "MainPID does not run the exact expected helper")
        after = Path(f"/proc/{pid}/stat").read_text().rpartition(")")[2].split()
        require(len(after) > 19 and after[19] == fields[19], "MainPID identity changed while inspecting command line")
        return dict(pid=pid, startTicks=int(fields[19]))

    def original_unchanged(self):
        for name, saved in self.original.items():
            _, current = read_regular(Path(name), root_owned=True)
            require(current == saved, "Original helper/unit identity or bytes changed")

    def start(self):
        check_directory(INSTALLED)
        for path in (INSTALLED / "broker.py", INSTALLED / "broker_core.py", UNIT):
            _, self.original[str(path)] = read_regular(path, root_owned=True)
        expected = self.overlay["changes"]["linux/platform/broker.py"]["originalSha256"]
        require(self.original[str(INSTALLED / "broker.py")]["sha256"] == expected
                and self.original[str(INSTALLED / "broker_core.py")]["sha256"] == self.overlay["files"]["broker_core.py"],
                "Functional gates used different installed source")
        original = self.wait_idle()
        require(original["DropInPaths"] == "", "Existing unit drop-ins are outside this experiment")
        self.record.update(originalFiles=self.original, originalProperties=original,
                           originalProcess=self.process(original, INSTALLED / "broker.py"))
        self.directory.mkdir(mode=0o755)
        self.created_directory = True
        self.directory.chmod(0o755)
        for name, data in self.data.items():
            path = self.directory / name
            with path.open("xb") as stream:
                stream.write(data)
            path.chmod(0o644)
        if not DROPINS.exists():
            DROPINS.mkdir(mode=0o755)
            self.created_dropin_dir = True
        check_directory(DROPINS)
        self.service_touched = True
        self.command("systemctl", "stop", SERVICE)
        self.wait_idle(allow_stopped=True)
        with self.dropin.open("xb") as stream:
            self.created_dropin = True
            stream.write(self.dropin_bytes)
        self.dropin.chmod(0o644)
        _, self.dropin_identity = read_regular(self.dropin, root_owned=True)
        self.command("systemctl", "daemon-reload")
        self.command("systemctl", "start", SERVICE)
        observed = self.wait_idle()
        require(observed["DropInPaths"] == str(self.dropin), "Observed service has unexpected drop-ins")
        require(all(original[name] == observed[name] for name in SANDBOX), "Observed helper sandbox differs")
        self.original_unchanged()
        self.record.update(observedProperties=observed,
                           observedProcess=self.process(observed, self.directory / "broker.py"),
                           dropinPath=str(self.dropin), dropinSha256=hashlib.sha256(self.dropin_bytes).hexdigest())
        self.save()

    def close(self):
        try:
            if self.service_touched:
                self.original_unchanged()
                before = self.wait_idle(allow_stopped=True)
                if self.created_dropin:
                    data, identity = read_regular(self.dropin, root_owned=True)
                    loaded = before["DropInPaths"] == str(self.dropin)
                    unloaded_stopped = (before["DropInPaths"] == "" and before["MainPID"] == "0"
                                        and before["ActiveState"] in ("inactive", "failed"))
                    require(data == self.dropin_bytes and identity == self.dropin_identity and (loaded or unloaded_stopped),
                            "Temporary unit override changed externally")
                    if before["MainPID"] != "0":
                        current = self.process(before, self.directory / "broker.py")
                        require(current == self.record.get("observedProcess", current), "Observed helper restarted unexpectedly")
                self.command("systemctl", "stop", SERVICE)
                self.wait_idle(allow_stopped=True)
                if self.created_dropin:
                    self.dropin.unlink()
                    self.created_dropin = False
                if self.created_dropin_dir:
                    DROPINS.rmdir()
                    self.created_dropin_dir = False
                self.command("systemctl", "daemon-reload")
                self.command("systemctl", "start", SERVICE)
                restored = self.wait_idle()
                require(restored["DropInPaths"] == "" and all(self.record["originalProperties"][name] == restored[name]
                        for name in SANDBOX), "Original service configuration was not restored")
                self.original_unchanged()
                self.record.update(originalServiceRestored=True, restoredProperties=restored,
                                   restoredProcess=self.process(restored, INSTALLED / "broker.py"))
            if self.created_directory:
                check_directory(self.directory)
                require(self.directory.parent == Path("/opt") and self.directory.name.startswith("madsense-helper-trace-")
                        and set(path.name for path in self.directory.iterdir()) == set(NAMES), "Unexpected research helper directory contents")
                for name, original in self.data.items():
                    current, _ = read_regular(self.directory / name, root_owned=True)
                    require(current == original, "Research source changed externally")
                shutil.rmtree(self.directory)
                self.created_directory = False
                self.record["copiedDirectoryRemoved"] = True
        except BaseException as error:
            self.record["cleanupErrors"].append(f"{type(error).__name__}: {error}")
            raise
        finally:
            self.save()
