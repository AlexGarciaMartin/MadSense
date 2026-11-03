"""Optional ordinary-user Xbox evdev FF delivery evidence, never production code."""
import json
import hashlib
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "research"))
from consumer_session import ConsumerSession, applies_rumble, bt_state, read_rows, require

PHASES = ("start-call-before", "start-call-after", "stop-call-before", "stop-call-after",
          "stop-observation-end", "erase-before", "erase-after", "close-before", "close-after")
CASE = "app_xbox_evdev_force_feedback_marker_and_explicit_stop_delivered"


def verify_delivery(record, rows):
    observed = record.get("evidence", {})
    require(record.get("exitCode") == 0 and observed.get("result") == "passed", "FF consumer failed")
    require((observed.get("bus"), observed.get("vendor"), observed.get("product")) == (3, 0x045e, 0x028e),
            "FF consumer identity is not the Xbox USB persona")
    require(all(observed.get(key) is True for key in ("identityVerified", "ffRumble", "uploaded", "started", "stopped", "erased",
            "phaseTimingValid", "explicitStopBeforeAutoExpiry")), "FF lifecycle evidence incomplete")
    require(observed.get("phaseClock") == "CLOCK_MONOTONIC" and observed.get("effectDurationMs") == 60000,
            "FF explicit stop lacks a separate long-duration effect")
    phases = observed.get("phases", [])
    require([row.get("name") for row in phases] == list(PHASES), "FF phases missing, repeated or out of order")
    times = [row.get("monotonicNs") for row in phases]
    require(all(type(value) is int and value > 0 for value in times) and times == sorted(times),
            "Invalid FF phase timestamps")
    require(record["startedMonotonicNs"] <= times[0] <= times[-1] <= record["endedMonotonicNs"],
            "FF phases escape their process window")
    require(times[4] - times[3] >= 300_000_000 and times[4] - times[0] < 60_000_000_000,
            "FF stop observation overlaps expiry or lacks its separate window")
    require(observed.get("marker") == dict(strong=0x1234, weak=0x5678, expectedXboxLeftMotor=0x12,
                                          expectedXboxRightMotor=0x56), "Unexpected FF marker")
    marker = stopped = None
    for row in rows:
        timestamp = row.get("monotonicNs", -1)
        if row.get("kind") != "output" or not times[0] <= timestamp <= times[4]:
            continue
        data = bytes.fromhex(row["hex"])
        state = bt_state(data)  # Complete framing and A2 CRC validation.
        if state is None or not applies_rumble(state):
            continue
        if timestamp < times[2] and state[2:4] == bytes((0x56, 0x12)):
            marker = dict(monotonicNs=timestamp, hex=data.hex())
        if times[2] <= timestamp <= times[4] and timestamp < times[5] and state[2:4] == b"\0\0":
            stopped = dict(monotonicNs=timestamp, hex=data.hex())
    require(marker is not None, "FF marker did not reach Bluetooth before explicit stop")
    require(stopped is not None, "Selected zero rumble did not reach Bluetooth before erase/close/expiry")
    return dict(outputDeliveryVerified=True, stopReportForwarded=True, physicalActuationVerified=False,
                marker=marker, explicitStop=stopped, phases=phases)


def run(ready_path, executable, output, physical_parent):
    session = ConsumerSession(ready_path, output, physical_parent)
    with session:
        require((session.ready["vendorId"], session.ready["productId"]) == (0x045e, 0x028e)
                and session.ready["hidrawPath"] is None, "Only the attributed Xbox evdev consumer is allowed")
        program = session.stage(executable)
        record, data = session.command("xbox-ff", [program, session.ready["ownedGuestToken"],
            session.ready["usbParent"], session.ready["evdevPath"]], required=False)
        record["evidence"] = json.loads(data)
        session.save()
        deadline = time.monotonic() + 2
        while True:
            try:
                record["delivery"] = verify_delivery(record, read_rows(Path(session.ready["uhidEventsPath"])))
                break
            except RuntimeError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.02)
        session.result["cases"].append(dict(name=CASE, passed=True, delivery=record["delivery"]))
        session.result["result"] = "passed"
        session.save()
    require(session.result.get("accountProcessesAndFilesRemoved") is True and not session.result.get("cleanupErrors"),
            "Xbox FF consumer processes, account or permissions were not restored")
    return session.result


def verify_result(result, token, ready_path=None):
    from qemu_app import verify_app_result
    verify_app_result(result, token)
    extension = result.get("xboxForceFeedback", {})
    require(extension.get("requested") is True and extension.get("result") == "passed",
            "Xbox FF extension was not completed")
    consumer = extension.get("consumer", {})
    ready = consumer.get("ready", {})
    require(ready.get("ownedGuestToken") == token and (ready.get("vendorId"), ready.get("productId")) == (0x045e, 0x028e)
            and "hidrawPath" in ready and ready["hidrawPath"] is None,
            "Xbox FF evidence belongs to a different guest or persona")
    digest = extension.get("readySha256", "")
    require(type(digest) is str and len(digest) == 64 and all(ch in "0123456789abcdef" for ch in digest),
            "Xbox FF readiness digest is missing")
    if ready_path is not None:
        raw = Path(ready_path).read_bytes()
        require(hashlib.sha256(raw).hexdigest() == digest and json.loads(raw) == ready,
                "Xbox FF consumer readiness differs from the attributed session")
    require(consumer.get("result") == "passed" and consumer.get("accountProcessesAndFilesRemoved") is True
            and not consumer.get("cleanupErrors"), "Xbox FF consumer cleanup is unconfirmed")
    cases = consumer.get("cases", [])
    require(len(cases) == 1 and cases[0].get("name") == CASE and cases[0].get("passed") is True,
            "Xbox FF extension lacks its exact delivery case")
    require(cases[0].get("delivery", {}).get("outputDeliveryVerified") is True
            and cases[0]["delivery"].get("stopReportForwarded") is True
            and cases[0]["delivery"].get("physicalActuationVerified") is False,
            "Xbox FF delivery scope is incomplete or claims physical response")
