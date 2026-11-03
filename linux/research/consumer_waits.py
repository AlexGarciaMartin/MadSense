"""Opt-in, read-only owned-guest kernel wait evidence; never a recovery action."""
import json
import os
from pathlib import Path
import subprocess
import threading
import time

FIELD_BYTES = 8192
TOTAL_BYTES = 4 * 1024 * 1024
MAX_SNAPSHOTS = 64
MAX_PROCESSES = 64
MAX_TASKS = 64
_active = None


def field(path):
    try:
        with Path(path).open("rb") as stream:
            raw = stream.read(FIELD_BYTES + 1)
        return dict(text=raw[:FIELD_BYTES].decode(errors="replace"), truncated=len(raw) > FIELD_BYTES)
    except OSError as error:
        return dict(unavailable=type(error).__name__, errno=error.errno)


def identity(pid, proc=Path("/proc")):
    # comm can contain spaces and parentheses. Fields after its final ')' start
    # at state (3); starttime is field 22. Check UID without reading arguments.
    text = (proc / str(pid) / "stat").read_text()
    end = text.rfind(")")
    if end < 0 or text[:text.find(" ")] != str(pid):
        raise ValueError("Malformed process identity")
    rest = text[end + 2:].split()
    if len(rest) < 20:
        raise ValueError("Incomplete process identity")
    status = (proc / str(pid) / "status").read_text()
    uid = next(line.split()[1:] for line in status.splitlines() if line.startswith("Uid:"))
    if len(uid) != 4 or len(set(uid)) != 1:
        raise ValueError("Process UID changed or differs between credentials")
    return dict(pid=pid, startTicks=int(rest[19]), uid=int(uid[0]), state=rest[0], parentPid=int(rest[1]))


def same_process(expected, actual):
    return all(actual.get(key) == expected.get(key) for key in ("pid", "startTicks", "uid"))


def register_owned_process(pid, uid, label):
    # Called only for Popen instances created by ConsumerSession. The optional
    # watcher never discovers unrelated PIDs by walking /proc.
    observer = _active
    if observer is not None:
        observer.register(pid, uid, label)


