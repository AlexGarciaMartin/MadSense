import io
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

import consumer_waits as waits


class WaitEvidenceTests(unittest.TestCase):
    def test_identity_preserves_comm_parentheses_and_rejects_uid_transition(self):
        with tempfile.TemporaryDirectory() as temp:
            proc = Path(temp); path = proc / "42"; path.mkdir()
            (path / "stat").write_text("42 (name with ) parentheses) D " + "0 " * 18 + "12345 0\n")
            (path / "status").write_text("Uid:\t1001\t1001\t1001\t1001\n")
            identity = waits.identity(42, proc)
            self.assertEqual(identity, dict(pid=42, startTicks=12345, uid=1001, state="D", parentPid=0))
            self.assertFalse(waits.same_process(identity, identity | {"startTicks":12346}))
            self.assertFalse(waits.same_process(identity, identity | {"uid":0}))
            (path / "status").write_text("Uid:\t1001\t0\t1001\t1001\n")
            with self.assertRaisesRegex(ValueError, "UID changed"):
                waits.identity(42, proc)

    def test_fields_are_bounded_and_missing_is_unavailable(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "stack"; path.write_bytes(b"x" * (waits.FIELD_BYTES + 1))
            self.assertEqual(waits.field(path), dict(text="x"*waits.FIELD_BYTES, truncated=True))
            self.assertIn("unavailable", waits.field(path.with_name("absent")))

    def test_snapshot_and_byte_caps_do_not_write_partial_records(self):
        observer = waits.WaitObserver(Path("unused"), 11, 1001); observer.stream = io.BytesIO()
        for _ in range(waits.MAX_SNAPSHOTS):
            self.assertTrue(observer.append(dict(monotonicNs=123)))
        before = observer.stream.getvalue()
        self.assertFalse(observer.append(dict(monotonicNs=124)))
        self.assertEqual(before, observer.stream.getvalue())
        observer.summary["snapshots"] = 0; observer.bytes = waits.TOTAL_BYTES - 1
        self.assertFalse(observer.append(dict(monotonicNs=125)))
        self.assertEqual(before, observer.stream.getvalue())

    def test_reused_registered_pid_is_removed_and_never_followed(self):
        observer = waits.WaitObserver(Path("unused"), 11, 1001)
        expected = dict(pid=42, startTicks=123, uid=1001, state="S", label="wine")
        observer.processes[42] = expected
        with patch.object(waits, "identity", return_value=expected | {"startTicks":124}):
            self.assertEqual(observer.current(), [])
        self.assertEqual(observer.processes, {})

    def test_dead_consumer_does_not_discard_a_captured_broker_wait(self):
        observer = waits.WaitObserver(Path("unused"), 11, 1001)
        broker = dict(pid=10, startTicks=1, uid=0, state="D")
        consumer = dict(pid=11, startTicks=2, uid=1001, state="S")
        captured = broker | {"tasks":[dict(tid=10, stack={"text":"hidraw_write"})]}
        with patch.object(observer, "process_snapshot", side_effect=[captured, FileNotFoundError()]):
            result = observer.snapshot([broker, consumer])
        self.assertEqual(result["processes"][0], captured)
        self.assertIn("unavailable", result["processes"][1])

    def test_observer_shutdown_is_recorded_without_masking_main_error(self):
        with tempfile.TemporaryDirectory() as temp:
            observer = waits.WaitObserver(Path(temp), 11, 1001)
            observer.unavailable("start", PermissionError("denied"))
            summary = observer.close()
            self.assertTrue(summary["stoppedBeforeCleanup"])
            self.assertIn("PermissionError", summary["unavailable"][0]["error"])
            self.assertTrue((Path(temp) / "kernel-waits-summary.json").exists())

    def test_child_registration_rejects_parent_replacement_and_child_pid_reuse(self):
        parent = dict(pid=11, startTicks=1, uid=1001, state="S", label="wine", parentPid=9)
        child = dict(pid=12, startTicks=2, uid=1001, state="S", parentPid=11)
        scenarios = ([parent | {"startTicks":99}],
                     [parent, child | {"parentPid":99}],
                     [parent, child, child | {"startTicks":99}],
                     [parent, child, child, parent | {"startTicks":99}])
        for answers in scenarios:
            observer = waits.WaitObserver(Path("unused"), 11, 1001)
            with self.subTest(answers=answers), patch.object(waits, "identity", side_effect=answers), \
                 patch.object(waits, "field", return_value={"text":"12"}), self.assertRaises(ValueError):
                observer.register_child(parent, 12)
            self.assertEqual(observer.processes, {})
        observer = waits.WaitObserver(Path("unused"), 11, 1001)
        with patch.object(waits, "identity", side_effect=[parent, child, child, parent]), \
             patch.object(waits, "field", return_value={"text":"12"}):
            observer.register_child(parent, 12)
        self.assertEqual(observer.processes[12], child | {"label":"wine:child"})

    def test_missing_child_membership_never_registers_same_uid_process(self):
        observer = waits.WaitObserver(Path("unused"), 11, 1001)
        parent = dict(pid=11, startTicks=1, uid=1001, state="S", label="wine")
        child = dict(pid=12, startTicks=2, uid=1001, state="S", parentPid=11)
        with patch.object(waits, "identity", side_effect=[parent, child]), \
             patch.object(waits, "field", return_value={"text":"13"}), self.assertRaises(ValueError):
            observer.register_child(parent, 12)
        self.assertEqual(observer.processes, {})

    def test_unjoined_worker_is_incomplete_diagnostic(self):
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as temp:
            observer = waits.WaitObserver(Path(temp), 11, 1001)
            observer.thread = Mock(); observer.thread.is_alive.return_value = True
            observer.thread_started = True
            result = observer.close()
            self.assertFalse(result["stoppedBeforeCleanup"])
            self.assertTrue(result["captureIncomplete"])
            observer.thread.join.assert_called_once_with(timeout=3)

    def test_thread_start_failure_never_joins_unstarted_thread_or_masks_error(self):
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as temp:
            observer = waits.WaitObserver(Path(temp), 11, 1001)
            thread = Mock(); thread.start.side_effect = RuntimeError("cannot start new thread")
            with patch.object(waits.os, "geteuid", return_value=0, create=True), \
                 patch.object(waits.subprocess, "run", return_value=Mock(stdout=b"10\n")), \
                 patch.object(waits, "identity", return_value=dict(pid=10, uid=0, startTicks=5)), \
                 patch.object(Path, "read_bytes", return_value=b"python3\0/usr/libexec/madsense/broker.py\0"), \
                 patch.object(observer, "register"), patch.object(waits.threading, "Thread", return_value=thread):
                observer.start()
            result = observer.close()
            thread.join.assert_not_called()
            self.assertTrue(observer.stream.closed)
            self.assertTrue(result["stoppedBeforeCleanup"])
            self.assertIn("cannot start new thread", result["unavailable"][0]["error"])
            self.assertIsNone(waits._active)


if __name__ == "__main__":
    unittest.main()
