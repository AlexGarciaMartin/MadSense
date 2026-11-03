"""Regression for existing evdev writers, which EVIOCGRAB does not exclude."""
from contextlib import contextmanager
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import broker_core as core
import broker


class ExistingOutputTests(unittest.TestCase):
    @contextmanager
    def proc(self, metadata="flags:\t02000002\n", identities=None, pid=7):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fd = root / str(pid) / "fd" / "3"
            fd.parent.mkdir(parents=True)
            fd.touch()
            info = root / str(pid) / "fdinfo" / "3"
            info.parent.mkdir()
            if metadata is not None:
                info.write_text(metadata)
            original = Path.stat
            values = iter(identities) if identities else None
            def node_info(path, *args, **kwargs):
                if path == fd:
                    value = next(values) if values else 42
                    if isinstance(value, Exception):
                        raise value
                    if isinstance(value, SimpleNamespace):
                        return value
                    return SimpleNamespace(st_mode=stat.S_IFCHR | 0o600, st_dev=1,
                                           st_ino=value, st_rdev=55)
                return original(path, *args, **kwargs)
            with patch.object(Path, "stat", node_info), patch.object(core, "process_start", return_value=123):
                yield root

    def scan(self, root, *, writers=(55,), **kwargs):
        return core.foreign_device_owners({54}, 9, root, writable_targets=writers, **kwargs)

    def test_writable_output_descriptors_block_even_with_cloexec_nonblock(self):
        for flags in ("1", "2", "02004001", "02004002"):
            with self.subTest(flags=flags), self.proc(f"pos:\t0\nflags:\t{flags}\nmnt_id:\t9\n") as root:
                self.assertEqual(self.scan(root), [(7, 123, "3")])

    def test_readonly_led_sound_descriptors_do_not_block(self):
        with self.proc("flags:\t02004000\n") as root:
            self.assertEqual(self.scan(root), [])

    def test_readonly_force_feedback_descriptor_blocks(self):
        with self.proc("flags:\t02004000\n") as root:
            self.assertEqual(self.scan(root, force_feedback_targets={55}), [(7, 123, "3")])

    def test_only_exact_readonly_systemd_stored_grab_is_exempt(self):
        stored = {"dev": 1, "inode": 42, "rdev": 55}
        with self.proc("flags:\t02004000\n", pid=1) as root:
            self.assertEqual(self.scan(root, force_feedback_targets={55},
                                       systemd_grabs=[stored], systemd_start=123), [])
            for start, grabs in ((None, [stored]), (124, [stored]), (123, []),
                                 (123, [{**stored, "inode": 43}]), (123, [{**stored, "dev": 2}])):
                with self.subTest(start=start, grabs=grabs):
                    self.assertEqual(self.scan(root, force_feedback_targets={55},
                        systemd_grabs=grabs, systemd_start=start), [(1, 123, "3")])
        with self.proc("flags:\t02004000\n", pid=7) as root:
            self.assertEqual(self.scan(root, force_feedback_targets={55},
                systemd_grabs=[stored], systemd_start=123), [(7, 123, "3")])
        with self.proc("flags:\t02004002\n", pid=1) as root:
            self.assertEqual(self.scan(root, force_feedback_targets={55},
                systemd_grabs=[stored], systemd_start=123), [(1, 123, "3")])

    def test_writable_input_only_touchpad_does_not_block(self):
        with self.proc() as root:
            self.assertEqual(self.scan(root, writers=()), [])

    def test_hidraw_reader_still_blocks_without_access_mode_metadata(self):
        with self.proc(metadata=None) as root:
            self.assertEqual(core.foreign_device_owners({55}, 9, root), [(7, 123, "3")])

    def test_missing_or_malformed_metadata_is_not_clear(self):
        for metadata in (None, "", "flags:\t8\n", "flags:\n2\n",
                         "flags:\t0\nflags:\t2\n", "flags:\t0\n" + "x" * 8192):
            with self.subTest(metadata=repr(metadata)[:40]), self.proc(metadata) as root:
                with self.assertRaises(core.BrokerError) as error:
                    self.scan(root)
                self.assertEqual(error.exception.code, "isolation_unverified")

    def test_descriptor_replacement_during_flags_read_is_reclassified(self):
        with self.proc(identities=[42, 43, 43, 43]) as root:
            self.assertEqual(self.scan(root), [(7, 123, "3")])

    def test_descriptor_continuous_replacement_never_passes_as_clear(self):
        with self.proc(identities=[42, 43, 44, 45, 46, 47]) as root:
            with self.assertRaises(core.BrokerError) as error:
                self.scan(root)
            self.assertEqual(error.exception.code, "isolation_unverified")
            self.assertIn("pid=7, fd=3, stage=node-after-metadata", str(error.exception))

    def test_initial_stat_closed_then_reused_by_regular_file_is_not_missing_metadata(self):
        regular = SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_dev=1, st_ino=90, st_rdev=0)
        with self.proc(identities=[FileNotFoundError(), regular, regular]) as root:
            self.assertEqual(self.scan(root), [])

    def test_closed_descriptor_recheck_permission_denial_is_not_clear(self):
        with self.proc(identities=[FileNotFoundError(), PermissionError()]) as root:
            with self.assertRaisesRegex(core.BrokerError, "Cannot inspect descriptor metadata.*pid=7, fd=3, stage=node-recheck"):
                self.scan(root)

    def test_initial_stat_closed_then_reused_by_target_still_blocks(self):
        for targets in ({"writers": (55,)}, {"writers": (), "force_feedback_targets": {55}}):
            with self.subTest(targets=targets), self.proc(identities=[FileNotFoundError(), 43, 43, 43]) as root:
                self.assertEqual(self.scan(root, **targets), [(7, 123, "3")])
        with self.proc(metadata=None, identities=[FileNotFoundError(), 43, 43, 43]) as root:
            self.assertEqual(core.foreign_device_owners({55}, 9, root), [(7, 123, "3")])

    def test_fdinfo_closed_then_reused_by_writable_target_is_reclassified(self):
        original = Path.open
        missing = True
        def replaced(path, *args, **kwargs):
            nonlocal missing
            if path.parent.name == "fdinfo" and missing:
                missing = False
                raise FileNotFoundError("closed and reused during metadata open")
            return original(path, *args, **kwargs)
        with self.proc(identities=[42, 43, 43, 43]) as root, patch.object(Path, "open", replaced):
            self.assertEqual(self.scan(root), [(7, 123, "3")])

    def test_retry_cannot_use_another_process_identity(self):
        regular = SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_dev=1, st_ino=90, st_rdev=0)
        with self.proc(identities=[FileNotFoundError(), regular]) as root, \
                patch.object(core, "process_start", side_effect=[123, 124]):
            with self.assertRaisesRegex(core.BrokerError, "Process identity changed.*pid=7, fd=3"):
                self.scan(root)

    def test_live_process_missing_identity_is_not_clear(self):
        with self.proc(identities=[FileNotFoundError(), 43]) as root, \
                patch.object(core, "process_start", side_effect=[123, FileNotFoundError()]):
            with self.assertRaisesRegex(core.BrokerError, "live process identity is unavailable.*pid=7, fd=3"):
                self.scan(root)

    def test_descriptor_closed_during_flags_read_is_allowed(self):
        with self.proc(identities=[42, FileNotFoundError(), FileNotFoundError()]) as root:
            self.assertEqual(self.scan(root), [])

    def test_inaccessible_flags_are_not_clear(self):
        original = Path.open
        def denied(path, *args, **kwargs):
            if path.parent.name == "fdinfo":
                raise PermissionError("inaccessible descriptor flags")
            return original(path, *args, **kwargs)
        with self.proc() as root, patch.object(Path, "open", denied):
            with self.assertRaises(core.BrokerError) as error:
                self.scan(root)
            self.assertEqual(error.exception.code, "isolation_unverified")

    def test_audit_selects_only_output_capable_evdev_and_all_hidraw(self):
        lease = broker.Lease.__new__(broker.Lease)
        lease.active = False
        lease.saved = {"nodes": [
            {"path": "/dev/hidraw1", "rdev": 1}, {"path": "/dev/hidraw2", "rdev": 2},
            {"path": "/dev/input/event1", "rdev": 3, "outputCapable": True, "eventTypes": 1 << 0x15},
            {"path": "/dev/input/event2", "rdev": 4, "outputCapable": False}]}
        with patch.object(broker, "foreign_device_owners", return_value=[(7, 123, "3")]) as scan:
            with self.assertRaises(core.BrokerError) as error:
                lease.audit_foreign()
            self.assertEqual(error.exception.code, "physical_device_busy")
        self.assertEqual(scan.call_args.kwargs, {"writable_targets": {3}, "force_feedback_targets": {3},
                                                "systemd_grabs": (), "systemd_start": None})
        self.assertEqual(scan.call_args.args[0], {1, 2})

    def test_initial_audit_cannot_exempt_stale_systemd_manifest(self):
        lease = broker.Lease.__new__(broker.Lease)
        lease.active = False
        lease.saved = {"nodes": [{"path": "/dev/hidraw1", "rdev": 1}],
                       "storedGrabs": [{"dev": 1, "inode": 42, "rdev": 55}], "systemdStart": 123}
        with patch.object(broker, "foreign_device_owners", return_value=[]) as scan:
            lease.audit_foreign()
            self.assertEqual(scan.call_args.kwargs["systemd_grabs"], ())
            self.assertIsNone(scan.call_args.kwargs["systemd_start"])
            lease.active = True
            lease.audit_foreign()
            self.assertEqual(scan.call_args.kwargs["systemd_grabs"], lease.saved["storedGrabs"])
            self.assertEqual(scan.call_args.kwargs["systemd_start"], 123)


if __name__ == "__main__":
    unittest.main()
