import copy
import csv
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from analyze import FIELDS, summarize
from gates import REMOVAL_CASES, verify_upgrade
from qemu_latency import add_latency_payload, prepare_combined_payload, run_latency_stage, verify_measurement


class GateTests(unittest.TestCase):
    def test_partial_or_failed_functional_stage_cannot_start_measurement(self):
        for states in ({}, dict(baselineExit=0, coreExit=1), dict(baselineExit=0, coreExit=0),
                       dict(baselineExit=0, coreExit=0, appExit=1)):
            guest = SimpleNamespace(provenance=states, run=Mock(), token="guest")
            with self.subTest(states=states), self.assertRaises(RuntimeError):
                run_latency_stage(guest)
            guest.run.assert_not_called()

    def test_installer_success_cannot_hide_missing_removal_cases(self):
        valid = dict(result="passed", installations=[{}, {}], removal=dict(result="passed", cases=REMOVAL_CASES.copy()))
        verify_upgrade(valid)
        valid["removal"]["cases"].pop()
        with self.assertRaises(RuntimeError):
            verify_upgrade(valid)

    def test_payload_contains_importable_guest_entrypoints_and_exact_publish_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            publishes = []
            for label, executable in (("neutral", "MadSense.Linux.Probe"), ("core", "MadSense.Linux.CoreProbe"),
                                      ("app", "MadSense.Linux.AppProbe")):
                directory = root / label; directory.mkdir()
                (directory / executable).write_bytes((label + "-published-binary").encode())
                publishes.append(directory)
            payload = root / "payload"
            prepare_combined_payload(payload, *publishes)
            hashes = add_latency_payload(payload)
            expected = ["linux/tests/latency/run_latency_guest.py", "linux/tests/latency/run_latency_experiment.sh",
                        "linux/tests/latency/gates.py", "linux/tests/latency/latency_probe.c",
                        "linux/tests/audio-hotplug/actor_files.py",
                        "linux/tests/latency/capture_diagnostics.py",
                        "linux/platform/integration/actor_staging.py", "linux/research/qemu_kernel.py",
                        "linux/research/kvm_acceleration.py", "app-probe/MadSense.Linux.AppProbe"]
            self.assertTrue(set(expected).issubset(hashes))
            for relative, digest in hashes.items():
                self.assertEqual(hashlib.sha256((payload / relative).read_bytes()).hexdigest(), digest)
            self.assertEqual((payload / "app-probe/MadSense.Linux.AppProbe").read_bytes(), b"app-published-binary")
            # Isolated import resolves entirely from the staged tree, not this
            # checkout or an ambient PYTHONPATH. Runtime guard is not invoked.
            code = ("import pathlib,sys; root=pathlib.Path(sys.argv[1]); "
                    "sys.path.insert(0,str(root/'linux/tests/latency')); import gates,qemu_latency; "
                    "import qemu_core,actor_staging; "
                    "assert pathlib.Path(qemu_core.__file__).is_relative_to(root); "
                    "assert pathlib.Path(actor_staging.__file__).is_relative_to(root); "
                    "sys.path.insert(0,str(root/'linux/tests/audio-hotplug')); import actor_files; "
                    "assert pathlib.Path(actor_files.__file__).is_relative_to(root)")
            if sys.platform == "linux":
                code += "; import run_latency_guest"
            subprocess.run([sys.executable, "-I", "-c", code, str(payload)], cwd=root, check=True, timeout=15)


class CollectedEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.gates = {"baseline": {"sha256": "actual-gate-digest"}}
        cases = []
        for rate in (100, 1000):
            directory = self.root / f"{rate}hz"
            (directory / "app").mkdir(parents=True)
            rows = [dict(zip(FIELDS, [i, 1000000000 + i * (1000000000 // rate), 0, 0, 0, 0, 0, 1]))
                    for i in range(rate * 10)]
            rows[0].update(inject_before_ns=1000000010, inject_after_ns=1000000020, observed_ns=1000000030, deadline_skipped=0)
            with (directory / "samples.csv").open("w", newline="") as stream:
                writer = csv.DictWriter(stream, FIELDS); writer.writeheader(); writer.writerows(rows)
            raw = dict(schema=1, samplesSaved=True, rate=rate, nominalSamples=len(rows), attempted=1, written=1,
                       deadlineSkipped=len(rows)-1, matched=1, fatalErrno=0, malformedMatches=0, inputReads=1, unmatched=0, reordered=0)
            self.save(directory / "probe.json", raw)
            with (directory / "samples.csv").open(newline="") as stream:
                case = summarize(stream, raw)
            case.update(cleanupConfirmed=True, metricsAfter={"metrics": {"InputOverflows": 0}})
            self.save(directory / "result.json", case)
            self.save(directory / "app/stopped.json", dict(cleanupConfirmed=True))
            self.save(directory / "fixture-before.json", dict(nodes=[dict(path="/dev/hidraw-fixture", mode=432)]))
            self.save(directory / "fixture-restored.json", [dict(path="/dev/hidraw-fixture", mode=432)])
            cases.append(case)
        self.value = dict(schema=1, result="generator-limit", environment=dict(kind="owned-qemu-guest", instance="guest"),
                          functionalGate=False, physicalDevices=False, radioInitialized=False, physicalLatencyValidated=False,
                          applicationControllerExercised=True, applicationUiExercised=False, functionalGates=self.gates, cases=cases)
        self.save(self.root / "result.json", self.value)

    @staticmethod
    def save(path, value):
        path.write_text(json.dumps(value))

    def test_generator_limit_remains_valid_evidence_with_confirmed_cleanup(self):
        observed = verify_measurement(self.root, "guest", self.gates)
        self.assertEqual(observed["result"], "generator-limit")

    def test_summary_tampering_cannot_hide_missing_samples(self):
        changed = copy.deepcopy(self.value)
        changed["cases"][0]["observed"] = 1000
        self.save(self.root / "result.json", changed)
        self.save(self.root / "100hz/result.json", changed["cases"][0])
        with self.assertRaises(RuntimeError):
            verify_measurement(self.root, "guest", self.gates)

    def test_top_level_cleanup_claim_requires_fresh_helper_evidence(self):
        self.save(self.root / "100hz/app/stopped.json", dict(cleanupConfirmed=False))
        with self.assertRaises(RuntimeError):
            verify_measurement(self.root, "guest", self.gates)

    def test_changed_restoration_or_wrong_guest_is_rejected(self):
        with self.assertRaises(RuntimeError):
            verify_measurement(self.root, "another-guest", self.gates)
        self.save(self.root / "100hz/fixture-restored.json", [dict(path="/dev/hidraw-fixture", mode=438)])
        with self.assertRaises(RuntimeError):
            verify_measurement(self.root, "guest", self.gates)


if __name__ == "__main__":
    unittest.main()
