import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

from run_wine_variant import registry_sdl_disabled, run_after_default, audited_wine_version, configure_udev


class WineVariantTests(unittest.TestCase):
    def test_version_allowlist_rejects_unaudited_families_and_suffix_versions(self):
        for version in ('wine-9.0', 'wine-9.0 (Ubuntu 9.0~repack-4build3)', 'wine-10.0 (Debian 10.0~repack-6)\n'):
            self.assertIn(audited_wine_version(version), ('9.0','10.0'))
        for version in ('wine-8.0','wine-11.0','wine-10.1','wine-10.0-staging','wine-10.0\nignored'):
            with self.assertRaises(RuntimeError): audited_wine_version(version)

    def test_unsupported_version_records_request_without_configuration(self):
        session = Mock(); session.result = {'wineSession':{}}
        with self.assertRaisesRegex(RuntimeError, 'audited'):
            configure_udev(session, Path('unused'), 'wine-11.0')
        self.assertEqual(session.result['wineSession'], {'configurationRequested':True,'configured':False})
        session.command.assert_not_called()

    def test_registry_readback_requires_exact_one_dword_zero(self):
        registry_sdl_disabled(b"HKEY_LOCAL_MACHINE\\System\\CurrentControlSet\\Services\\winebus\r\n    Enable SDL    REG_DWORD    0x0\r\n")
        for value in (b"", b"Enable SDL REG_SZ 0x0", b"Enable SDL REG_DWORD 0x1",
                      b"Enable SDL REG_DWORD 0x0\nEnable SDL REG_DWORD 0x0"):
            with self.subTest(value=value), self.assertRaises(RuntimeError): registry_sdl_disabled(value)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.ready = dict(ownedGuestToken="guest", usbParent="/sys/devices/platform/vhci_hcd.0/usb1/1-2",
                          virtualMacHex="02d500000001")
        self.ready_path = self.root / "ready.json"; self.ready_path.write_text(json.dumps(self.ready))
        self.evidence = self.root / "consumer-suite"; self.evidence.mkdir()
        self.baseline = dict(result="failed", ready=self.ready, accountProcessesAndFilesRemoved=True,
                             wineSession=dict(kind="distribution-wine", defaultWinebus=True))

    def primary(self, error=None):
        (self.evidence / "result.json").write_text(json.dumps(self.baseline))
        if error: raise error
        return self.baseline

    def variant(self, _ready, _exe, _callback, evidence, _physical, **changes):
        evidence.mkdir()
        result = dict(scope="configured-distribution-wine-udev", result="passed", ready=self.ready,
                      accountProcessesAndFilesRemoved=True, defaultWinebus=False) | changes
        (evidence / "result.json").write_text(json.dumps(result))
        return result

    def run_all(self, primary):
        return run_after_default(primary, self.ready_path, "consumer.exe", lambda *_: None, self.evidence, "physical")

    def test_default_failure_is_retained_after_separate_configured_success(self):
        error = RuntimeError("original default feature failure")
        with patch("run_wine_variant.run_variant", side_effect=self.variant) as variant:
            with self.assertRaises(RuntimeError) as caught: self.run_all(lambda: self.primary(error))
        self.assertIs(caught.exception, error)
        variant.assert_called_once()
        result = json.loads((self.evidence / "result.json").read_text())
        self.assertEqual(result["result"], "failed")
        self.assertTrue(result["wineSession"]["defaultWinebus"])
        self.assertEqual(result["wineUdevVariant"]["result"], "passed")
        self.assertFalse(result["wineUdevVariant"]["replacesDefaultWineResult"])

    def test_cleanup_missing_or_wrong_guest_or_nondefault_session_prevents_variant(self):
        original = dict(self.baseline)
        for changes in (dict(accountProcessesAndFilesRemoved=False), dict(cleanupErrors=["ACL drift"]),
                        dict(ready=self.ready | dict(virtualMacHex="02d500000002")),
                        dict(wineSession=dict(kind="configured-distribution-wine-udev", defaultWinebus=False))):
            self.baseline = original | changes
            with patch("run_wine_variant.run_variant") as variant, self.assertRaises(RuntimeError):
                self.run_all(self.primary)
            variant.assert_not_called()

    def test_missing_default_result_and_stale_variant_evidence_prevent_new_execution(self):
        with patch("run_wine_variant.run_variant") as variant, self.assertRaises(FileNotFoundError):
            self.run_all(lambda: None)
        variant.assert_not_called()
        (self.root / "wine-udev-variant").mkdir()
        with patch("run_wine_variant.run_variant") as variant, self.assertRaises(RuntimeError):
            self.run_all(self.primary)
        variant.assert_not_called()

    def test_failed_variant_cleanup_cannot_be_reported_as_completed(self):
        def fail_cleanup(*args): return self.variant(*args, accountProcessesAndFilesRemoved=False)
        self.baseline["result"] = "passed"
        with patch("run_wine_variant.run_variant", side_effect=fail_cleanup), self.assertRaises(RuntimeError):
            self.run_all(self.primary)
        self.assertNotIn("wineUdevVariant", json.loads((self.evidence / "result.json").read_text()))

    def test_early_observer_failure_is_preserved_without_starting_variant(self):
        original = RuntimeError("Gain state restoration was not verified")
        def early_failure(): raise original
        with patch("run_wine_variant.run_variant") as variant, self.assertRaises(RuntimeError) as caught:
            self.run_all(early_failure)
        self.assertIs(caught.exception, original)
        variant.assert_not_called()
        self.assertFalse((self.evidence / "result.json").exists())


if __name__ == "__main__":
    unittest.main()
