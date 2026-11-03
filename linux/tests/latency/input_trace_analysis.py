"""Validate a recent client-input tail; never infer absence in overwritten history."""
import csv
import io
from collections import Counter

from analyze import summarize

FIELDS = ["slot", "ticks", "stage", "tag", "sequence8", "broker_read_ns", "received_ns",
          "queue_count", "last_read_started_ns", "thread"]
STAGES = dict(accept=1, enqueue=2, enqueueFailed=3, dequeue=4)


def require(value, message):
    if not value:
        raise ValueError(message)


def integer(value):
    return type(value) is int


def analyze(trace_stream, metadata, sample_stream, probe):
    require(metadata.get("schema") == 1 and metadata.get("kind") == "client-input-tail"
            and metadata.get("productionBinary") is False and metadata.get("writersFinished") is True
            and metadata.get("dumpedAfterBridgeDispose") is True, "Incomplete input-tail scope/publication")
    require(all(integer(metadata.get(name)) and metadata[name] == 0 for name in ("holes", "collisions", "badSequence")),
            "Collisions, holes or malformed reports invalidate the tail")
    require(metadata.get("timestampFrequency") == 1_000_000_000 and metadata.get("stages") == STAGES,
            "Unexpected input clock or stages")
    clock = metadata.get("clockValidation", {})
    require(clock.get("nativeClockId") == 1 and clock.get("bracketedSamples") == 32
            and integer(clock.get("minimumBracketNs")) and integer(clock.get("maximumBracketNs"))
            and 0 <= clock["minimumBracketNs"] <= clock["maximumBracketNs"], "Unverified monotonic clock")
    nonce, total = metadata.get("nonce"), metadata.get("totalAttempts")
    require(integer(nonce) and 0x8000 <= nonce <= 0xffff and probe.get("nonce") == nonce,
            "Input tail and generator nonce differ")
    require(integer(total) and total >= 0 and metadata.get("capacity") == 4096, "Invalid tail capacity/count")
    first = max(0, total - 4096)
    require(all(integer(metadata.get(name)) for name in ("tailStart", "tailEnd", "retained", "overwritten"))
            and metadata.get("tailStart") == first and metadata.get("tailEnd") == total
            and metadata.get("retained") == total - first and metadata.get("overwritten") == first,
            "Tail bounds/counts are inconsistent")
    require(metadata.get("gcPauseAttribution") is False, "GC counts cannot attribute a pause")
    for name in ("gcBefore", "gcAfter"):
        value = metadata.get(name, {})
        counts = value.get("collectionCounts", [])
        require(len(counts) == 3 and all(integer(n) and n >= 0 for n in counts)
                and all(integer(value.get(key)) and value[key] >= 0
                        for key in ("totalAllocatedBytes", "lastCollectionIndex")), "Invalid GC snapshot")
    before_gc, after_gc = metadata["gcBefore"], metadata["gcAfter"]
    require(all(b <= a for b, a in zip(before_gc["collectionCounts"], after_gc["collectionCounts"]))
            and before_gc["totalAllocatedBytes"] <= after_gc["totalAllocatedBytes"]
            and before_gc["lastCollectionIndex"] <= after_gc["lastCollectionIndex"], "GC counters went backwards")

    sample_text = sample_stream.read()
    summary = summarize(io.StringIO(sample_text), probe)  # Exact independent virtual reader gate.
    samples = list(csv.DictReader(io.StringIO(sample_text)))
    reader = csv.DictReader(trace_stream)
    require(reader.fieldnames == FIELDS, "Unexpected input-tail columns")
    rows, tags, events = [], {}, Counter()
    for index, raw in enumerate(reader):
        require(index < total - first and None not in raw and all(value is not None for value in raw.values()),
                "Malformed/excess input-tail row")
        row = {key: int(value) for key, value in raw.items()}
        sequence, stage = row["tag"] & 0xffff, row["stage"]
        require(row["slot"] == first + index and row["tag"] >> 16 == nonce
                and sequence < len(samples) and row["sequence8"] == sequence % 256
                and stage in STAGES.values(), "Input-tail identity, slot or stage differs")
        sample = samples[sequence]
        injected = int(sample["inject_before_ns"])
        require(injected > 0 and int(sample["inject_after_ns"]) > 0 and int(sample["write_errno"]) == 0,
                "Input trace refers to an unwritten sequence")
        require(injected <= row["broker_read_ns"] <= row["received_ns"] <= row["ticks"]
                and 0 <= row["last_read_started_ns"] <= row["ticks"] and row["thread"] > 0
                and 0 <= row["queue_count"] <= 8, "Invalid input-tail timestamps/count snapshot")
        by_stage = tags.setdefault(sequence, {})
        require(stage not in by_stage, "Duplicate input tag/stage")
        by_stage[stage] = row
        rows.append(row)
        events[stage] += 1
    require(len(rows) == total - first, "Truncated input tail")

    complete = 0
    for sequence, stages in tags.items():
        require(not (2 in stages and 3 in stages), "One tagged input both enqueued and rejected")
        pairs = {(r["broker_read_ns"], r["received_ns"]) for r in stages.values()}
        require(len(pairs) == 1, "Input tag changed its source timestamps")
        if 1 in stages:
            require(2 in stages or 3 in stages, "Retained accept has no enqueue outcome")
            require(all(r["ticks"] >= stages[1]["ticks"] for r in stages.values()), "Input event precedes accept")
        elif first == 0:
            require(False, "Input predecessor missing without overwritten history")
        if 3 in stages:
            require(4 not in stages, "Rejected input was dequeued")
        if 4 in stages and 1 in stages:
            require(2 in stages, "Retained dequeue has no successful enqueue")
            complete += 1
        # TryWrite publishes before the enqueue hook; dequeue can precede that hook.
        observed = int(samples[sequence]["observed_ns"])
        if observed and 1 in stages:
            require(4 in stages, "Observed retained accept has no dequeue")
        if observed and 4 in stages:
            require(stages[4]["ticks"] <= observed, "Virtual read precedes physical dequeue")

    if first == 0:
        require(all(not int(sample["observed_ns"]) or 4 in tags.get(sequence, {})
                    for sequence, sample in enumerate(samples)),
                "Virtual reader observed input absent from complete client history")

    accepts = [r for r in rows if r["stage"] == 1]
    dequeues = [r for r in rows if r["stage"] == 4]
    for values in (accepts, dequeues):
        require(all(a["tag"] < b["tag"] and a["ticks"] <= b["ticks"]
                    and a["broker_read_ns"] <= b["broker_read_ns"]
                    and a["received_ns"] <= b["received_ns"] for a, b in zip(values, values[1:])),
                "Client input sequence/timestamps are reordered")
    def gaps(values, key):
        return [b[key] - a[key] for a, b in zip(values, values[1:])]
    def bounds(values):
        return dict(count=len(values), minimumNs=min(values) if values else None,
                    maximumNs=max(values) if values else None)
    failures = []
    for row in rows:
        if row["stage"] != 3:
            continue
        previous = [r for r in accepts if r["received_ns"] < row["received_ns"]]
        prior_dequeues = [r for r in dequeues if r["ticks"] <= row["ticks"]]
        previous = previous[-1] if previous else None
        failures.append(dict(sequence=row["tag"] & 0xffff, eventNs=row["ticks"],
            brokerReadNs=row["broker_read_ns"], receivedNs=row["received_ns"],
            queueCountSnapshot=row["queue_count"], lastReadStartedNs=row["last_read_started_ns"],
            lastRetainedDequeueNs=prior_dequeues[-1]["ticks"] if prior_dequeues else None,
            previousRetainedReceiveGapNs=row["received_ns"] - previous["received_ns"] if previous else None,
            previousRetainedBrokerGapNs=row["broker_read_ns"] - previous["broker_read_ns"] if previous else None))
    return dict(schema=1, scope="client-input-tail", productionBinary=False,
        retainedEvents=len(rows), totalAttempts=total, overwrittenEvents=first,
        fullHistoryRetained=first == 0, completeRetainedInputs=complete,
        stageEvents={name: events[number] for name, number in STAGES.items()},
        retainedAcceptReceiveGaps=bounds(gaps(accepts, "received_ns")),
        retainedAcceptBrokerGaps=bounds(gaps(accepts, "broker_read_ns")),
        retainedDequeueGaps=bounds(gaps(dequeues, "ticks")),
        retainedBrokerToReceive=bounds([r["received_ns"] - r["broker_read_ns"] for r in accepts]),
        retainedReceiveToDequeue=bounds([r["ticks"] - r["received_ns"] for r in dequeues]),
        enqueueFailures=failures, virtualReaderResult=summary["result"],
        gcCollectionDeltas=[a - b for b, a in zip(before_gc["collectionCounts"], after_gc["collectionCounts"])],
        gcAllocatedBytesDelta=after_gc["totalAllocatedBytes"] - before_gc["totalAllocatedBytes"],
        schedulingCauseEstablished=False, gcPauseAttribution=False,
        observerPerturbationQuantified=False, physicalLatencyValidated=False,
        overwrittenHistoryAbsenceInferred=False)
