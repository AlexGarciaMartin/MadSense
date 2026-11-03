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
    def test_helper_variant_requires_input_actor_and_selects_only_the_owned_wrapper(self):
        state = dict(baselineExit=0, coreExit=0, appExit=0, helperObservationRequested=True)
        guest = SimpleNamespace(provenance=state, run=Mock(return_value=SimpleNamespace(returncode=0)), token="guest")
        with self.assertRaises(RuntimeError):
            run_latency_stage(guest)
        guest.run.assert_not_called()
        state.update(traceExperimentRequested=True, traceSourceOverlay={"traceKind": "input"})
        self.assertEqual(run_latency_stage(guest), 0)
        self.assertTrue(guest.run.call_args.args[1].endswith("guest --helper"))
        self.assertEqual(guest.run.call_args.kwargs["timeout"], 900)

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
                        "linux/tests/latency/input_trace_analysis.py", "linux/tests/latency/trace_variant.py",
                        "linux/tests/latency/helper_trace.py", "linux/tests/latency/helper_override.py",
                        "linux/tests/latency/helper_trace_analysis.py", "linux/tests/latency/run_helper_latency_guest.py",
                        "linux/platform/integration/actor_staging.py", "linux/research/qemu_kernel.py",
                        "linux/research/kvm_acceleration.py", "app-probe/MadSense.Linux.AppProbe"]
            self.assertTrue(set(expected).issubset(hashes))
            for relative, digest in hashes.items():
                self.assertEqual(hashlib.sha256((payload / relative).read_bytes()).hexdigest(), digest)
            self.assertEqual((payload / "app-probe/MadSense.Linux.AppProbe").read_bytes(), b"app-published-binary")
            # Isolated import resolves entirely from the staged tree, not this
            # checkout or an ambient PYTHONPATH. Runtime guard is not invoked.
            code = ("import pathlib,sys; root=pathlib.Path(sys.argv[1]); "
                    "sys.path.insert(0,str(root/'linux/tests/latency')); import gates,qemu_latency,input_trace_analysis,trace_variant; "
                    "assert pathlib.Path(input_trace_analysis.__file__).is_relative_to(root); "
                    "assert pathlib.Path(trace_variant.__file__).is_relative_to(root); "
                    "import run_helper_latency_guest; assert pathlib.Path(run_helper_latency_guest.__file__).is_relative_to(root); "
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


class PairedInputEvidenceTests(unittest.TestCase):
    def setUp(self):
        from test_input_trace import InputTailTests, FIELDS as INPUT_FIELDS
        from test_trace import stream
        from input_trace_analysis import analyze
        from trace_schedule import schedule
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.gates = {"baseline": {"sha256": "actual-gate-digest"}}
        self.value = dict(schema=1, result="generator-limit", environment=dict(kind="owned-qemu-guest", instance="guest"),
            functionalGate=False, physicalDevices=False, radioInitialized=False, physicalLatencyValidated=False,
            applicationControllerExercised=True, applicationUiExercised=False, functionalGates=self.gates, cases=[],
            attributionExperiment=dict(kind="paired-control-and-instrumented-copy", productionTuning=False,
                cases=[name for name, _, _ in schedule("input")],
                sourceOverlay=dict(schema=1, productionBinary=False, kind="instrumented-research-copy", traceKind="input")))
        for name, rate, instrumented in schedule("input"):
            fixture = InputTailTests()
            fixture.fixture(6, rate)
            directory = self.root / name
            (directory / "app").mkdir(parents=True)
            samples = stream(FIELDS, fixture.samples).read()
            (directory / "samples.csv").write_text(samples)
            self.save(directory / "probe.json", fixture.probe)
            with (directory / "samples.csv").open() as source:
                case = summarize(source, fixture.probe)
            case.update(caseName=name, instrumented=instrumented, cleanupConfirmed=True,
                        metricsAfter={"metrics": {"InputOverflows": 0}})
            if instrumented:
                (directory / "app/input-trace.csv").write_text(stream(INPUT_FIELDS, fixture.rows).read())
                self.save(directory / "app/input-trace.json", fixture.meta)
                case["attribution"] = analyze(stream(INPUT_FIELDS, fixture.rows), fixture.meta,
                                              stream(FIELDS, fixture.samples), fixture.probe)
            self.save(directory / "result.json", case)
            self.save(directory / "app/stopped.json", dict(cleanupConfirmed=True))
            self.save(directory / "fixture-before.json", dict(nodes=[dict(path="fixture", mode=432)]))
            self.save(directory / "fixture-restored.json", [dict(path="fixture", mode=432)])
            self.value["cases"].append(case)
        self.save(self.root / "result.json", self.value)

    @staticmethod
    def save(path, value):
        path.write_text(json.dumps(value))

    def test_host_recomputes_the_four_case_input_schedule_from_raw_tail(self):
        value = verify_measurement(self.root, "guest", self.gates)
        self.assertEqual(len(value["cases"]), 4)

    def test_top_level_and_case_agreement_cannot_hide_tampered_attribution(self):
        self.value["cases"][1]["attribution"]["retainedEvents"] += 1
        self.save(self.root / "result.json", self.value)
        self.save(self.root / "1000hz-input-1/result.json", self.value["cases"][1])
        with self.assertRaises(RuntimeError):
            verify_measurement(self.root, "guest", self.gates)

    def test_mixed_instrumentation_is_not_an_input_only_measurement(self):
        (self.root / "1000hz-input-1/app/report-trace.json").write_text("{}")
        with self.assertRaises(ValueError):
            verify_measurement(self.root, "guest", self.gates)

if __name__ == "__main__":
    unittest.main()