class WaitObserver:
    def __init__(self, output, core_pid, core_uid):
        self.output = Path(output)
        self.processes = {}
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread = None
        self.thread_started = False
        self.stream = None
        self.bytes = 0
        self.summary = dict(schema=1, scope="owned-guest-readonly-kernel-waits", policyModified=False,
            intervalMs=100, maximumSnapshots=MAX_SNAPSHOTS, maximumBytes=TOTAL_BYTES,
            maximumFieldBytes=FIELD_BYTES, snapshots=0, unavailable=[], stopped=False,
            processDiscovery="registered Popen and their same-UID children only",
            completeProcessEnumeration=False)
        self.core_pid, self.core_uid = core_pid, core_uid

    def unavailable(self, context, error):
        with self.lock:
            if len(self.summary["unavailable"]) < 32:
                self.summary["unavailable"].append(dict(context=context,
                    error=f"{type(error).__name__}: {error}"[:512], monotonicNs=time.monotonic_ns()))

    def register(self, pid, uid, label):
        try:
            current = identity(pid)
            if current["uid"] != uid:
                raise ValueError("Registered process UID mismatch")
            self.remember(current, label)
        except (OSError, ValueError, StopIteration) as error:
            self.unavailable("register", error)

    def remember(self, current, label):
        with self.lock:
            pid = current["pid"]
            if pid not in self.processes and len(self.processes) >= MAX_PROCESSES:
                self.summary["processLimitReached"] = True
                return
            self.processes[pid] = current | {"label": label[:80]}

    def start(self):
        global _active
        self.summary["startedMonotonicNs"] = time.monotonic_ns()
        try:
            if os.geteuid() != 0 or _active is not None:
                raise ValueError("Owned root harness and one observer required")
            answer = subprocess.run(["systemctl", "show", "madsense-broker.service", "--property=MainPID", "--value"],
                                    capture_output=True, check=True, timeout=3)
            pid = int(answer.stdout.strip())
            if pid <= 1:
                raise ValueError("Broker service is not running")
            self.broker = identity(pid)
            if self.broker["uid"] != 0:
                raise ValueError("Broker is not root")
            # Validate, but never retain process arguments/environment.
            args = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
            if b"/usr/libexec/madsense/broker.py" not in args:
                raise ValueError("Unexpected broker command")
            self.summary["broker"] = self.broker
            self.register(pid, 0, "broker")
            self.register(self.core_pid, self.core_uid, "production-core")
            self.stream = (self.output / "kernel-waits.jsonl").open("xb")
            _active = self
            self.thread = threading.Thread(target=self.run, name="madsense-owned-wait-observer", daemon=True)
            self.thread.start()
            self.thread_started = True
        except BaseException as error:
            self.unavailable("start", error)
        return self

    def current(self):
        with self.lock:
            candidates = list(self.processes.values())
        live = []
        for expected in candidates:
            try:
                actual = identity(expected["pid"])
                if not same_process(expected, actual):
                    raise ValueError("PID identity changed; no replacement followed")
                live.append(expected | {"state": actual["state"]})
            except (OSError, ValueError, StopIteration):
                with self.lock:
                    if self.processes.get(expected["pid"]) == expected:
                        self.processes.pop(expected["pid"], None)
        return live

    def children(self, live):
        for parent in live:
            if parent["uid"] == 0:
                continue  # Never expand the privileged service's process tree.
            try:
                if not same_process(parent, identity(parent["pid"])):
                    raise ValueError("Parent changed before children observation")
                data = field(Path("/proc", str(parent["pid"]), "task", str(parent["pid"]), "children"))
                if data.get("truncated"):
                    self.summary["childrenLimitReached"] = True
                for item in data.get("text", "").split()[:32]:
                    pid = int(item)
                    with self.lock:
                        known = pid in self.processes
                    if not known:
                        self.register_child(parent, pid)
            except (OSError, ValueError, StopIteration) as error:
                self.unavailable("children", error)

    def register_child(self, parent, pid):
        if not same_process(parent, identity(parent["pid"])):
            raise ValueError("Parent changed before child registration")
        child = identity(pid)
        if child["uid"] != parent["uid"] or child["parentPid"] != parent["pid"]:
            raise ValueError("Child credentials or parent differ")
        listed = field(Path("/proc", str(parent["pid"]), "task", str(parent["pid"]), "children"))
        if str(pid) not in listed.get("text", "").split():
            raise ValueError("Child no longer belongs to the pinned parent")
        after = identity(pid)
        if not same_process(child, after) or after["parentPid"] != parent["pid"]:
            raise ValueError("Child identity changed during registration")
        if not same_process(parent, identity(parent["pid"])):
            raise ValueError("Parent changed during child registration")
        # Store the already validated identity. Re-reading by PID here could
        # accidentally adopt a replacement between validation and registration.
        self.remember(child, parent["label"] + ":child")

    def snapshot(self, live):
        result = dict(monotonicNs=time.monotonic_ns(), processes=[])
        task_budget = MAX_TASKS
        for expected in live:
            if task_budget <= 0:
                result["taskLimitReached"] = True
                break
            try:
                row = self.process_snapshot(expected, task_budget)
                task_budget -= len(row["tasks"])
                result["processes"].append(row)
            except (OSError, ValueError, StopIteration) as error:
                result["processes"].append(expected | {"unavailable": f"{type(error).__name__}: {error}"[:512]})
        result["endedMonotonicNs"] = time.monotonic_ns()
        return result

    def process_snapshot(self, expected, task_budget):
        pid = expected["pid"]
        if not same_process(expected, identity(pid)):
            raise ValueError("PID identity changed before snapshot")
        row = expected | {"tasks": []}
        tasks = sorted(Path(f"/proc/{pid}/task").iterdir(), key=lambda item: int(item.name))
        row["tasksTruncated"] = len(tasks) > task_budget
        for task in tasks[:task_budget]:
            row["tasks"].append(dict(tid=int(task.name), comm=field(task / "comm"), stat=field(task / "stat"),
                wchan=field(task / "wchan"), syscall=field(task / "syscall"), stack=field(task / "stack")))
        # Exact service descriptors identify whether a blocked write targets
        # physical hidraw, a socket, or another object. Never read FD content.
        if pid == self.broker["pid"]:
            row["fdTargets"] = {}
            fds = sorted(Path(f"/proc/{pid}/fd").iterdir(), key=lambda item: int(item.name))
            row["fdsTruncated"] = len(fds) > 64
            for fd in fds[:64]:
                try:
                    row["fdTargets"][fd.name] = os.readlink(fd)[:FIELD_BYTES]
                except OSError as error:
                    row["fdTargets"][fd.name] = dict(unavailable=type(error).__name__, errno=error.errno)
        if not same_process(expected, identity(pid)):
            raise ValueError("PID identity changed during snapshot; fields discarded")
        return row

    def append(self, record):
        data = (json.dumps(record, separators=(",", ":")) + "\n").encode()
        if self.summary["snapshots"] >= MAX_SNAPSHOTS or self.bytes + len(data) > TOTAL_BYTES:
            self.summary["captureLimitReached"] = True
            return False
        self.stream.write(data); self.stream.flush()
        self.bytes += len(data)
        self.summary["snapshots"] += 1
        return True

    def run(self):
        try:
            while not self.stop_event.is_set():
                live = self.current()
                broker = next((row for row in live if same_process(self.broker, row)), None)
                if broker is None:
                    self.summary["brokerNoLongerMatches"] = True
                    break
                self.children(live)
                if broker["state"] == "D":
                    try:
                        if not self.append(self.snapshot(live)):
                            break
                    except (OSError, ValueError, StopIteration) as error:
                        self.unavailable("snapshot", error)
                self.stop_event.wait(.1)
        except BaseException as error:
            self.unavailable("observer", error)
        finally:
            if self.stream is not None:
                self.stream.close()
            self.summary.update(stopped=True, endedMonotonicNs=time.monotonic_ns(), bytes=self.bytes)

    def close(self):
        global _active
        if _active is self:
            _active = None
        self.stop_event.set()
        if self.thread_started:
            self.thread.join(timeout=3)
            self.summary["stoppedBeforeCleanup"] = not self.thread.is_alive()
        else:
            self.summary.update(stopped=True, stoppedBeforeCleanup=True)
            if self.stream is not None:
                self.stream.close()
        self.summary["captureIncomplete"] = not self.summary["stoppedBeforeCleanup"]
        try:
            (self.output / "kernel-waits-summary.json").write_text(json.dumps(self.summary, indent=2) + "\n")
        except OSError:
            pass  # Diagnostic failure must not mask the actual experiment error.
        return dict(self.summary)
