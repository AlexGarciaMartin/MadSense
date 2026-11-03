import contextlib
import copy
import csv
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import helper_trace
from helper_override import HelperOverride, NAMES, SANDBOX, read_regular, validate_sources
from helper_trace_analysis import analyze_case, assemble, validate


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.lease = SimpleNamespace(lease_id="a"*32, pid=31, start=1234, active=True)
        helper_trace.initialize(self.lease)
        @helper_trace.measured("inventory")
        def inventory(self):
            return "original"
        @helper_trace.measured("verify")
        def verify(self):
            return inventory(self)
        for _ in range(20):
            self.assertEqual(verify(self.lease), "original")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            helper_trace.dump(self.lease)
        self.expected = self.lease.research_trace.identity
        self.journal = [dict(_PID=str(self.expected["helperPid"]), MESSAGE=line)
                        for line in output.getvalue().splitlines()]

    def test_complete_chunked_dump_reassembles_and_preserves_nested_durations(self):
        value, _ = assemble(self.journal, self.expected)
        rows = validate(value)
        self.assertEqual(len(rows), 40)
        for outer, inner in zip(rows[::2], rows[1::2]):
            self.assertLessEqual(outer["startNs"], inner["startNs"])
            self.assertGreaterEqual(outer["endNs"], inner["endNs"])

    def test_missing_repeated_corrupt_or_foreign_chunk_cannot_be_complete(self):
        for action in ("missing", "repeated", "corrupt", "foreign"):
            rows = copy.deepcopy(self.journal)
            if action == "missing": rows.pop()
            elif action == "repeated": rows.append(rows[0])
            elif action == "foreign": rows[0]["_PID"] = "1"
            else:
                value = json.loads(rows[0]["MESSAGE"])
                value["data"] = value["data"][:-1] + "X"
                rows[0]["MESSAGE"] = json.dumps(value)
            with self.subTest(action=action), self.assertRaises(ValueError):
                assemble(rows, self.expected)

    def test_open_overwritten_or_queued_scopes_are_not_complete_duration_evidence(self):
        original, _ = assemble(self.journal, self.expected)
        for name, bad in (("openScopes", 1), ("lateFinishes", 1), ("observerErrors", 1),
                          ("queuedAuditsNotStarted", 1), ("overwritten", 1), ("openScopes", False)):
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate(original | {name: bad})

    def test_unrelated_lease_is_not_selected_by_pid_alone(self):
        with self.assertRaises(ValueError):
            assemble(self.journal, self.expected | {"leaseId": "b"*32})

    def test_case_joins_exact_lease_and_real_scope_clocks_without_gil_claim(self):
        value, _ = assemble(self.journal, self.expected)
        spans = validate(value)
        start, end = min(row["startNs"] for row in spans), max(row["endNs"] for row in spans)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "diagnostics").mkdir()
            (root / "app").mkdir()
            process = dict(pid=self.expected["helperPid"], startTicks=88)
            (root / "process-samples.json").write_text(json.dumps(dict(
                before=dict(application=dict(pid=31, startTicks=1234), broker=process), after=dict(broker=process))))
            (root / "lease-at-measurement.json").write_text(json.dumps(dict(
                storedGrabs=[dict(name="madsense-grab-" + self.expected["leaseId"] + "-0")])) )
            journal = "\n".join(json.dumps(row) for row in self.journal).encode()
            (root / "diagnostics/broker-journal.stdout").write_bytes(journal)
            command = dict(complete=True, childReaped=True, returncode=0, timedOut=False, outputLimitReached=False,
                errors=[], stdout=dict(path="broker-journal.stdout", size=len(journal),
                                       sha256=hashlib.sha256(journal).hexdigest(), truncated=False))
            (root / "diagnostics/result.json").write_text(json.dumps(dict(commands={"broker-journal": command})))
            with (root / "samples.csv").open("w", newline="") as stream:
                writer = csv.DictWriter(stream, ["inject_before_ns", "inject_after_ns", "write_errno"])
                writer.writeheader()
                writer.writerow(dict(inject_before_ns=start-2_000_000, inject_after_ns=start-1_999_999, write_errno=0))
                writer.writerow(dict(inject_before_ns=end+2_000_000, inject_after_ns=end+2_000_001, write_errno=0))
            with (root / "app/input-trace.csv").open("w", newline="") as stream:
                writer = csv.DictWriter(stream, ["stage", "broker_read_ns", "tag"])
                writer.writeheader()
                writer.writerow(dict(stage=1, broker_read_ns=start-1_500_000, tag=0x81230000))
                writer.writerow(dict(stage=1, broker_read_ns=end+1_500_000, tag=0x81230001))
            report = analyze_case(root, process)
            self.assertEqual(report["activeOverlappingDurations"]["verify"]["count"], 20)
            self.assertEqual(len(report["retainedClientBrokerGapsAtLeast2Ms"][0]["overlappingHelperSpans"]), 40)
            self.assertFalse(report["gilCauseEstablished"])
            self.assertFalse(report["schedulingCauseEstablished"])
            with self.assertRaises(ValueError):
                analyze_case(root, process | {"startTicks": 89})
            command["stdout"]["path"] = "../outside"
            (root / "diagnostics/result.json").write_text(json.dumps(dict(commands={"broker-journal": command})))
            with self.assertRaises(ValueError):
                analyze_case(root, process)


