"""Repeated rates with reversed control/trace order; no performance threshold."""


def schedule(trace_kind="stages"):
    if trace_kind == "input":
        return [("1000hz-control-1", 1000, False), ("1000hz-input-1", 1000, True),
                ("1000hz-input-2", 1000, True), ("1000hz-control-2", 1000, False)]
    if trace_kind != "stages":
        raise ValueError("Unknown research trace kind")
    rows = []
    for rate, flags in ((100, (False, True, True, False)), (1000, (True, False, False, True))):
        counters = {False: 0, True: 0}
        for instrumented in flags:
            counters[instrumented] += 1
            kind = "trace" if instrumented else "control"
            rows.append((f"{rate}hz-{kind}-{counters[instrumented]}", rate, instrumented))
    return rows
