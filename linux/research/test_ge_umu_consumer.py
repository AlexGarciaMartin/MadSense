import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ge_umu_consumer import prerequisites, run_after_experiments
from run_wine_consumer import run_windows_cases
from consumer_session import run_suite


class GePrerequisiteTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.ready = dict(ownedGuestToken="owned", usbParent="/sys/devices/platform/vhci_hcd.0/usb1/1-3",
                          virtualMacHex="02d500000003")
        self.ready_path = self.root / "ready.json"; self.ready_path.write_text(json.dumps(self.ready))
        self.evidence = self.root / "consumer-suite"; self.evidence.mkdir()
        self.variant = self.root / "wine-udev-variant"; self.variant.mkdir()
        self.variant_result = dict(scope="configured-distribution-wine-udev", result="failed", ready=self.ready,
            defaultWinebus=False, accountProcessesAndFilesRemoved=True)
        self.baseline = dict(result="failed", ready=self.ready, accountProcessesAndFilesRemoved=True,
                             wineSession=dict(kind="distribution-wine", defaultWinebus=True))
        self.write_prerequisites()

    def write_prerequisites(self):
        path = self.variant / "result.json"; path.write_text(json.dumps(self.variant_result))
        self.baseline["wineUdevVariant"] = dict(result=self.variant_result["result"], cleanupVerified=True,
                                                resultSha256=hashlib.sha256(path.read_bytes()).hexdigest())
        (self.evidence / "result.json").write_text(json.dumps(self.baseline))

    def call(self, primary=lambda: None):
        return run_after_experiments(primary, self.ready_path, "observer.exe", lambda *_: None,
                                     self.evidence, "physical", "explicit-private-cache")

    def fake_ge(self, _ready, _pe, _callback, destination, _physical, _cache, **changes):
        destination.mkdir()
        result = dict(scope="configured-ge-proton-umu-consumer", result="passed", ready=self.ready,
            accountProcessesAndFilesRemoved=True, officialValveProton=False, runtimeCacheCollected=False,
            geUmuSession=dict(consumerAssertionsPassed=True)) | changes
        (destination / "result.json").write_text(json.dumps(result))

    def test_failed_default_and_udev_may_be_measured_but_never_replaced_by_ge_success(self):
        original = RuntimeError("default failure retained")
        def primary(): raise original
        with patch("ge_umu_consumer._run_ge_suite", side_effect=self.fake_ge):
            with self.assertRaises(RuntimeError) as caught: self.call(primary)
        self.assertIs(caught.exception, original)
        result = json.loads((self.evidence / "result.json").read_text())
        self.assertEqual(result["result"], "failed")
        self.assertFalse(result["geUmuExperiment"]["replacesDistributionWineResult"])
        self.assertFalse(result["geUmuExperiment"]["officialValveProton"])

    def test_wrong_guest_or_cleanup_failure_prevents_all_ge_execution(self):
        original = dict(self.variant_result)
        for changes in (dict(accountProcessesAndFilesRemoved=False), dict(cleanupErrors=["lost ACL"]),
                        dict(ready=self.ready | dict(ownedGuestToken="other"))):
            self.variant_result = original | changes; self.write_prerequisites()
            with patch("ge_umu_consumer._run_ge_suite") as run, self.assertRaises(RuntimeError): self.call()
            run.assert_not_called()

    def test_hash_changed_or_missing_udev_result_cannot_be_substituted(self):
        path = self.variant / "result.json"
        path.write_text(path.read_text() + " ")
        with self.assertRaises(RuntimeError): prerequisites(self.evidence / "result.json", self.ready)
        path.unlink()
        with patch("ge_umu_consumer._run_ge_suite") as run, self.assertRaises(FileNotFoundError): self.call()
        run.assert_not_called()

    def test_ge_cleanup_failure_or_stale_evidence_is_not_completed(self):
        def failed(*args): self.fake_ge(*args, accountProcessesAndFilesRemoved=False)
        with patch("ge_umu_consumer._run_ge_suite", side_effect=failed), self.assertRaises(RuntimeError): self.call()
        with patch("ge_umu_consumer._run_ge_suite") as run, self.assertRaises(RuntimeError): self.call()
        run.assert_not_called()

    def test_same_shared_assertions_reject_changed_feature_bytes_with_zero_exit(self):
        session = SimpleNamespace(ready=dict(ownedGuestToken="owned", vendorId=0x054c, productId=0x0ce6,
            virtualMacHex="02d500000003", features={"09": "0900", "05": "0500", "20": "2000"}),
            result=dict(geUmuSession=dict(cases=[])), save=lambda: None, check_device=lambda: None)
        rows = [dict(kind="hid-candidate", macMatched=True, containerKnown=True,
                     containerId="00112233-4455-6677-8899-aabbccddeeff"),
                dict(kind="hid-feature", reportId=9, hex="09ff"), dict(kind="result", result="passed")]
        def invoke(*_args, **_kwargs): return dict(exitCode=0), b"\n".join(json.dumps(row).encode() for row in rows)
        with patch("run_wine_consumer.clear_sony_state"), patch("run_wine_consumer.time.sleep"), \
             patch("run_wine_consumer.shutil.which", return_value="stub"), \
             patch("run_wine_consumer.start_pulse", side_effect=RuntimeError("No audio fixture")), \
             self.assertRaises(RuntimeError):
            run_windows_cases(session, invoke, lambda *_: None, result_key="geUmuSession", label_prefix="ge-umu")
        cases = session.result["geUmuSession"]["cases"]
        self.assertEqual(len(cases), 3)
        self.assertTrue(all(case["passed"] is False for case in cases))
        self.assertIn("feature bytes differ", cases[0]["error"])

    def test_explicit_ge_preparation_must_observe_each_state_before_pe_launch(self):
        from unittest.mock import Mock
        session = SimpleNamespace(ready=dict(ownedGuestToken="owned", vendorId=0x054c),
            result=dict(geUmuSession=dict(cases=[])), save=lambda: None, check_device=lambda: None)
        prepare = Mock(side_effect=RuntimeError("Priming marker not observed"));invoke=Mock()
        with patch("run_wine_consumer.time.sleep"), patch("run_wine_consumer.clear_sony_state") as default_clear, \
             self.assertRaisesRegex(RuntimeError,"Priming marker not observed"):
            run_windows_cases(session, invoke, lambda _:None, result_key="geUmuSession", label_prefix="ge-umu",
                              prepare_sony_state=prepare)
        self.assertEqual([call.args[1] for call in prepare.call_args_list], ["ge-umu-sony-south","ge-umu-sony-neutral"])
        default_clear.assert_not_called();invoke.assert_not_called()

    def test_default_suite_does_not_invoke_ge_and_opt_in_requires_udev(self):
        with patch("consumer_session._run_primary_suite", return_value="baseline") as primary, \
             patch("ge_umu_consumer.run_after_experiments") as ge:
            self.assertEqual(run_suite("ready", "bins", "pe", None, "output", "physical"), "baseline")
            primary.assert_called_once(); ge.assert_not_called()
        with patch("consumer_session._run_primary_suite") as primary, \
             patch("ge_umu_consumer.run_after_experiments") as ge, self.assertRaises(RuntimeError):
            run_suite("ready", "bins", "pe", None, "output", "physical", ge_umu_cache="cache")
        primary.assert_not_called(); ge.assert_not_called()

    def test_preconsumer_cleanup_failure_survives_both_wrappers_without_fabricated_result(self):
        (self.evidence / "result.json").unlink()
        # Fresh configured-prefix location, as in an actual early observer failure.
        (self.variant / "result.json").unlink(); self.variant.rmdir()
        original = RuntimeError("Gain state restoration was not verified")
        with patch("consumer_session._run_primary_suite", side_effect=original), \
             patch("run_wine_variant.run_variant") as variant, \
             patch("ge_umu_consumer._run_ge_suite") as ge, self.assertRaises(RuntimeError) as caught:
            run_suite(self.ready_path, "bins", "pe", None, self.evidence, "physical",
                      wine_udev_variant=True, ge_umu_cache="private-cache")
        self.assertIs(caught.exception, original)
        variant.assert_not_called(); ge.assert_not_called()
        self.assertFalse((self.evidence / "result.json").exists())
        self.assertFalse((self.root / "ge-umu-suite").exists())


if __name__ == "__main__":
    unittest.main()
