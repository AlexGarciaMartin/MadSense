"""Read-only SDL API-phase correlation; does not replace stop acceptance gates."""
import argparse
import json
from pathlib import Path

from consumer_session import applies_rumble, bt_state, expected_motors, require


def phase_windows(observed, command):
    require(observed.get("phaseClock") == "CLOCK_MONOTONIC" and observed.get("phaseTimingValid") is True,
            "Missing valid Linux monotonic phase evidence")
    rows = observed.get("phases", [])
    require(isinstance(rows, list) and 1 <= len(rows) <= 16, "Unexpected phase list")
    phases = {}
    previous = command["startedMonotonicNs"]
    for row in rows:
        name, timestamp = row.get("name"), row.get("monotonicNs")
        require(isinstance(name, str) and name not in phases and type(timestamp) is int
                and 0 < previous <= timestamp <= command["endedMonotonicNs"], "Invalid, duplicate or unordered phase")
        phases[name] = timestamp
        previous = timestamp
    names = ["rumble-call-before", "rumble-call-after", "stop-call-before", "stop-call-after", "stop-observation-end"]
    led = observed.get("capabilities", {}).get("rgbLed") is True
    if led:
        names += ["led-call-before", "led-call-after"]
    names += ["output-observation-end", "close-before", "close-after", "quit-before", "quit-after"]
    require(list(phases) == names, "Phase sequence does not isolate stop from LED and close")
    duration = observed.get("rumbleDurationMs")
    require(type(duration) is int and duration >= 60000 and observed.get("explicitStopBeforeAutoExpiry") is True
            and phases["stop-call-before"] - phases["rumble-call-before"] < duration * 1_000_000,
            "Automatic rumble expiry cannot be excluded")
    require(phases["stop-observation-end"] - phases["stop-call-after"] >= 300_000_000,
            "Explicit stop lacks the complete separate observation interval")
    windows = {
        "rumble": (phases["rumble-call-before"], phases["stop-call-before"]),
        "explicit-stop": (phases["stop-call-before"], phases["stop-observation-end"]),
        "close": (phases["close-before"], phases["quit-after"]),
        "after-close": (phases["quit-after"], command["endedMonotonicNs"] + 1),
    }
    if led:
        windows["led"] = (phases["led-call-before"], phases["output-observation-end"])
    return windows


def decoded_fields(value):
    selectors = ("rumbleEmulationEnabled", "useRumbleNotHaptics", "improvedRumbleEmulationEnabled", "lightbarColorAllowed")
    require(all(type(value.get(name)) is bool for name in selectors), "Missing decoded USB selector fields")
    require(all(type(value.get(name)) is int and 0 <= value[name] <= 255
                for name in ("rumbleLeft", "rumbleRight", "red", "green", "blue")), "Missing decoded USB byte fields")
    return dict(selectedRumble=value["useRumbleNotHaptics"] and
                (value["rumbleEmulationEnabled"] or value["improvedRumbleEmulationEnabled"]),
        selectorsClear=not any(value[name] for name in selectors[:3]),
        left=value["rumbleLeft"], right=value["rumbleRight"], rgbSelected=value["lightbarColorAllowed"],
        rgb=[value["red"], value["green"], value["blue"]])


def bluetooth_fields(data):
    payload = bt_state(data)  # validates report size and Bluetooth CRC, including audio-state framing
    if payload is None:
        return None
    return dict(selectedRumble=applies_rumble(payload),
        selectorsClear=not (payload[0] & 3 or payload[38] & 4),
        left=payload[3], right=payload[2], rgbSelected=bool(payload[1] & 4), rgb=list(payload[44:47]),
        flags0=payload[0], flags1=payload[1], flags2=payload[38], payload47Hex=payload.hex())


def describe(fields, left, right):
    if fields is None:
        return None
    return fields | dict(
        selectedMarker=fields["selectedRumble"] and fields["left"] == left and fields["right"] == right,
        selectedZero=fields["selectedRumble"] and fields["left"] == fields["right"] == 0,
        selectorClearZeroFields=fields["selectorsClear"] and fields["left"] == fields["right"] == 0,
        rgbMarker=fields["rgbSelected"] and fields["rgb"] == [0x12, 0x34, 0x56])


