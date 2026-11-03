"""Explicit variant selection shared by guest and independent host verification."""
import json


def kind(overlay):
    value = overlay.get("traceKind", "stages")
    if value not in ("stages", "input"):
        raise ValueError("Unknown source overlay trace kind")
    return value


def analyze_directory(directory, overlay, samples, probe):
    selected = kind(overlay)
    if selected == "input":
        from input_trace_analysis import analyze
        stem, other = "input-trace", "report-trace"
    else:
        from trace_analysis import analyze
        stem, other = "report-trace", "input-trace"
    if any((directory / (other + suffix)).exists() for suffix in (".csv", ".json")):
        raise ValueError("Mixed instrumentation variants")
    with (directory / (stem + ".csv")).open(newline="") as stream:
        return analyze(stream, json.loads((directory / (stem + ".json")).read_text()), samples, probe)


def require_control(directory):
    if any((directory / (stem + suffix)).exists()
           for stem in ("input-trace", "report-trace") for suffix in (".csv", ".json")):
        raise ValueError("Instrumented evidence labelled as control")
