"""Join bounded journal helper durations to the exact observed lease and input."""
import csv
import hashlib
import json
from pathlib import Path
import re

from helper_trace import CAPACITY, FIELDS, STAGES


def require(value, message):
    if not value:
        raise ValueError(message)


def integer(value):
    return type(value) is int


def assemble(journal_rows, expected):
    chunks = {}
    identity = {name: expected[name] for name in ("leaseId", "ownerPid", "ownerStart", "helperPid")}
    count = digest = None
    for outer in journal_rows:
        try:
            message = json.loads(outer.get("MESSAGE", ""))
        except (ValueError, TypeError):
            continue
        if not isinstance(message, dict) or message.get("event") not in ("research-helper-trace", "research-helper-trace-error"):
            continue
        if message.get("leaseId") != identity["leaseId"]:
            continue
        require(message.get("event") == "research-helper-trace", "Helper observer failed while dumping this lease")
        require(all(message.get(key) == value for key, value in identity.items())
                and outer.get("_PID") == str(identity["helperPid"]), "Helper journal identity differs")
        index, declared, sha = message.get("index"), message.get("chunks"), message.get("sha256")
        require(integer(index) and integer(declared) and 1 <= declared <= 22 and 0 <= index < declared
                and type(sha) is str and re.fullmatch("[0-9a-f]{64}", sha)
                and type(message.get("data")) is str and len(message["data"].encode("ascii")) <= 3000,
                "Invalid helper journal chunk")
        require(index not in chunks, "Repeated helper journal chunk")
        if count is None:
            count, digest = declared, sha
        require((declared, sha) == (count, digest), "Conflicting helper dump metadata")
        chunks[index] = message["data"]
    require(count is not None and set(chunks) == set(range(count)), "Missing or truncated helper journal dump")
    encoded = "".join(chunks[index] for index in range(count)).encode("ascii")
    require(len(encoded) <= 65536 and hashlib.sha256(encoded).hexdigest() == digest, "Helper dump digest mismatch")
    value = json.loads(encoded)
    require(all(value.get(key) == expected[key] for key in identity), "Inner helper dump identity differs")
    return value, digest


def validate(value):
    require(value.get("schema") == 1 and value.get("kind") == "research-helper-durations"
            and value.get("productionHelper") is False and value.get("decisionsChanged") is False
            and value.get("waitedForAuditor") is False, "Unexpected helper trace scope")
    for name in ("openScopes", "lateFinishes", "observerErrors", "queuedAuditsNotStarted", "overwritten", "tailStart"):
        require(integer(value.get(name)) and value[name] == 0, "Incomplete helper scope/history: " + name)
    total = value.get("total")
    require(integer(total) and 0 < total <= CAPACITY and value.get("capacity") == CAPACITY
            and value.get("tailEnd") == total and value.get("fields") == FIELDS and value.get("stages") == STAGES,
            "Invalid helper ring metadata")
    rows = value.get("rows", [])
    require(type(rows) is list and len(rows) == total, "Incomplete helper rows")
    counted = {name: 0 for name in STAGES}
    for index, row in enumerate(rows):
        require(type(row) is list and len(row) == len(FIELDS), "Invalid helper row schema")
        data = dict(zip(FIELDS, row))
        require(integer(data["sequence"]) and data["sequence"] == index and integer(data["stage"])
                and data["stage"] in STAGES.values() and integer(data["thread"]) and data["thread"] > 0
                and type(data["active"]) is bool and data["outcome"] in ("returned", "raised"), "Invalid helper row identity/outcome")
        require(all(integer(data[field]) and data[field] >= 0 for field in FIELDS[4:10])
                and 0 < data["startNs"] <= data["endNs"] <= value["flushedNs"]
                and data["threadStartNs"] <= data["threadEndNs"]
                and data["processStartNs"] <= data["processEndNs"], "Invalid helper duration clocks")
        counted[next(name for name, number in STAGES.items() if data["stage"] == number)] += 1
    require(value.get("stageStarts") == counted, "Helper start counters differ from complete rows")
    require(counted["start_audit"] == counted["audit_foreign"], "Unobserved queued audit thread")
    return [dict(zip(FIELDS, row)) for row in rows]