def correlate(observed, command, core_rows, bluetooth_rows, frequency, left=0x12, right=0x56):
    # The owned Linux .NET10 fixture uses CLOCK_MONOTONIC nanoseconds. Reject
    # another frequency rather than imply another platform shares its epoch.
    require(type(frequency) is int and frequency == 1_000_000_000, "Unsupported core clock frequency")
    windows = phase_windows(observed, command)
    result = dict(schema=1, scope="same-owned-Linux-guest-monotonic-phases", label=command["label"],
        usbEvidence="decoded USB report 0x02 fields; complete USB bytes unavailable",
        completeUsbBytesCaptured=False, physicalActuationVerified=False, latencyValidated=False,
        causalOneToOneMappingVerified=False, changesStopAcceptance=False, phases={})
    for name, (start, end) in windows.items():
        usb, bt = [], []
        for row in core_rows:
            if row.get("kind") == "virtual-output" and start <= row["timestamp"] < end:
                usb.append(dict(monotonicNs=row["timestamp"], decoded=row["value"],
                                classification=describe(decoded_fields(row["value"]), left, right)))
        for row in bluetooth_rows:
            if row.get("kind") == "output" and start <= row["monotonicNs"] < end:
                bt.append(dict(monotonicNs=row["monotonicNs"], hex=row["hex"],
                               classification=describe(bluetooth_fields(bytes.fromhex(row["hex"])), left, right)))
        def count(rows, flag):
            return sum(bool(row["classification"] and row["classification"][flag]) for row in rows)
        result["phases"][name] = dict(startedMonotonicNs=start, endedMonotonicNsExclusive=end,
            decodedUsb02=usb, bluetoothOutputs=bt,
            usbSelectedMarkerCount=count(usb, "selectedMarker"), btSelectedMarkerCount=count(bt, "selectedMarker"),
            usbSelectedZeroCount=count(usb, "selectedZero"), btSelectedZeroCount=count(bt, "selectedZero"),
            usbSelectorClearZeroFieldsCount=count(usb, "selectorClearZeroFields"),
            btSelectorClearZeroFieldsCount=count(bt, "selectorClearZeroFields"))
    stop = result["phases"]["explicit-stop"]
    result["explicitStop"] = dict(apiAccepted=observed["marker"]["stopAccepted"],
        selectedZeroObservedUsb=stop["usbSelectedZeroCount"] > 0,
        selectedZeroObservedBt=stop["btSelectedZeroCount"] > 0,
        selectorClearZeroFieldsObservedUsb=stop["usbSelectorClearZeroFieldsCount"] > 0,
        selectorClearZeroFieldsObservedBt=stop["btSelectorClearZeroFieldsCount"] > 0,
        zeroFieldsAloneProveMotorStop=False, independentStopAssertionUnchanged=True)
    return result


def read_jsonl(path):
    path = Path(path)
    require(path.stat().st_size <= 64 * 1024 * 1024, "Event evidence exceeds 64 MiB")
    lines = path.read_text().splitlines()
    return [json.loads(line) for line in lines if line]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", required=True, type=Path)
    parser.add_argument("--core-events", required=True, type=Path)
    parser.add_argument("--core-ready", required=True, type=Path)
    parser.add_argument("--uhid-events", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    suite = json.loads(args.suite.read_text())
    core_ready = json.loads(args.core_ready.read_text())
    require(core_ready.get("ownedGuestToken") == suite["ready"]["ownedGuestToken"], "Core and consumer guest identity differ")
    core, bt = read_jsonl(args.core_events), read_jsonl(args.uhid_events)
    results = []
    for command in suite["consumers"]:
        observed = command.get("evidence", {})
        if observed.get("sdlMajor") not in (2, 3):
            continue
        try:
            left, right = expected_motors(suite["ready"], observed)
            result = correlate(observed, command, core, bt, core_ready["timestampFrequency"], left, right)
        except Exception as error:
            result = dict(label=command["label"], correlationAvailable=False, reason=f"{type(error).__name__}: {error}")
        results.append(result)
    with args.output.open("x") as stream:
        json.dump(dict(schema=1, stopAcceptanceChanged=False, consumers=results), stream, indent=2)
        stream.write("\n")


if __name__ == "__main__":
    main()
