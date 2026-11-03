import ast
import contextlib
import io
import json
from pathlib import Path
import threading
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import helper_trace as trace
from stage_helper_trace import BROKER, CORE, RECORDER, ROOT, digest, expected_overlay, instrument, verify_overlay, verify_transform


class HelperRecorderTests(unittest.TestCase):
    def lease(self):
        lease = SimpleNamespace(lease_id="a" * 32, pid=123, start=456, active=True)
        trace.initialize(lease)
        return lease

    def test_return_and_original_exception_identity_survive_observer_failure(self):
        lease = self.lease()
        result, error = object(), RuntimeError("original")
        wrapped = trace.measured("verify")(lambda self: result)
        self.assertIs(wrapped(lease), result)
        def fail(self):
            raise error
        wrapped_failure = trace.measured("verify")(fail)
        with patch.object(lease.research_trace, "finish", side_effect=RuntimeError("observer")):
            with self.assertRaises(RuntimeError) as caught:
                wrapped_failure(lease)
            self.assertIs(caught.exception, error)
            self.assertIs(wrapped(lease), result)
        with patch.object(lease.research_trace, "begin", side_effect=RuntimeError("observer")):
            self.assertIs(wrapped(lease), result)
        self.assertGreater(lease.research_trace.observer_errors, 0)

    def test_live_auditor_is_not_joined_or_hidden_during_dump(self):
        lease = self.lease()
        entered, release = threading.Event(), threading.Event()
        @trace.measured("audit_foreign")
        def audit(self):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("Test barrier timed out")
        worker = threading.Thread(target=audit, args=(lease,))
        worker.start()
        try:
            self.assertTrue(entered.wait(5))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                trace.dump(lease)
            self.assertTrue(worker.is_alive())
            chunks = [json.loads(line) for line in output.getvalue().splitlines()]
            value = json.loads("".join(row["data"] for row in chunks))
            self.assertEqual(value["openScopes"], 1)
            self.assertFalse(value["waitedForAuditor"])
            self.assertEqual(value["rows"][0][-1], "open")
        finally:
            release.set()
            worker.join(5)
        self.assertFalse(worker.is_alive())

    def test_pending_thread_start_is_explicit_even_before_worker_enters(self):
        lease = self.lease()
        trace.measured("start_audit")(lambda self: None)(lease)
        value = lease.research_trace.snapshot()
        self.assertEqual(value["queuedAuditsNotStarted"], 1)
        self.assertEqual(value["openScopes"], 0)

    def test_wrap_and_nested_calls_remain_bounded_with_precise_identity(self):
        lease = self.lease()
        @trace.measured("inventory")
        def inventory(self):
            return 17
        @trace.measured("verify")
        def verify(self):
            return inventory(self)
        for _ in range(160):
            self.assertEqual(verify(lease), 17)
        value = lease.research_trace.snapshot()
        self.assertEqual(value["total"], 320)
        self.assertEqual(value["overwritten"], 64)
        self.assertEqual([row[0] for row in value["rows"]], list(range(64, 320)))
        self.assertEqual((value["leaseId"], value["ownerPid"], value["ownerStart"]), ("a"*32, 123, 456))
        self.assertTrue(all(row[-1] == "returned" and row[5] >= row[4] and row[7] >= row[6] and row[9] >= row[8]
                            for row in value["rows"]))

    def test_journal_error_cannot_replace_cleanup_return(self):
        lease = self.lease()
        with patch("builtins.print", side_effect=OSError("closed journal")):
            self.assertIsNone(trace.dump(lease))


class HelperOverlayTests(unittest.TestCase):
    def test_self_consistent_unapproved_source_or_extra_provenance_is_rejected(self):
        original, core, recorder = ((ROOT / name).read_bytes() for name in (BROKER, CORE, RECORDER))
        revision, archive = "a"*40, b"exact archive fixture"
        expected = expected_overlay(original, core, recorder, revision, digest(archive))
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for name, data in (("broker.py", instrument(original)), ("broker_core.py", core), ("research_helper_trace.py", recorder)):
                (directory / name).write_bytes(data)
            metadata = directory / "research-helper-overlay.json"
            metadata.write_text(json.dumps(expected))
            with patch("stage_helper_trace.subprocess.check_output", side_effect=[original, core, recorder, archive]):
                self.assertEqual(verify_overlay(directory, revision), expected)
            changed = instrument(original) + b"\nprint('unapproved transformation')\n"
            (directory / "broker.py").write_bytes(changed)
            forged = json.loads(json.dumps(expected))
            forged["files"]["broker.py"] = digest(changed)
            forged["changes"][BROKER]["instrumentedSha256"] = digest(changed)
            with self.assertRaises(ValueError):
                verify_transform(directory, forged, original, core, recorder, revision=revision, archive_digest=digest(archive))
            (directory / "broker.py").write_bytes(instrument(original))
            with self.assertRaises(ValueError):
                verify_transform(directory, expected | {"extra": True}, original, core, recorder,
                                 revision=revision, archive_digest=digest(archive))
            (directory / "unexpected.py").write_text("extra code")
            with self.assertRaises(ValueError):
                verify_transform(directory, expected, original, core, recorder, revision=revision, archive_digest=digest(archive))

    def test_exact_original_methods_are_only_decorated(self):
        source = (ROOT / BROKER).read_bytes()
        original, observed = ast.parse(source), ast.parse(instrument(source))
        original_class = next(n for n in original.body if isinstance(n, ast.ClassDef) and n.name == "Lease")
        observed_class = next(n for n in observed.body if isinstance(n, ast.ClassDef) and n.name == "Lease")
        for name in trace.STAGES:
            before = next(n for n in original_class.body if isinstance(n, ast.FunctionDef) and n.name == name)
            after = next(n for n in observed_class.body if isinstance(n, ast.FunctionDef) and n.name == name)
            self.assertEqual(len(after.decorator_list), 1)
            after.decorator_list = []
            self.assertEqual(ast.dump(before), ast.dump(after))
        compile(instrument(source), "research-broker-copy", "exec")


if __name__ == "__main__":
    unittest.main()
