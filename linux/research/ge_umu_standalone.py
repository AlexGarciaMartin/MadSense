"""Separate GE-only observation after a complete, already disposed core actor."""
import hashlib
import json
from pathlib import Path

from consumer_session import bounded_bytes, require
from ge_umu_consumer import _run_ge_suite


CORE_CASES = frozenset((
    "preexisting_output_capable_evdev_writer_refused",
    "preexisting_readonly_force_feedback_descriptor_refused",
    "readonly_sensor_observers_allow_acquire_and_all_grabs_are_stored",
    "production_hid_enumeration_features_parser_attach",
    "kernel_virtual_descriptor_features_neutral_input",
    "kernel_rgb_output_to_production_bluetooth_writer",
    "full_bluetooth_input_through_parser_and_virtual_usb",
    "alsa_four_channel_ingress_and_nonzero_bluetooth_haptics",
    "production_core_dispose_detach_reset_and_physical_restore",
))
BROKER_CASES = frozenset((
    "root_application_is_rejected", "polkit_denies_unapproved_user",
    "foreign_hidraw_reader_refused_and_acl_rolled_back",
    "grab_fdstore_acl_and_input_rpc_then_normal_release", "owner_socket_eof_cleanup",
    "owner_process_exit_cleanup", "permission_tampering_ends_lease", "named_acl_tampering_ends_lease",
    "sigkill_fdstore_import_shutdown_grab_release_and_other_device_preserved",
    "disconnect_does_not_reacquire_replacement",
))
SCOPE = "standalone-ge-proton-umu-consumer"


def read(path):
    return json.loads(bounded_bytes(Path(path)))


def prior_gates(directory, token, current_actor_pid=None):
    directory = Path(directory)
    paths = {"broker": directory / "result.json", "core": directory / "core-experiment/result.json",
             "stopped": directory / "core-experiment/core/stopped.json",
             "ready": directory / "core-experiment/core/ready.json"}
    values = {key: read(path) for key, path in paths.items()}
    for key, expected in (("broker", BROKER_CASES), ("core", CORE_CASES)):
        row = values[key]; cases = row.get("cases", [])
        names = [case.get("name") for case in cases]
        require(row.get("result") == "passed" and not row.get("cleanupError")
                and row.get("environment") == dict(kind="owned-qemu-guest", instance=token)
                and row.get("physicalDevices") is False and row.get("radioInitialized") is False
                and len(names) == len(set(names)) and set(names) == expected
                and all(case.get("passed") is True for case in cases), "GE-only requires complete fresh broker/core gates")
    require(values["core"].get("applicationUiExercised") is False
            and type(values["broker"].get("retainedGrabSamples")) is int
            and values["broker"]["retainedGrabSamples"] > 0, "Prior functional scope is incomplete")
    stopped, ready = values["stopped"], values["ready"]
    require(stopped.get("completed") is True and stopped.get("cleanupConfirmed") is True
            and stopped.get("evidenceOverflow") is False and stopped.get("metrics", {}).get("inputOverflows") == 0
            and ready.get("ownedGuestToken") == token and type(ready.get("pid")) is int,
            "Prior core actor was not fully disposed")
    if current_actor_pid is not None:
        require(type(current_actor_pid) is int and current_actor_pid > 0 and current_actor_pid != ready["pid"],
                "GE-only must use a fresh second core actor")
    return dict(ownedGuestToken=token, priorActorPid=ready["pid"], coreCases=sorted(CORE_CASES),
                brokerCases=sorted(BROKER_CASES),
                hashes={key:hashlib.sha256(path.read_bytes()).hexdigest() for key,path in paths.items()})


def verify_result(result, ready, gates):
    require(result.get("scope") == SCOPE and result.get("geOnlyExperiment") is True
            and result.get("distributionWineExecuted") is False
            and result.get("priorDistributionResultsReused") is False
            and result.get("replacesDistributionWineResult") is False
            and result.get("officialValveProton") is False and result.get("runtimeCacheCollected") is False,
            "GE-only must remain separate from distribution Wine and official Proton")
    require(result.get("priorFunctionalGates") == gates and result.get("ready") == ready,
            "GE-only result has a different actor, fixture or prerequisite evidence")
    require(result.get("result") == "passed" and result.get("accountProcessesAndFilesRemoved") is True
            and not result.get("cleanupErrors") and result.get("cleanup")
            and all(row.get("aclRestored") is True for row in result["cleanup"]),
            "GE-only consumer/account/ACL cleanup did not complete")
    session = result.get("geUmuSession", {})
    expected = {"ge-umu-sony-south", "ge-umu-sony-neutral", "ge-umu-wasapi"}
    cases = session.get("cases", [])
    require(session.get("consumerAssertionsPassed") is True and session.get("runtimeExecutionConfirmed") is True
            and session.get("freshPrefix") is True and session.get("observedWineBuildId")
            and len(cases) == len(expected) and {row.get("name") for row in cases} == expected
            and all(row.get("passed") is True for row in cases), "GE-only lacks real runtime and unchanged PE assertions")
    return result


def run(ready_path, executable, fixture_state_callback, evidence, physical_parent, cache,
        functional_results, *, hidapi_executable=None):
    ready = read(ready_path)
    require(executable is not None and cache is not None and not Path(evidence).exists(),
            "Explicit GE payload and a fresh evidence directory required")
    gates = prior_gates(functional_results, ready["ownedGuestToken"], ready["actorPid"])
    error = None
    try:
        _run_ge_suite(ready_path, executable, fixture_state_callback, evidence, physical_parent, cache,
                      hidapi_executable=hidapi_executable)
    except Exception as failure:
        error = failure
    path = Path(evidence) / "result.json"
    if not path.is_file():
        if error is not None:
            raise error
        raise RuntimeError("GE-only returned without a result")
    result = read(path)
    result.update(scope=SCOPE, geOnlyExperiment=True, distributionWineExecuted=False,
                  priorDistributionResultsReused=False, priorFunctionalGates=gates)
    if error is not None:
        result["geOnlyExecutionError"] = f"{type(error).__name__}: {error}"
    path.write_text(json.dumps(result, indent=2) + "\n")
    if error is not None:
        raise error
    return verify_result(result, ready, gates)
