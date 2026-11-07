"""Attribute absent tagged reports without calling an internal hook a reader.

The normal CSV reader remains authoritative. This analyzer accepts only a
bounded, fully published trace from the experimental actor after disposal.
Stage presence localizes a gap; it does not establish its scheduling cause.
"""
from bisect import bisect_right
import csv
import io
from collections import Counter
from analyze import summarize

FIELDS = ["slot", "ticks", "stage", "origin", "generation", "tag", "sequence8"]
STAGES = {"parsedUsb": 1, "rawSlot": 2, "interruptPayload": 3, "sendReturned": 4}
ORIGINS = {"none": 0, "periodic": 1, "freshWorker": 2}
LABELS = {1: "before-parser", 2: "parser-to-slot", 3: "slot-to-interrupt",
          4: "send-not-confirmed", 5: "after-send-return"}
EVENT_FIELDS = ["slot", "ticks", "kind", "origin", "generation", "extra"]
EVENTS = {"urbEnqueued": 5, "scheduleRequested": 6, "workerStarted": 7, "gateEntered": 8,
          "skippedStale": 9, "noUrbPending": 10, "workerExit": 11, "blockedNoReport": 12}
EVENT_CAPACITY = 262144
GATE_EVENTS = (8, 9, 10, 12)
GAP_LABELS = ("slot-replaced-before-gate-entry", "gate-entry-without-pending-urb",
              "gate-entry-skipped-stale", "gate-entry-blocked-no-report", "gate-entry-other")


def require(value, message):
    if not value:
        raise ValueError(message)


def load_events(event_stream, metadata):
    """Bounded, fully published delivery events of the same instrumented actor."""
    require(metadata.get("events") == EVENTS and metadata.get("eventCapacity") == EVENT_CAPACITY,
            "Unknown delivery event kinds")
    count = metadata.get("eventsRecorded")
    require(isinstance(count, int) and 0 <= count <= EVENT_CAPACITY and metadata.get("eventsAttempted") == count
            and metadata.get("eventOverflow") == 0, "Overflowed or incomplete event recording")
    reader = csv.DictReader(event_stream)
    require(reader.fieldnames == EVENT_FIELDS, "Unexpected event columns")
    events = []
    for index, raw in enumerate(reader):
        require(index < count and None not in raw, "Too many or malformed event rows")
        row = {key: int(value) for key, value in raw.items()}
        require(row["slot"] == index and row["ticks"] > 0 and row["kind"] in EVENTS.values(), "Invalid event row")
        if row["kind"] in GATE_EVENTS:
            require(row["origin"] in (1, 2) and row["generation"] >= 0, "Gate event without a delivery origin")
        else:
            require(row["origin"] == 0, "Origin on a non-gate event")
        events.append(row)
    require(len(events) == count, "Truncated event CSV")
    # Concurrent writers reserve slots after reading the clock; order by time.
    events.sort(key=lambda row: (row["ticks"], row["slot"]))
    return events


def attribute_slot_gaps(events, traces, sequences):
    """Describe the delivery events between a lost sequence's raw-slot publication and the next one."""
    slots = sorted((min(row["ticks"] for row in stages[2]), sequence)
                   for sequence, stages in traces.items() if 2 in stages)
    times = [ticks for ticks, _ in slots]
    labels = Counter({label: 0 for label in GAP_LABELS})
    details = {}
    for sequence in sequences:
        start = min(row["ticks"] for row in traces[sequence][2])
        index = bisect_right(times, start)
        end, replacement = (slots[index] if index < len(slots) else (None, None))
        window = [row for row in events if row["ticks"] >= start and (end is None or row["ticks"] < end)]
        counts = Counter(row["kind"] for row in window)
        gate = [dict(origin=row["origin"], afterSlotNs=row["ticks"] - start,
                     generationsBehind=row["generation"] - row["extra"])
                for row in window if row["kind"] == 8]
        if not gate:
            label = GAP_LABELS[0]
        elif counts[10]:
            label = GAP_LABELS[1]
        elif counts[9]:
            label = GAP_LABELS[2]
        elif counts[12]:
            label = GAP_LABELS[3]
        else:
            label = GAP_LABELS[4]
        labels[label] += 1
        details[sequence] = dict(label=label, windowNs=None if end is None else end - start,
                                 replacedBySequence=replacement,
                                 events={name: counts[value] for name, value in EVENTS.items()},
                                 workersQueued=sum(1 for row in window if row["kind"] == 6 and row["extra"] == 1),
                                 gateEntries=gate)
    return dict(labels=dict(labels), sequences=details)


