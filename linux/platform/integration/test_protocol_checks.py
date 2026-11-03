import copy
import unittest

from protocol_checks import CORE_CASES, APP_CASES, verify_protocol_results
from qemu_core import REQUIRED_CORE_CASES
from qemu_app import REQUIRED_CASES


class ProtocolChecksTests(unittest.TestCase):
    def test_base_gates_cleanup_scope_and_every_extension_remain_required(self):
        core = dict(result="passed", environment=dict(kind="owned-qemu-guest", instance="owned"),
                    physicalDevices=False, radioInitialized=False, applicationUiExercised=False,
                    cases=[dict(name=name, passed=True) for name in REQUIRED_CORE_CASES],
                    protocolExtension=dict(requested=True, result="passed", cases=[dict(name=name, passed=True) for name in CORE_CASES]))
        app = copy.deepcopy(core)
        app.update(applicationControllerExercised=True, cases=[dict(name=name, passed=True) for name in REQUIRED_CASES],
                   protocolExtension=dict(requested=True, result="passed", cases=[dict(name=name, passed=True) for name in APP_CASES]))
        verify_protocol_results(core, app, "owned")
        for target in ("core", "app"):
            for mutation in ("base_missing", "extension_missing", "extension_duplicate", "not_requested", "cleanup", "physical"):
                first, second = copy.deepcopy(core), copy.deepcopy(app)
                value = first if target == "core" else second
                if mutation == "base_missing": value["cases"].pop()
                elif mutation == "extension_missing": value["protocolExtension"]["cases"].pop()
                elif mutation == "extension_duplicate": value["protocolExtension"]["cases"] *= 2
                elif mutation == "not_requested": value["protocolExtension"]["requested"] = False
                elif mutation == "cleanup": value["cleanupError"] = "pending"
                else: value["physicalDevices"] = True
                with self.subTest(target=target, mutation=mutation), self.assertRaises(RuntimeError):
                    verify_protocol_results(first, second, "owned")


if __name__ == "__main__":
    unittest.main()
