"""Optional GE/UMU consumer suite after isolated default and configured Wine.

Only the explicit workflow-dispatch GE input enables this module. Archives stay in a private
cache and ConsumerSession staging, never in the returned evidence directory.
"""
import hashlib
import json
from pathlib import Path
import time

from consumer_session import ConsumerSession, bounded_bytes, require
from ge_umu_session import prepare, run_consumer
from run_wine_consumer import parse_pe_output, run_windows_cases, start_display
from run_wine_variant import clean_result


def prerequisites(primary_path, ready):
    baseline = clean_result(primary_path, ready)
    require(baseline.get("wineSession", {}).get("kind") == "distribution-wine"
            and baseline["wineSession"].get("defaultWinebus") is True,
            "Record the actual default Wine session before GE/UMU")
    variant_path = Path(primary_path).parent.parent / "wine-udev-variant/result.json"
    variant = clean_result(variant_path, ready)
    summary = baseline.get("wineUdevVariant", {})
    require(variant.get("scope") == "configured-distribution-wine-udev"
            and variant.get("defaultWinebus") is False and summary.get("cleanupVerified") is True
            and summary.get("result") == variant.get("result")
            and summary.get("resultSha256") == hashlib.sha256(variant_path.read_bytes()).hexdigest(),
            "The completed UDEV diagnostic and its exact cleanup evidence are required before GE/UMU")
    return baseline


def _run_ge_suite(ready_path, executable, fixture_state_callback, evidence, physical_parent, cache, *, hidapi_executable=None):
    evidence, cache = Path(evidence).resolve(), Path(cache).resolve(strict=True)
    require(not cache.is_relative_to(evidence) and not evidence.is_relative_to(cache),
            "GE cache and collected evidence must be separate")
    with ConsumerSession(ready_path, evidence, physical_parent) as session:
        session.result.update(scope="configured-ge-proton-umu-consumer", replacesDistributionWineResult=False,
                              officialValveProton=False, runtimeCacheCollected=False)
        try:
            # Prepare before starting Xvfb or any runtime payload. This retains
            # the ownership-transfer boundary enforced by ge_umu_session.
            preparation_started = time.monotonic_ns()
            try:
                prepared = prepare(session, cache)
            finally:
                session.result["gePreparationTiming"] = dict(startedMonotonicNs=preparation_started,
                    endedMonotonicNs=time.monotonic_ns(), actorBudgetSeconds=600,
                    includesDownloads=False, completionDoesNotProveCompatibility=True)
                session.save()
            program = session.stage(executable)
            session.env.update(prepared["environment"], WINEARCH="win64", WINEDLLOVERRIDES="mscoree,mshtml=")
            start_display(session)
            session.result["geUmuSession"].update(cases=[], freshPrefix=True,
                disabledOptionalManagedAndHtmlInstallers=True, officialValveProton=False)
            session.save()

            def invoke(label, arguments, *, required=False):
                row, output = run_consumer(session, prepared, program, arguments, label=label, timeout=120)
                # The strict parser is shared with distribution Wine. Launcher
                # chatter/missing PE output cannot silently become success.
                parsed = parse_pe_output(output)
                runtime = [value for value in parsed if value.get("kind") == "runtime"]
                require(len(runtime) == 1 and isinstance(runtime[0].get("wineVersion"), str)
                        and runtime[0]["wineVersion"] and runtime[0].get("protonValidated") is False
                        and isinstance(runtime[0].get("wineBuildId"), str) and runtime[0]["wineBuildId"],
                        "GE/UMU did not return the native observer's Wine runtime identity")
                session.result["geUmuSession"].update(runtimeExecutionConfirmed=True,
                    observedWineVersion=runtime[0]["wineVersion"], observedWineBuildId=runtime[0]["wineBuildId"],
                    runtimeBuildIdIsSourceAttestation=False, officialValveProton=False)
                row["evidence"] = parsed; session.save()
                require(not required or row["exitCode"] == 0, "GE/UMU Windows observer failed: " + label)
                return row, output

            from run_hidapi_windows import after_api
            after_api(lambda: run_windows_cases(session, invoke, fixture_state_callback,
                result_key="geUmuSession", label_prefix="ge-umu"), session, hidapi_executable, fixture_state_callback,
                invoke=lambda label, hidapi_program, arguments: run_consumer(session, prepared, hidapi_program,
                    arguments, label=label, timeout=120), label_prefix="ge-hidapi-windows")
            session.result["geUmuSession"]["consumerAssertionsPassed"] = True
            session.result["result"] = "passed"
        finally:
            fixture_state_callback("neutral")
        session.save()
    return session.result


def run_after_experiments(primary, ready_path, executable, fixture_state_callback, evidence, physical_parent, cache, *, hidapi_executable=None):
    """Call primary default+UDEV, then GE in a new account only after exact cleanup.

Earlier failures remain failures even when the optional GE experiment passes.
The caller must explicitly opt in and supply a verified private cache path.
"""
    require(executable is not None and cache is not None, "GE/UMU requires explicit PE and private cache")
    evidence = Path(evidence)
    destination = evidence.parent / "ge-umu-suite"
    require(not destination.exists(), "Fresh GE/UMU evidence directory required")
    original_error = None
    try:
        primary()
    except Exception as failure:
        original_error = failure
    # An earlier observer can refuse continuation before the default consumer
    # session exists (for example failed PipeWire restoration). Preserve that
    # real cause; absence of its result is not permission to start GE.
    if original_error is not None and not (evidence / "result.json").is_file():
        raise original_error
    ready = json.loads(bounded_bytes(Path(ready_path)))
    baseline = prerequisites(evidence / "result.json", ready)
    error = None
    try:
        optional = {"hidapi_executable": hidapi_executable} if hidapi_executable is not None else {}
        _run_ge_suite(ready_path, executable, fixture_state_callback, destination, physical_parent, cache, **optional)
    except Exception as failure:
        error = failure
    result = clean_result(destination / "result.json", ready)
    require(result.get("scope") == "configured-ge-proton-umu-consumer"
            and result.get("officialValveProton") is False and result.get("runtimeCacheCollected") is False,
            "Unexpected GE/UMU result scope")
    baseline["geUmuExperiment"] = dict(result=result.get("result"), evidenceDirectory=str(destination),
        resultSha256=hashlib.sha256((destination / "result.json").read_bytes()).hexdigest(), cleanupVerified=True,
        replacesDistributionWineResult=False, officialValveProton=False, error=str(error) if error else None)
    (evidence / "result.json").write_text(json.dumps(baseline, indent=2) + "\n")
    if original_error is not None:
        raise original_error
    if error is not None:
        raise error
    require(result.get("result") == "passed" and result.get("geUmuSession", {}).get("consumerAssertionsPassed") is True,
            "GE/UMU did not complete all independent consumer assertions")
    return baseline
