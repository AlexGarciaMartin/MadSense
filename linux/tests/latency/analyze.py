"""Validate timing evidence; no latency threshold or physical-radio claim."""
import csv
import math


FIELDS = ("sequence", "scheduled_ns", "inject_before_ns", "inject_after_ns",
          "observed_ns", "duplicates", "write_errno", "deadline_skipped")


def distribution(values):
    """Nearest-rank percentiles, nanoseconds; an empty population stays null."""
    ordered = sorted(values)
    if not ordered:
        return None
    return {"count": len(ordered), "min": ordered[0], "p50": ordered[math.ceil(len(ordered) * .5) - 1],
            "p95": ordered[math.ceil(len(ordered) * .95) - 1],
            "p99": ordered[math.ceil(len(ordered) * .99) - 1], "max": ordered[-1]}


def summarize(stream, probe):
    reader = csv.DictReader(stream)
    if reader.fieldnames != list(FIELDS):
        raise ValueError("Unknown timing CSV schema")
    rows = []
    for row in reader:
        if set(row) != set(FIELDS) or any(value is None for value in row.values()):
            raise ValueError("Malformed sample")
        row = {key: int(value) for key, value in row.items()}
        if min(row.values()) < 0 or row["sequence"] != len(rows) or row["deadline_skipped"] not in (0, 1):
            raise ValueError("Negative, out-of-order or repeated sample")
        rows.append(row)
    if (probe.get("schema") != 1 or probe.get("samplesSaved") is not True or probe.get("rate") not in (100, 1000)
            or len(rows) != probe["rate"] * 10 or probe.get("nominalSamples") != len(rows)):
        raise ValueError("Incomplete probe evidence")
    attempted = written = skipped = observed = duplicates = 0
    lower, upper, lateness, write_cost, missing = [], [], [], [], []
    for row in rows:
        scheduled, before, after, arrival = (row[key] for key in FIELDS[1:5])
        if row["deadline_skipped"]:
            if not scheduled or before or after or arrival or row["write_errno"] or row["duplicates"]:
                raise ValueError("Skipped deadline also claims transport activity")
            skipped += 1
        elif before:
            if not scheduled or before < scheduled or after < before:
                raise ValueError("Impossible injection timestamps")
            attempted += 1
            lateness.append(before - scheduled)
            write_cost.append(after - before)
            if not row["write_errno"]:
                written += 1
                if not arrival:
                    missing.append(row["sequence"])
        elif after or arrival or row["write_errno"] or row["duplicates"]:
            raise ValueError("Transport observation without an injection")
        if arrival:
            if row["write_errno"] or arrival < before:
                raise ValueError("Observation predates or contradicts the injection")
            observed += 1
            lower.append(max(0, arrival - after))
            upper.append(arrival - before)
        if row["duplicates"] and not arrival:
            raise ValueError("Duplicates without a first observation")
        duplicates += row["duplicates"]
    for field, count in (("attempted", attempted), ("written", written), ("deadlineSkipped", skipped), ("matched", observed)):
        if probe.get(field) != count:
            raise ValueError(f"CSV/probe count mismatch: {field}")
    for field in ("fatalErrno", "malformedMatches", "inputReads", "unmatched", "reordered"):
        if type(probe.get(field)) is not int or probe[field] < 0:
            raise ValueError(f"Invalid counter: {field}")
    if probe["inputReads"] != observed + duplicates + probe["unmatched"] + probe["malformedMatches"]:
        raise ValueError("Unaccounted virtual reads")
    if probe["malformedMatches"]:
        raise ValueError("Tagged payload was altered; latency evidence is invalid")
    if not probe["fatalErrno"] and attempted + skipped != len(rows):
        raise ValueError("Unaccounted nominal injection slots")
    classification = ("no-samples" if not observed else "transport-limit" if missing or probe["fatalErrno"]
                      else "generator-limit" if skipped else "measured")
    return dict(result=classification,
                rateHz=probe["rate"], seconds=10, nominalSamples=len(rows), attempted=attempted,
                written=written, observed=observed, producerDeadlineSkipped=skipped,
                unattempted=len(rows) - attempted - skipped, missingWrittenSequences=missing,
                duplicateReads=duplicates, reorderedReads=probe["reordered"],
                unrelatedReads=probe["unmatched"], transportErrno=probe["fatalErrno"],
                elapsedFromInjectionLowerNs=distribution(lower), elapsedFromInjectionUpperNs=distribution(upper),
                producerLatenessNs=distribution(lateness), uhidWriteDurationNs=distribution(write_cost),
                percentileMethod="nearest-rank", cleanupConfirmed=False)