def analyze(trace_stream, metadata, sample_stream, probe, event_stream=None):
    require(metadata.get("schema") == 1 and metadata.get("kind") == "instrumented-research-copy"
            and metadata.get("productionBinary") is False and metadata.get("writersFinished") is True
            and metadata.get("dumpedAfterBridgeDispose") is True, "Trace scope/publication is incomplete")
    require(metadata.get("overflow") == 0 and metadata.get("badSequence") == 0,
            "Overflowed or malformed trace cannot attribute absent reports")
    require(metadata.get("timestampFrequency") == 1_000_000_000,
            "Linux nanosecond Stopwatch clock required for this join")
    clock = metadata.get("clockValidation", {})
    require(clock.get("nativeClockId") == 1 and clock.get("bracketedSamples") == 32
            and type(clock.get("minimumBracketNs")) is int and type(clock.get("maximumBracketNs")) is int
            and 0 <= clock["minimumBracketNs"] <= clock["maximumBracketNs"],
            "Missing direct Stopwatch/CLOCK_MONOTONIC bracketing")
    require(metadata.get("stages") == STAGES and metadata.get("origins") == ORIGINS,
            "Unknown trace stages/origins")
    nonce = metadata.get("nonce")
    require(isinstance(nonce, int) and 0x8000 <= nonce <= 0xffff and probe.get("nonce") == nonce,
            "Trace and C producer nonce differ")
    count = metadata.get("recorded")
    require(isinstance(count, int) and metadata.get("capacity") == 131072 and 0 <= count <= 131072
            and metadata.get("attempted") == count, "Incomplete bounded recording")
    reader = csv.DictReader(trace_stream)
    require(reader.fieldnames == FIELDS, "Unexpected trace columns")
    traces = {}
    per_stage = Counter()
    per_origin = Counter()
    for index, raw in enumerate(reader):
        require(index < count and None not in raw, "Too many or malformed trace rows")
        row = {key: int(value) for key, value in raw.items()}
        stage, tag = row["stage"], row["tag"]
        sequence = tag & 0xffff
        require(row["slot"] == index and row["ticks"] > 0 and tag >> 16 == nonce
                and sequence < probe["nominalSamples"] and row["sequence8"] == sequence % 256,
                "Trace row identity/sequence differs from producer")
        require(stage in STAGES.values(), "Invalid trace stage")
        if stage in (1, 2):
            require(row["origin"] == 0 and row["generation"] == 0, "Origin on a non-delivery stage")
        else:
            require(row["origin"] in (1, 2) and row["generation"] > 0, "Missing delivery origin/generation")
            per_origin[(stage, row["origin"])] += 1
        traces.setdefault(sequence, {}).setdefault(stage, []).append(row)
        per_stage[stage] += 1
    require(sum(per_stage.values()) == count, "Truncated trace CSV")
    sample_text = sample_stream.read()
    summarize(io.StringIO(sample_text), probe)  # Original strict independent reader gate.
    samples = list(csv.DictReader(io.StringIO(sample_text)))
    require(len(samples) == probe["nominalSamples"], "Wrong sample population")
    missing = {label: [] for label in LABELS.values()}
    unique = Counter()
    observed_count = written_count = 0
    for index, raw in enumerate(samples):
        sequence = int(raw["sequence"])
        require(sequence == index, "Sample sequence order differs")
        before, after, observed = (int(raw[name]) for name in
                                  ("inject_before_ns", "inject_after_ns", "observed_ns"))
        stages = traces.get(sequence, {})
        written = before > 0 and after > 0 and int(raw["write_errno"]) == 0
        if not written:
            require(not stages and not observed, "Trace contains an unwritten producer sequence")
            continue
        written_count += 1
        for stage, rows in stages.items():
            require(all(row["ticks"] >= before for row in rows), "Trace precedes this nonce's injection")
            unique[stage] += 1
        # Copies can race newer publication, but every emitted tag must have
        # passed each preceding stage at least once. Do not reorder hook time
        # against atomic slot index: parallel writers reserve indices later.
        for stage in range(2, 5):
            if stage in stages:
                require(stage - 1 in stages and min(x["ticks"] for x in stages[stage]) >=
                        min(x["ticks"] for x in stages[stage - 1]), "Impossible stage presence/order")
        if observed:
            observed_count += 1
            require(3 in stages and min(x["ticks"] for x in stages[3]) <= observed,
                    "Reader received a sequence absent from the recorded send payloads")
            # Kernel reads can finish before the writer returns. No requirement
            # that sendReturned precede the observed timestamp is correct here.
            continue
        first_absent = next((stage for stage in range(1, 5) if stage not in stages), 5)
        missing[LABELS[first_absent]].append(sequence)
    require(written_count == probe["written"] and observed_count == probe["matched"],
            "C counters and joined samples differ")
    result = dict(schema=1, scope="instrumented-research-copy", physicalLatencyValidated=False,
                  written=written_count, observed=observed_count,
                  stageEvents={name: per_stage[value] for name, value in STAGES.items()},
                  stageUnique={name: unique[value] for name, value in STAGES.items()},
                  deliveryEvents={name: {label: per_origin[(stage, origin)] for label, origin in ORIGINS.items() if origin}
                                  for name, stage in STAGES.items() if stage >= 3},
                  missingByLastBoundary=missing,
                  missing=sum(map(len, missing.values())),
                  schedulingCauseEstablished=False,
                  observerPerturbationQuantified=False)
    require((event_stream is None) == ("events" not in metadata), "Event recording and event evidence must match")
    if event_stream is not None:
        events = load_events(event_stream, metadata)
        result["eventCounts"] = {name: sum(1 for row in events if row["kind"] == value) for name, value in EVENTS.items()}
        result["slotToInterruptAttribution"] = attribute_slot_gaps(events, traces, missing["slot-to-interrupt"])
    return result
