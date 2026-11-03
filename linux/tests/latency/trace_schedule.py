"""Repeated rates with reversed control/trace order; no performance threshold."""


def schedule():
    rows = []
    for rate, flags in ((100, (False, True, True, False)), (1000, (True, False, False, True))):
        counters = {False: 0, True: 0}
        for instrumented in flags:
            counters[instrumented] += 1
            kind = "trace" if instrumented else "control"
            rows.append((f"{rate}hz-{kind}-{counters[instrumented]}", rate, instrumented))
    return rows
