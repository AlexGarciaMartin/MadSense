"""Independent real-HIDAPI observations in the current fresh Wine/GE session."""
import hashlib
from pathlib import Path
import time

from consumer_session import read_rows, require
from hidapi_windows_evidence import verify
from run_wine_consumer import clear_sony_state, parse_pe_output


def run_cases(session, executable, fixture_state_callback, *, invoke=None, label_prefix="hidapi-windows"):
    require(session.ready["vendorId"] == 0x054c and session.ready["productId"] in (0x0ce6, 0x0df2),
            "Real HIDAPI observer requires the owned Sony persona")
    require(session.ready.get("rawOutputObservationEnabled") is True, "Raw USB observer must be enabled")
    program = session.stage(executable)
    result = dict(scope="real-hidapi-windows-library", cases=[],
        observerSha256=hashlib.sha256(program.read_bytes()).hexdigest(),
        win32StrictCountGateUnchanged=True, physicalActuationVerified=False,
        nativeWindowsParityVerified=False, finalCoreCaptureDrainStillRequired=True)
    session.result["hidapiWindows"] = result; session.save()
    def distribution(label, arguments):
        return session.command(label, ["wine", "Z:" + str(program).replace("/", "\\"), *arguments], required=False)
    execute = distribution if invoke is None else lambda label, arguments: invoke(label, program, arguments)
    failures = []
    for state in ("south", "neutral"):
        label = label_prefix + "-" + state
        try:
            fixture_state_callback(state); time.sleep(.1)
            clear_sony_state(session, label + "-clear")
            persona = "edge" if session.ready["productId"] == 0x0df2 else "dualsense"
            row, output = execute(label, [session.ready["ownedGuestToken"], persona, session.ready["virtualMacHex"], state])
            row.update(evidence=parse_pe_output(output), fixtureState=state)
            # Deferred capture is outside the device reader. Waiting here only
            # lets it flush events whose timestamps are inside this PE interval.
            deadline = time.monotonic() + 2
            while True:
                try:
                    evidence = verify(session.ready, row,
                        read_rows(Path(session.ready["coreOutputDirectory"]) / "events.jsonl"),
                        read_rows(Path(session.ready["uhidEventsPath"])))
                    break
                except RuntimeError:
                    if time.monotonic() >= deadline: raise
                    time.sleep(.05)
            row["independentForwardingEvidence"] = evidence
            result["cases"].append(dict(name=label, passed=True, evidence=evidence))
        except Exception as error:
            session.check_device()
            failures.append(f"{label}: {error}")
            result["cases"].append(dict(name=label, passed=False, error=str(error)))
        session.save()
    fixture_state_callback("neutral")
    result["result"] = "failed" if failures else "passed"
    session.save()
    require(not failures, "; ".join(failures))
    return result


def after_api(primary, session, executable, fixture_state_callback, *, invoke=None, label_prefix="hidapi-windows"):
    """Observe library functionality even after strict API failure; retain it."""
    if executable is None:
        return primary()
    original = None
    try:
        primary()
    except Exception as error:
        original = error
    observed_error = None
    try:
        run_cases(session, executable, fixture_state_callback, invoke=invoke, label_prefix=label_prefix)
    except Exception as error:
        observed_error = error
    finally:
        try:
            fixture_state_callback("neutral")
        except Exception as error:
            # alive()/lease checks in the real callback may fail. Keep that
            # secondary evidence without masking the original strict API error.
            session.result["hidapiWindowsResetError"] = f"{type(error).__name__}: {error}"
            if observed_error is None:
                observed_error = error
            try:
                session.save()
            except Exception as save_error:
                session.result["hidapiWindowsResetSaveError"] = str(save_error)
    if original is not None:
        raise original
    if observed_error is not None:
        raise observed_error


def final_capture_summary(result, stopped, token):
    """Host-side promotion only after final raw capture and account cleanup."""
    observer = result.get("hidapiWindows")
    summary = dict(requested=True, result="not-executed" if observer is None else observer.get("result", "failed"),
        rawCaptureFinalized=False, reportForwardingVerified=False, nativeWindowsParityVerified=False,
        physicalActuationVerified=False, win32StrictCountGateUnchanged=True)
    if observer is None:
        return summary
    try:
        require(result.get("ready", {}).get("ownedGuestToken") == token, "HIDAPI result guest mismatch")
        require(result.get("accountProcessesAndFilesRemoved") is True and not result.get("cleanupErrors")
                and not result.get("hidapiWindowsResetError")
                and result.get("cleanup") and all(x.get("aclRestored") is True for x in result["cleanup"]),
                "HIDAPI session cleanup incomplete")
        require(stopped.get("completed") is True and stopped.get("cleanupConfirmed") is True
                and stopped.get("evidenceOverflow") is False, "Core did not finalize cleanly")
        raw = stopped.get("rawOutputObservation", {})
        require(all(type(raw.get(key)) is int and raw[key] == 0 for key in ("rejectedSnapshots", "unwrittenSnapshots"))
                and raw.get("error", "missing") is None, "Raw USB capture has missing/lost evidence")
        summary["rawCaptureFinalized"] = True
        cases = observer.get("cases", [])
        require(observer.get("result") == "passed" and len(cases) == 2
                and all(case.get("passed") is True and case.get("evidence", {}).get("outputReportForwarded") is True
                        for case in cases), "HIDAPI feature/input/output cases did not pass")
        summary.update(reportForwardingVerified=True, cases=cases)
    except Exception as error:
        summary["error"] = str(error)
    return summary