def analyze_case(directory, helper_process):
    directory = Path(directory)
    samples = json.loads((directory / "process-samples.json").read_text())
    actor, broker = samples["before"]["application"], samples["before"]["broker"]
    require(dict(pid=broker["pid"], startTicks=broker["startTicks"]) == helper_process,
            "Measured service differs from observed helper process")
    require(samples["after"]["broker"]["pid"] == broker["pid"]
            and samples["after"]["broker"]["startTicks"] == broker["startTicks"], "Helper restarted during timing")
    lease = json.loads((directory / "lease-at-measurement.json").read_text())
    names = [row["name"] for row in lease["storedGrabs"]]
    matches = [re.fullmatch(r"madsense-grab-([0-9a-f]{32})-\d+", name) for name in names]
    require(matches and all(matches) and len({match[1] for match in matches}) == 1, "Lease grab identities differ")
    expected = dict(leaseId=matches[0][1], ownerPid=actor["pid"], ownerStart=actor["startTicks"], helperPid=broker["pid"])
    diagnostics = json.loads((directory / "diagnostics/result.json").read_text())
    command = diagnostics["commands"]["broker-journal"]
    require(command["complete"] and command["childReaped"] and command["returncode"] == 0
            and not command["timedOut"] and not command["outputLimitReached"] and not command["errors"],
            "Bounded broker journal collection is incomplete")
    recorded = command["stdout"]
    require(recorded.get("path") == "broker-journal.stdout", "Unexpected helper diagnostic stream path")
    data = (directory / "diagnostics" / recorded["path"]).read_bytes()
    require(len(data) == recorded["size"] <= 1024*1024 and hashlib.sha256(data).hexdigest() == recorded["sha256"]
            and not recorded["truncated"], "Collected helper journal bytes differ")
    trace, digest = assemble([json.loads(line) for line in data.splitlines()], expected)
    rows = validate(trace)
    with (directory / "samples.csv").open(newline="") as stream:
        inputs = [{key: int(item) for key, item in row.items()} for row in csv.DictReader(stream)]
    writes = [row for row in inputs if row["inject_before_ns"] and row["inject_after_ns"] and not row["write_errno"]]
    require(writes, "No injection interval available to join helper durations")
    start, end = writes[0]["inject_before_ns"], writes[-1]["inject_after_ns"]
    active = [row for row in rows if row["active"] and row["endNs"] >= start and row["startNs"] <= end]
    durations = {}
    for name, stage in STAGES.items():
        selected = [row for row in active if row["stage"] == stage]
        durations[name] = dict(count=len(selected), raised=sum(row["outcome"] == "raised" for row in selected),
            maximumWallNs=max((row["endNs"]-row["startNs"] for row in selected), default=None),
            maximumThreadCpuNs=max((row["threadEndNs"]-row["threadStartNs"] for row in selected), default=None),
            maximumProcessCpuNs=max((row["processEndNs"]-row["processStartNs"] for row in selected), default=None))
    gaps = []
    client = directory / "app/input-trace.csv"
    if client.exists():
        with client.open(newline="") as stream:
            client_rows = [{key: int(item) for key, item in row.items()} for row in csv.DictReader(stream)]
        accepted = [row for row in client_rows if row["stage"] == 1]
        for before, after in zip(accepted, accepted[1:]):
            if after["broker_read_ns"] - before["broker_read_ns"] < 2_000_000:
                continue
            overlapping = [row for row in active if row["startNs"] <= after["broker_read_ns"]
                           and row["endNs"] >= before["broker_read_ns"]]
            gaps.append(dict(beforeSequence=before["tag"] & 65535, afterSequence=after["tag"] & 65535,
                brokerGapNs=after["broker_read_ns"]-before["broker_read_ns"],
                beforeBrokerNs=before["broker_read_ns"], afterBrokerNs=after["broker_read_ns"],
                overlappingHelperSpans=overlapping))
    return dict(schema=1, kind="observed-helper-durations", identity=expected, dumpSha256=digest,
                completeLeaseRows=len(rows), injectionInterval=dict(startNs=start, endNs=end),
                activeOverlappingDurations=durations, retainedClientBrokerGapsAtLeast2Ms=gaps,
                sourceHelperChanged=True, functionalOriginalHelperCoverageTransferred=False,
                schedulingCauseEstablished=False, gilCauseEstablished=False,
                physicalLatencyValidated=False, observerPerturbationQuantified=False)
