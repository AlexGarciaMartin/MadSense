"""Shared guest/host gate for the optional benchmark, using functional verifiers."""
import hashlib
import json
from pathlib import Path
import sys

LINUX = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(LINUX / "platform/tests"))
sys.path.insert(0, str(LINUX / "platform/integration"))
sys.path.insert(0, str(LINUX / "tests/app-integration"))
from qemu_broker import verify_result
from qemu_core import verify_core_result
from qemu_app import verify_app_result


REMOVAL_CASES = ["modified_file_preserved_before_service_stop", "idle_removal_preserves_unknown_file",
                 "repeated_removal_is_idempotent", "reinstall_after_removal_confirms_idle"]


def verify_upgrade(value):
    if (value.get("result") != "passed" or len(value.get("installations", [])) != 2
            or value.get("removal", {}).get("result") != "passed"
            or value.get("removal", {}).get("cases") != REMOVAL_CASES):
        raise RuntimeError("Repeated idle installation/removal gates did not pass")


def verify_paths(baseline, core, app, upgrade, token):
    records = {}
    for name, path, verifier in (("baseline", baseline, verify_result), ("core", core, verify_core_result),
                                  ("app", app, verify_app_result)):
        data = Path(path).read_bytes()
        value = json.loads(data)
        verifier(value, token)
        records[name] = dict(cases=len(value["cases"]), sha256=hashlib.sha256(data).hexdigest())
    data = Path(upgrade).read_bytes()
    verify_upgrade(json.loads(data))
    records["idleUpgradeRemoval"] = dict(installations=2, removalCases=len(REMOVAL_CASES), sha256=hashlib.sha256(data).hexdigest())
    return records


def verify_directory(directory, token):
    directory = Path(directory)
    return verify_paths(directory / "result.json", directory / "core-experiment/result.json",
                        directory / "app-experiment/result.json", directory / "idle-upgrade/result.json", token)
