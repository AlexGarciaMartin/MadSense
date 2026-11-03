"""Copied research-only broker observer; no input/RPC hooks or decisions."""
import functools
import hashlib
import json
import os
import sys
import threading
import time

CAPACITY = 256
STAGES = {"verify": 1, "inventory": 2, "check_audit": 3, "audit_foreign": 4, "start_audit": 5}
FIELDS = ["sequence", "stage", "thread", "active", "startNs", "endNs", "threadStartNs",
          "threadEndNs", "processStartNs", "processEndNs", "outcome"]


class HelperTrace:
    def __init__(self, lease):
        self.identity = dict(leaseId=lease.lease_id, ownerPid=lease.pid, ownerStart=lease.start,
                             helperPid=os.getpid())
        self.lock = threading.Lock()
        self.rows = [None] * CAPACITY
        self.attempts = self.open_scopes = self.late_finishes = 0
        self.observer_errors = 0
        self.stage_starts = {number: 0 for number in STAGES.values()}
        self.closed = False

    def begin(self, stage, active):
        start = (time.monotonic_ns(), time.thread_time_ns(), time.process_time_ns())
        with self.lock:
            if self.closed:
                self.late_finishes += 1
                return None
            sequence = self.attempts
            self.attempts += 1
            self.open_scopes += 1
            self.stage_starts[stage] += 1
            self.rows[sequence % CAPACITY] = [sequence, stage, threading.get_native_id(), bool(active),
                                              start[0], 0, start[1], 0, start[2], 0, "open"]
        return sequence

    def finish(self, sequence, outcome):
        if sequence is None:
            return
        end = (time.monotonic_ns(), time.thread_time_ns(), time.process_time_ns())
        with self.lock:
            self.open_scopes -= 1
            row = self.rows[sequence % CAPACITY]
            if self.closed or row is None or row[0] != sequence:
                self.late_finishes += 1
                return
            row[5], row[7], row[9], row[10] = *end, outcome

    def snapshot(self):
        with self.lock:
            self.closed = True
            first = max(0, self.attempts - CAPACITY)
            rows = [list(self.rows[i % CAPACITY]) for i in range(first, self.attempts)]
            return dict(schema=1, kind="research-helper-durations", **self.identity, capacity=CAPACITY,
                        total=self.attempts, overwritten=first, tailStart=first, tailEnd=self.attempts,
                        openScopes=self.open_scopes, lateFinishes=self.late_finishes,
                        observerErrors=self.observer_errors,
                        stageStarts={name: self.stage_starts[number] for name, number in STAGES.items()},
                        queuedAuditsNotStarted=self.stage_starts[5] - self.stage_starts[4],
                        rows=rows, fields=FIELDS, stages=STAGES, flushedNs=time.monotonic_ns(),
                        timestamps="CLOCK_MONOTONIC/thread CPU/process CPU nanoseconds",
                        productionHelper=False, decisionsChanged=False, waitedForAuditor=False)


def initialize(lease):
    lease.research_trace = HelperTrace(lease)


def measured(name):
    stage = STAGES[name]
    def decorate(function):
        @functools.wraps(function)
        def wrapper(self, *args, **kwargs):
            trace = self.research_trace
            sequence = None
            try:
                sequence = trace.begin(stage, self.active)
            except BaseException:
                trace.observer_errors += 1
            outcome = "returned"
            try:
                return function(self, *args, **kwargs)
            except BaseException:
                outcome = "raised"
                raise
            finally:
                try:
                    trace.finish(sequence, outcome)
                except BaseException:
                    trace.observer_errors += 1
        return wrapper
    return decorate


def dump(lease):
    """Bounded journal chunks after close; never join or wait for the audit thread.

    A scope still running is explicit incomplete evidence. Printing failure must
    not replace close's original return/exception or grant a cleanup ACK.
    """
    try:
        value = lease.research_trace.snapshot()
        encoded = json.dumps(value, separators=(",", ":"), sort_keys=True).encode("ascii")
        if len(encoded) > 65536:
            raise ValueError("Research helper trace exceeds its dump bound")
        digest = hashlib.sha256(encoded).hexdigest()
        chunks = [encoded[i:i+3000].decode("ascii") for i in range(0, len(encoded), 3000)]
        for index, chunk in enumerate(chunks):
            print(json.dumps(dict(event="research-helper-trace", **lease.research_trace.identity,
                                  index=index, chunks=len(chunks), sha256=digest, data=chunk), separators=(",", ":")))
        sys.stdout.flush()
    except BaseException as error:
        try:
            print(json.dumps(dict(event="research-helper-trace-error", **lease.research_trace.identity,
                                  error=type(error).__name__)), flush=True)
        except BaseException:
            pass