class OverrideLifecycleTests(unittest.TestCase):
    def context(self):
        value = object.__new__(HelperOverride)
        value.service_touched = True
        value.created_directory = False
        value.created_dropin = True
        value.dropin = Path("unused-private.conf")
        value.dropin_bytes = b"owned exact drop-in"
        value.dropin_identity = {"inode": 17}
        value.created_dropin_dir = False
        value.record = dict(cleanupErrors=[])
        value.save = Mock()
        value.original_unchanged = Mock()
        value.command = Mock()
        return value

    def test_busy_or_changed_original_service_is_never_stopped_for_restore(self):
        for reason in ("busy", "changed"):
            value = self.context()
            value.wait_idle = Mock(return_value={})
            if reason == "busy": value.wait_idle.side_effect = RuntimeError("still active lease")
            else: value.original_unchanged.side_effect = RuntimeError("original changed")
            with self.subTest(reason=reason), self.assertRaises(RuntimeError):
                value.close()
            value.command.assert_not_called()
            self.assertTrue(value.record["cleanupErrors"])

    def test_modified_override_is_not_removed_or_used_to_stop_a_service(self):
        value = self.context()
        value.wait_idle = Mock(return_value={"DropInPaths": str(value.dropin), "MainPID": "42"})
        with patch("helper_override.read_regular", return_value=(b"foreign data", {})), self.assertRaises(RuntimeError):
            value.close()
        value.command.assert_not_called()

    def test_owned_unloaded_dropin_after_failed_reload_is_removed_while_idle(self):
        with tempfile.TemporaryDirectory() as temporary:
            value = self.context()
            value.dropin = Path(temporary) / "owned.conf"
            value.dropin.write_bytes(value.dropin_bytes)
            inactive = dict(DropInPaths="", MainPID="0", ActiveState="inactive")
            original = dict(inactive, **{name: "unchanged" for name in SANDBOX})
            value.record["originalProperties"] = original
            restored = original | {"MainPID": "777", "ActiveState": "active"}
            value.wait_idle = Mock(side_effect=[inactive, inactive, restored])
            value.process = Mock(return_value=dict(pid=777, startTicks=18))
            with patch("helper_override.read_regular", return_value=(value.dropin_bytes, value.dropin_identity)):
                value.close()
            self.assertFalse(value.dropin.exists())
            self.assertTrue(value.record["originalServiceRestored"])
            self.assertEqual(value.record["cleanupErrors"], [])
            self.assertEqual([call.args for call in value.command.call_args_list],
                [("systemctl", "stop", "madsense-broker.service"), ("systemctl", "daemon-reload"),
                 ("systemctl", "start", "madsense-broker.service")])

    def test_guest_guard_precedes_output_or_service_mutation(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "must-not-exist"
            with patch("helper_override.sys.platform", "linux"), patch("helper_override.os.geteuid", return_value=0, create=True), \
                 patch("capture_diagnostics._owned_guest", side_effect=RuntimeError("not owned")), \
                 patch("helper_override.subprocess.run") as run, self.assertRaises(RuntimeError):
                HelperOverride(Path(temporary), output, "foreign-token")
            self.assertFalse(output.exists())
            run.assert_not_called()

    @unittest.skipUnless(sys.platform == "linux", "Native O_NOFOLLOW and inode semantics required")
    def test_real_source_symlink_and_hash_mismatch_are_rejected(self):
        import hashlib
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            files = {}
            for name in NAMES:
                data = (name + " original").encode()
                (directory / name).write_bytes(data)
                files[name] = hashlib.sha256(data).hexdigest()
            overlay = dict(schema=1, kind="research-helper-copy", productionHelper=False,
                           decisionsChanged=False, files=files)
            self.assertEqual(set(validate_sources(directory, overlay)), set(NAMES))
            (directory / "broker.py").write_bytes(b"changed")
            with self.assertRaises(RuntimeError):
                validate_sources(directory, overlay)
            target = directory / "broker.py"
            link = directory / "link.py"
            link.symlink_to(target)
            before = target.read_bytes(), target.stat().st_mode
            with self.assertRaises(OSError):
                read_regular(link)
            self.assertEqual((target.read_bytes(), target.stat().st_mode), before)


if __name__ == "__main__":
    unittest.main()
