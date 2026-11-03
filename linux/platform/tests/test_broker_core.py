import base64
from contextlib import contextmanager, ExitStack
import errno
import json
import os
import queue
from pathlib import Path
import stat
import struct
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import broker_core as core
import broker


class AuthorizationTests(unittest.TestCase):
    def lease(self):
        lease = broker.Lease.__new__(broker.Lease)
        lease.pid, lease.uid, lease.start = 7, 1000, 11
        lease.alive = MagicMock()
        return lease

    def test_cancellation_reaps_only_its_owned_authorization_child(self):
        launch = broker.subprocess.Popen
        unrelated = launch([sys.executable, "-I", "-c", "import time; time.sleep(60)"],
                           stdin=broker.subprocess.DEVNULL, stdout=broker.subprocess.DEVNULL,
                           stderr=broker.subprocess.DEVNULL)
        owned = []
        try:
            with tempfile.TemporaryDirectory() as directory:
                ready = Path(directory) / "ready"
                def spawn_owned(args, **kwargs):
                    child = launch([sys.executable, "-I", "-c",
                                    "import pathlib,sys,time; pathlib.Path(sys.argv[1]).touch(); time.sleep(60)", str(ready)],
                                   **{key: value for key, value in kwargs.items() if key != "env"})
                    owned.append(child)
                    return child
                lease = self.lease()
                def cancelled_after_child_started():
                    if ready.exists():
                        raise core.BrokerError("owner_lost", "The requesting socket closed")
                lease.alive.side_effect = cancelled_after_child_started
                started = time.monotonic()
                with patch.object(broker, "tool", return_value="/usr/bin/pkcheck"), \
                     patch.object(broker.subprocess, "Popen", side_effect=spawn_owned) as spawn, \
                     patch.object(broker, "notify") as notify, self.assertRaises(core.BrokerError) as error:
                    lease.authorize()
                self.assertEqual(error.exception.code, "owner_lost")
                self.assertLess(time.monotonic() - started, 10, "Cancellation must not wait for the 120-second prompt")
                self.assertEqual(len(owned), 1)
                self.assertIsNotNone(owned[0].poll(), "The owned child must be reaped")
                self.assertIsNone(unrelated.poll(), "Unrelated processes must remain untouched")
                self.assertEqual(spawn.call_args.args[0], ["/usr/bin/pkcheck", "--action-id", "org.madsense.bridge.acquire",
                                                         "--process", "7,11,1000", "--allow-user-interaction"])
                self.assertEqual(notify.call_args_list, [unittest.mock.call("WATCHDOG_USEC=130000000\nWATCHDOG=1"),
                                                         unittest.mock.call("WATCHDOG_USEC=15000000\nWATCHDOG=1")])
        finally:
            for child in [unrelated, *owned]:
                if child.poll() is None:
                    child.kill()
                child.wait()

    def test_successful_authorization_racing_with_disconnect_is_rejected(self):
        lease = self.lease()
        connected = [True]
        lease.alive.side_effect = lambda: core.require(connected[0], "owner_lost", "Socket closed")
        child = MagicMock(); child.returncode = 0; child.poll.return_value = 0
        def complete(**_):
            connected[0] = False
            return None, b""
        child.communicate.side_effect = complete
        with patch.object(broker, "tool", return_value="/usr/bin/pkcheck"), \
             patch.object(broker.subprocess, "Popen", return_value=child), patch.object(broker, "notify"), \
             self.assertRaises(core.BrokerError) as error:
            lease.authorize()
        self.assertEqual(error.exception.code, "owner_lost")
        child.kill.assert_not_called()  # It already exited successfully.

    def test_disconnect_after_authorization_cannot_touch_a_device_or_journal(self):
        lease = self.lease()
        lease.authorize = MagicMock()
        lease.alive.side_effect = core.BrokerError("owner_lost", "Socket closed")
        with patch.dict(sys.modules, {"fcntl": MagicMock()}), \
             patch.object(os, "open") as opened, patch.object(broker, "atomic_json") as journal, \
             patch.object(broker, "install_rule") as rule, patch.object(broker, "command") as command, \
             self.assertRaises(core.BrokerError) as error:
            lease.acquire("/dev/hidraw1")
        self.assertEqual(error.exception.code, "owner_lost")
        opened.assert_not_called(); journal.assert_not_called(); rule.assert_not_called(); command.assert_not_called()

    def test_authorization_deadline_reaps_child_and_restores_watchdog(self):
        lease = self.lease()
        child = MagicMock(); child.poll.return_value = None
        with patch.object(broker, "tool", return_value="/usr/bin/pkcheck"), \
             patch.object(broker.subprocess, "Popen", return_value=child), patch.object(broker, "notify") as notify, \
             patch.object(broker.time, "monotonic", side_effect=[0, 121]), self.assertRaises(core.BrokerError) as error:
            lease.authorize()
        self.assertEqual(error.exception.code, "not_authorized")
        child.kill.assert_called_once(); child.communicate.assert_called_once_with()
        self.assertEqual(notify.call_args.args[0], "WATCHDOG_USEC=15000000\nWATCHDOG=1")

    @unittest.skipUnless(sys.platform == "linux", "Requires Linux pidfd and POLLRDHUP")
    def test_real_owner_half_close_is_detected_behind_unread_requests(self):
        endpoint, owner = broker.socket.socketpair()
        pidfd = os.pidfd_open(os.getpid())
        try:
            lease = broker.Lease(os.getpid(), 1000, core.process_start(os.getpid()), pidfd, endpoint)
            lease.alive()
            owner.sendall(b"unread request bytes")
            lease.alive()  # Ordinary queued data is not cancellation.
            owner.shutdown(broker.socket.SHUT_WR)
            with self.assertRaises(core.BrokerError) as error:
                lease.alive()
            self.assertEqual(error.exception.code, "owner_lost")
            self.assertEqual(endpoint.recv(64), b"unread request bytes", "The liveness check must not consume protocol data")
        finally:
            endpoint.close(); owner.close(); os.close(pidfd)


class ProtocolTests(unittest.TestCase):
    def parse(self, **value):
        return core.request(json.dumps({"id": 1, **value}).encode())

    def test_only_explicit_operations_and_fields(self):
        for value in ({"op": "exec", "command": "id"}, {"op": "release", "path": "/etc/passwd"},
                      {"op": "acquire", "path": "/dev/../etc/passwd"}, {"op": "acquire", "path": "/dev/input/event0"},
                      {"op": "attach", "host": "localhost", "port": 3000, "busId": "1-1"},
                      {"op": "attach", "host": "127.0.0.1", "port": True, "busId": "1-1"},
                      {"op": "detach", "busId": "0-1"}, {"op": "detach", "busId": "65536-1"},
                      {"op": "detach", "busId": "01-1"},
                      {"op": "get_feature", "reportId": 256, "size": 64}):
            with self.subTest(value=value), self.assertRaises(core.BrokerError):
                self.parse(**value)

    def test_report_bounds_and_duplicate_keys(self):
        payload = base64.b64encode(bytes(4096)).decode()
        self.assertEqual(len(self.parse(op="write", data=payload)["decoded"]), 4096)
        for payload in ("", "!", base64.b64encode(bytes(4097)).decode()):
            with self.assertRaises(core.BrokerError):
                self.parse(op="write", data=payload)
        with self.assertRaises(core.BrokerError):
            core.request(b'{"id":1,"op":"release","op":"acquire"}')
        with self.assertRaises(core.BrokerError):
            core.request(b" " * 16385)

    def test_valid_export_and_feature_limits(self):
        self.parse(op="attach", host="127.0.0.1", port=1024, busId="65535-1")
        self.parse(op="get_feature", reportId=255, size=4096)


class IdentityTests(unittest.TestCase):
    def test_proc_start_handles_parentheses_in_command(self):
        with tempfile.TemporaryDirectory() as directory:
            proc = Path(directory)
            (proc / "42").mkdir()
            (proc / "42" / "stat").write_text("42 (a (b) c) S " + " ".join(["0"] * 18 + ["12345"]))
            self.assertEqual(core.process_start(42, proc), 12345)

    def test_node_reuse_and_type_are_rejected(self):
        info = types.SimpleNamespace(st_mode=stat.S_IFCHR | 0o600, st_dev=1, st_ino=2, st_rdev=3)
        saved = {"dev": 1, "inode": 2, "rdev": 3}
        self.assertTrue(core.same_node(info, saved))
        for key in saved:
            changed = {**saved, key: 99}
            self.assertFalse(core.same_node(info, changed))
        info.st_mode = stat.S_IFREG | 0o600
        self.assertFalse(core.same_node(info, saved))

    def test_foreign_reader_and_permission_denial(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "7" / "fd").mkdir(parents=True)
            (root / "7" / "fd" / "3").touch()
            with patch.object(core, "process_start", return_value=123):
                original = Path.stat
                def fake_stat(path, *args, **kwargs):
                    if path.name == "3":
                        return types.SimpleNamespace(st_mode=stat.S_IFCHR, st_rdev=55)
                    return original(path, *args, **kwargs)
                with patch.object(Path, "stat", fake_stat):
                    self.assertEqual(core.foreign_device_owners(55, 9, root), [(7, 123, "3")])
                    self.assertEqual(core.foreign_device_owners({54, 55}, 9, root), [(7, 123, "3")])
                with patch.object(Path, "stat", side_effect=PermissionError()):
                    with self.assertRaises(core.BrokerError):
                        core.foreign_device_owners(55, 9, root)

    def test_journal_boot_and_path_guards(self):
        node = {"path": "/dev/hidraw1", "dev": 1, "inode": 2, "rdev": 3,
                "uid": 1000, "gid": 1000, "mode": 0o660, "acl": "user::rw-\n"}
        saved = {"version": 1, "boot": "old", "nodes": [node]}
        self.assertFalse(core.validate_journal(saved, "new"))
        self.assertTrue(core.validate_journal(saved, "old"))
        node["path"] = "/etc/passwd"
        with self.assertRaises(core.BrokerError):
            core.validate_journal(saved, "old")

    def test_pending_import_blocks_restore_until_owned_socket_recovered(self):
        saved = {"version": 1, "boot": "boot", "nodes": [], "importName": "madsense-import-" + "a" * 32}
        with patch.object(broker, "read_journal", return_value=saved), patch.object(broker, "boot_id", return_value="boot"):
            with self.assertRaises(core.BrokerError) as error:
                broker.restore_journal()
            self.assertIn("restart recovery", str(error.exception))

    def test_ioctl_codes_match_linux_uapi(self):
        self.assertEqual(broker.ioctl_code(2, 3, 8), 0x80084803)
        self.assertEqual(broker.ioctl_code(3, 7, 64), 0xC0404807)
        self.assertEqual(broker.EVIOCGRAB, 0x40044590)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        # Windows runs only fake-system checks; these names are actual flags on Linux.
        flags = patch.multiple(os, O_NONBLOCK=getattr(os, "O_NONBLOCK", 0),
                               O_CLOEXEC=getattr(os, "O_CLOEXEC", 0), O_NOFOLLOW=getattr(os, "O_NOFOLLOW", 0),
                               O_DIRECTORY=getattr(os, "O_DIRECTORY", 0), create=True)
        flags.start()
        self.addCleanup(flags.stop)

    def node(self):
        return {"path": "/dev/hidraw1", "dev": 1, "inode": 2, "rdev": 3,
                "uid": 1000, "gid": 1000, "mode": 0o660, "acl": "user::rw-\ngroup::rw-\nother::---\n"}

    def stored_lease(self):
        grab = {"name": "madsense-grab-" + "a" * 32 + "-0", "dev": 1, "inode": 2, "rdev": 3}
        return {"version": 1, "boot": "boot", "nodes": [], "storedGrabs": [grab],
                "isolationVerified": True, "importName": "madsense-import-" + "b" * 32}

    def test_active_grabs_missing_from_store_fail_closed(self):
        saved = self.stored_lease(); saved["importName"] = None
        for environment in ({}, {"LISTEN_PID": str(os.getpid()), "LISTEN_FDS": "0"}):
            with self.subTest(environment=environment), patch.dict(os.environ, environment, clear=True), \
                 patch.object(broker, "read_journal", return_value=saved), patch.object(broker, "boot_id", return_value="boot"), \
                 patch.object(broker, "reset_saved_device") as reset, patch.object(broker, "release_grab") as ungrab:
                with self.assertRaises(core.BrokerError):
                    broker.recover_imports(main_process=True)
                reset.assert_not_called(); ungrab.assert_not_called()

    def test_stop_post_without_inherited_store_does_not_claim_recovery(self):
        saved = self.stored_lease(); saved["importName"] = None
        with patch.dict(os.environ, {}, clear=True), patch.object(broker, "read_journal", return_value=saved), \
             patch.object(broker, "boot_id", return_value="boot"), patch.object(broker, "reset_saved_device") as reset:
            recovered = broker.recover_imports()
            self.assertEqual(recovered, set())
            with self.assertRaises(core.BrokerError):
                broker.restore_journal(recovered)
            reset.assert_not_called()

    def test_incomplete_pre_active_acquisition_can_rollback_missing_store(self):
        saved = self.stored_lease(); saved.update(importName=None, isolationVerified=False)
        with patch.dict(os.environ, {}, clear=True), patch.object(broker, "read_journal", return_value=saved), \
             patch.object(broker, "boot_id", return_value="boot"), patch.object(broker, "reset_saved_device"):
            self.assertEqual(broker.recover_imports(main_process=True), {saved["storedGrabs"][0]["name"]})

    def test_recovered_import_shutdown_and_neutral_precede_ungrab(self):
        saved = self.stored_lease()
        names = [saved["storedGrabs"][0]["name"], saved["importName"]]
        environment = {"LISTEN_PID": str(os.getpid()), "LISTEN_FDS": "2", "LISTEN_FDNAMES": ":".join(names)}
        current = types.SimpleNamespace(st_mode=stat.S_IFCHR, st_dev=1, st_ino=2, st_rdev=3)
        for failed_reset in (False, True):
            events = []
            endpoint = MagicMock(); endpoint.__enter__.return_value = endpoint
            endpoint.family = broker.socket.AF_INET; endpoint.type = broker.socket.SOCK_STREAM
            endpoint.getpeername.return_value = ("127.0.0.1", 12000)
            endpoint.shutdown.side_effect = lambda *_: events.append("shutdown")
            def reset(_saved):
                events.append("neutral")
                if failed_reset:
                    raise core.BrokerError("reset_failed", "simulated reset failure")
            with self.subTest(failed_reset=failed_reset), patch.dict(os.environ, environment, clear=True), \
                 patch.object(broker, "read_journal", return_value=saved), patch.object(broker, "boot_id", return_value="boot"), \
                 patch.object(os, "fstat", return_value=current), patch.object(os, "close", side_effect=lambda *_: events.append("close_grab")), \
                 patch.object(broker.socket, "socket", return_value=endpoint), \
                 patch.object(broker, "wait_owned_import", side_effect=lambda *_: events.append("wait")), \
                 patch.object(broker, "reset_saved_device", side_effect=reset), \
                 patch.object(broker, "release_grab", side_effect=lambda *_: events.append("ungrab")), \
                 patch.object(broker, "restore_journal") as restore:
                if failed_reset:
                    with self.assertRaises(core.BrokerError):
                        broker.recover_imports(main_process=True)
                    self.assertEqual(events, ["shutdown", "wait", "neutral"])
                else:
                    self.assertEqual(broker.recover_imports(main_process=True), set(names))
                    self.assertEqual(events, ["shutdown", "wait", "neutral", "ungrab", "close_grab"])
                restore.assert_not_called()

    def test_replaced_stored_grab_is_never_released(self):
        saved = self.stored_lease(); saved["importName"] = None
        environment = {"LISTEN_PID": str(os.getpid()), "LISTEN_FDS": "1",
                       "LISTEN_FDNAMES": saved["storedGrabs"][0]["name"]}
        replaced = types.SimpleNamespace(st_mode=stat.S_IFCHR, st_dev=1, st_ino=999, st_rdev=3)
        with patch.dict(os.environ, environment, clear=True), patch.object(broker, "read_journal", return_value=saved), \
             patch.object(broker, "boot_id", return_value="boot"), patch.object(os, "fstat", return_value=replaced), \
             patch.object(broker, "release_grab") as ungrab, patch.object(broker, "reset_saved_device") as reset:
            with self.assertRaises(core.BrokerError):
                broker.recover_imports(main_process=True)
            ungrab.assert_not_called(); reset.assert_not_called()

    def test_import_intermediate_and_error_states_do_not_prove_detach(self):
        saved = {"importToken": {"port": 0, "devid": 0x10001, "sockfd": 9}}
        status = MagicMock()
        for state in (5, 7):
            status.read_text.return_value = f"header\nhs 0000 {state:03d} 000 00000000 000000 0-0\n"
            with self.subTest(state=state), patch.object(Path, "glob", return_value=[status]), \
                 patch.object(broker.time, "monotonic", side_effect=[0, 1, 6]), patch.object(broker.time, "sleep"):
                with self.assertRaises(core.BrokerError) as error:
                    broker.wait_owned_import(saved)
                self.assertEqual(error.exception.code, "detach_failed")

    def test_import_null_state_still_waits_for_saved_virtual_node(self):
        saved = {"importToken": {"port": 0, "devid": 0x10001, "sockfd": 9},
                 "importDevice": {"path": "/sys/devices/platform/vhci_hcd.0/usb1/1-1", "dev": 1, "inode": 2}}
        status = MagicMock(); status.read_text.return_value = "header\nhs 0000 004 000 00000000 000000 0-0\n"
        current = types.SimpleNamespace(st_dev=1, st_ino=2)
        with patch.object(Path, "glob", return_value=[status]), patch.object(Path, "stat", return_value=current), \
             patch.object(broker.time, "monotonic", side_effect=[0, 1, 6]), patch.object(broker.time, "sleep"):
            with self.assertRaises(core.BrokerError):
                broker.wait_owned_import(saved)
        replaced = types.SimpleNamespace(st_dev=1, st_ino=999)
        with patch.object(Path, "glob", return_value=[status]), patch.object(Path, "stat", return_value=replaced), \
             patch.object(broker.time, "sleep") as sleep:
            broker.wait_owned_import(saved)
            sleep.assert_not_called()

    def test_import_observation_never_waits_for_different_used_import(self):
        saved = {"importToken": {"port": 0, "devid": 0x10001, "sockfd": 9},
                 "expectedImportPath": "/sys/devices/platform/vhci_hcd.0/usb1/1-1"}
        status = MagicMock(); status.read_text.return_value = "header\nhs 0000 006 003 00020001 000012 1-1\n"
        with patch.object(Path, "glob", return_value=[status]), patch.object(Path, "stat", side_effect=FileNotFoundError), \
             patch.object(broker.time, "sleep") as sleep:
            broker.wait_owned_import(saved)
            sleep.assert_not_called()

    def test_pre_enumeration_crash_requires_expected_path_absence(self):
        saved = {"importToken": {"port": 0, "devid": 0x10001, "sockfd": 9},
                 "expectedImportPath": "/sys/devices/platform/vhci_hcd.0/usb1/1-1"}
        status = MagicMock(); status.read_text.return_value = "header\nhs 0000 004 000 00000000 000000 0-0\n"
        with patch.object(Path, "glob", return_value=[status]), patch.object(Path, "stat", return_value=object()), \
             patch.object(broker.time, "monotonic", side_effect=[0, 1, 6]), patch.object(broker.time, "sleep"):
            with self.assertRaises(core.BrokerError) as error:
                broker.wait_owned_import(saved)
            self.assertEqual(error.exception.code, "detach_failed")
        with patch.object(Path, "glob", return_value=[status]), patch.object(Path, "stat", side_effect=FileNotFoundError):
            broker.wait_owned_import(saved)
        del saved["expectedImportPath"]
        with patch.object(Path, "glob", return_value=[status]), self.assertRaises(core.BrokerError) as error:
            broker.wait_owned_import(saved)
        self.assertEqual(error.exception.code, "recovery_failed")

    def test_expected_import_path_maps_status_controller_and_hs_local_index(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            status = base / "vhci_hcd.0" / "status.1"
            status.parent.mkdir()
            status.write_text("header\nhs 0016 004 000 00000000 000000 0-0\n"
                              "hs 0017 004 000 00000000 000000 0-0\nss 0024 004 000 00000000 000000 0-0\n")
            for number, speed in ((5, "480"), (6, "5000")):
                root = base / "vhci_hcd.1" / f"usb{number}"
                root.mkdir(parents=True)
                (root / "speed").write_text(speed)
                (root / "busnum").write_text(str(number))
            self.assertEqual(broker.expected_import_path(status, 17), base / "vhci_hcd.1" / "usb5" / "5-2")
            with self.assertRaises(core.BrokerError):
                broker.expected_import_path(status, 24)
            (base / "vhci_hcd.1" / "usb6" / "speed").write_text("480")
            with self.assertRaises(core.BrokerError):
                broker.expected_import_path(status, 16)

    def test_acl_snapshot_precedes_current_udev_policy(self):
        node = self.node()
        saved = {"version": 1, "boot": "boot", "nodes": [node],
                 "hidParent": {"path": "/sys/devices/virtual/0005:054C:0CE6.0001", "dev": 1, "inode": 2}}
        rule = MagicMock(); rule.exists.return_value = False
        current = types.SimpleNamespace(st_mode=stat.S_IFCHR, st_dev=1, st_ino=2, st_rdev=3)
        events = []
        with patch.object(broker, "read_journal", return_value=saved), patch.object(broker, "boot_id", return_value="boot"), \
             patch.object(broker, "RULE", rule), patch.object(broker, "JOURNAL", MagicMock()), \
             patch.object(os, "open", return_value=42), patch.object(os, "close"), patch.object(os, "fstat", return_value=current), \
             patch.object(os, "fchmod", create=True), patch.object(os, "fchown", create=True), \
             patch.object(Path, "exists", return_value=True), patch.object(Path, "stat", return_value=current), \
             patch.object(broker, "command", side_effect=lambda name, args, *rest: events.append((name, args[0]))):
            broker.restore_journal()
        self.assertEqual(events, [("setfacl", "--set-file=-"), ("udevadm", "control"),
                                  ("udevadm", "trigger"), ("udevadm", "settle")])

    def test_restore_never_changes_replaced_node(self):
        node = self.node()
        saved = {"version": 1, "boot": "boot", "nodes": [node]}
        rule = MagicMock(); rule.exists.return_value = False
        journal = MagicMock()
        replaced = types.SimpleNamespace(st_mode=stat.S_IFCHR, st_dev=1, st_ino=999, st_rdev=3)
        with patch.object(broker, "read_journal", return_value=saved), patch.object(broker, "boot_id", return_value="boot"), \
             patch.object(broker, "RULE", rule), patch.object(broker, "JOURNAL", journal), \
             patch.object(os, "open", return_value=42), patch.object(os, "close"), patch.object(os, "fstat", return_value=replaced), \
             patch.object(os, "fchmod", create=True) as chmod, patch.object(os, "fchown", create=True) as chown, \
             patch.object(broker, "command") as command:
            broker.restore_journal()
            chmod.assert_not_called(); chown.assert_not_called()
            command.assert_called_once_with("udevadm", ["control", "--reload-rules"])
            journal.unlink.assert_called_once()

    def test_rule_crash_before_install_never_exposes_partial_rule(self):
        with tempfile.TemporaryDirectory() as directory:
            rule = Path(directory) / "72-madsense-lease.rules"
            write = os.write
            def interrupt_write(fd, contents):
                write(fd, contents[:4])
                raise SystemExit("injected crash during write")
            with patch.object(broker, "RULE", rule), patch.object(os, "write", side_effect=interrupt_write):
                with self.assertRaises(SystemExit):
                    broker.install_rule(b"complete isolation rule\n")
            self.assertFalse(rule.exists())
            self.assertFalse(list(Path(directory).glob("*.rules")))

    def test_rule_crash_after_install_has_complete_journaled_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            rule = Path(directory) / "72-madsense-lease.rules"
            contents = b"complete isolation rule"
            link = os.link
            def interrupt_link(source, target, **options):
                link(source, target, **options)
                raise SystemExit("injected crash after atomic install")
            with patch.object(broker, "RULE", rule), patch.object(os, "link", side_effect=interrupt_link):
                with self.assertRaises(SystemExit):
                    broker.install_rule(contents)
            self.assertEqual(rule.read_bytes(), contents)

    def test_rule_install_never_overwrites_an_existing_rule(self):
        with tempfile.TemporaryDirectory() as directory:
            rule = Path(directory) / "72-madsense-lease.rules"
            rule.write_bytes(b"another root-owned rule")
            with patch.object(broker, "RULE", rule), self.assertRaises(FileExistsError):
                broker.install_rule(b"lease rule")
            self.assertEqual(rule.read_bytes(), b"another root-owned rule")

    def test_restore_reloads_after_crash_between_unlink_and_reload(self):
        saved = {"version": 1, "boot": "boot", "nodes": []}
        rule = MagicMock(); rule.exists.return_value = False
        journal = MagicMock()
        with patch.object(broker, "read_journal", return_value=saved), patch.object(broker, "boot_id", return_value="boot"), \
             patch.object(broker, "RULE", rule), patch.object(broker, "JOURNAL", journal), \
             patch.object(broker, "command", side_effect=core.BrokerError("operation_failed", "reload failed")) as command:
            with self.assertRaises(core.BrokerError):
                broker.restore_journal()
            journal.unlink.assert_not_called()
            command.side_effect = None
            broker.restore_journal()
            self.assertEqual(command.call_args_list, [unittest.mock.call("udevadm", ["control", "--reload-rules"])] * 2)
            journal.unlink.assert_called_once()

    def test_acl_restore_failure_preserves_journal(self):
        node = self.node()
        saved = {"version": 1, "boot": "boot", "nodes": [node]}
        rule = MagicMock(); rule.exists.return_value = False
        journal = MagicMock()
        current = types.SimpleNamespace(st_mode=stat.S_IFCHR, st_dev=1, st_ino=2, st_rdev=3)
        with patch.object(broker, "read_journal", return_value=saved), patch.object(broker, "boot_id", return_value="boot"), \
             patch.object(broker, "RULE", rule), patch.object(broker, "JOURNAL", journal), \
             patch.object(os, "open", return_value=42), patch.object(os, "close"), patch.object(os, "fstat", return_value=current), \
             patch.object(os, "fchmod", create=True), patch.object(os, "fchown", create=True), \
             patch.object(broker, "command", side_effect=core.BrokerError("operation_failed", "ACL restore failed")):
            with self.assertRaises(core.BrokerError):
                broker.restore_journal()
            journal.unlink.assert_not_called()

    def test_socket_shutdown_precedes_close_and_store_removal(self):
        lease = broker.Lease.__new__(broker.Lease)
        events = []
        endpoint = MagicMock()
        endpoint.shutdown.side_effect = lambda *_: events.append("shutdown")
        endpoint.close.side_effect = lambda: events.append("close")
        lease.import_socket = endpoint; lease.import_device = None; lease.bus_id = "1-1"
        lease.saved = {"importName": "madsense-import-test"}
        with patch.object(broker, "atomic_json", side_effect=lambda *_: events.append("journal")), \
             patch.object(broker, "notify", side_effect=lambda *_: events.append("remove")):
            lease.detach()
        self.assertEqual(events, ["shutdown", "close", "journal", "remove"])
        endpoint.shutdown.assert_called_once_with(broker.socket.SHUT_RDWR)
        self.assertIsNone(lease.saved["importName"])

    def test_failed_detach_preserves_all_grabs(self):
        lease = broker.Lease.__new__(broker.Lease)
        lease.active = True; lease.grabs = [12, 13]; lease.fd = 14
        lease.detach = MagicMock(side_effect=core.BrokerError("detach_failed", "still attached"))
        with patch.object(os, "close") as close, patch.object(broker, "restore_journal") as restore:
            with self.assertRaises(core.BrokerError):
                lease.close()
        self.assertFalse(lease.active)
        self.assertEqual(lease.grabs, [12, 13])
        close.assert_not_called(); restore.assert_not_called()

    def test_neutral_reports_match_all_shared_encoder_vectors(self):
        fixtures = json.loads(Path(__file__).with_name("neutral-reports.json").read_text())
        self.assertEqual(len(fixtures), 48)
        for vector in fixtures:
            with self.subTest(kind=vector["kind"], sequence=vector["sequence"]):
                self.assertEqual(core.neutral_report(vector["kind"], vector["sequence"]).hex(), vector["hex"])

    def test_all_hidraw_interfaces_are_audited(self):
        lease = broker.Lease.__new__(broker.Lease)
        lease.active = False
        lease.saved = {"nodes": [{"path": "/dev/hidraw1", "rdev": 1}, {"path": "/dev/hidraw2", "rdev": 2},
                                  {"path": "/dev/input/event1", "rdev": 3}]}
        with patch.object(broker, "foreign_device_owners", return_value=[]) as audit:
            lease.audit_foreign()
        audit.assert_called_once_with({1, 2}, os.getpid(), writable_targets=set(),
                                      force_feedback_targets=set(), systemd_grabs=(), systemd_start=None)

    def test_audit_failure_and_timeout_end_verification(self):
        lease = broker.Lease.__new__(broker.Lease)
        lease.audit = queue.Queue(); lease.audit.put("A foreign reader appeared")
        with self.assertRaises(core.BrokerError):
            lease.check_audit()
        lease.audit = queue.Queue(); lease.audit_started = 1
        with patch.object(broker.time, "monotonic", return_value=4), self.assertRaises(core.BrokerError):
            lease.check_audit()

    def test_initial_audit_has_two_second_deadline(self):
        lease = broker.Lease.__new__(broker.Lease)
        lease.audit = MagicMock(); lease.audit.get.side_effect = queue.Empty
        lease.start_audit = MagicMock()
        with self.assertRaises(core.BrokerError) as error:
            lease.initial_audit()
        self.assertEqual(error.exception.code, "isolation_unverified")
        lease.audit.get.assert_called_once_with(timeout=2)

    def test_permission_monitor_uses_no_subprocess_or_blocking_process_scan(self):
        lease = broker.Lease.__new__(broker.Lease)
        node = self.node(); node.update(uid=0, gid=0, mode=0o600)
        lease.saved = {"nodes": [node]}; lease.nodes = [node["path"]]
        lease.rule = b"rule"; lease.grabs = []; lease.import_socket = None; lease.active = False
        lease.alive = lambda: None; lease.inventory = lambda: lease.nodes
        current = types.SimpleNamespace(st_mode=stat.S_IFCHR | 0o600, st_dev=1, st_ino=2, st_rdev=3, st_uid=0, st_gid=0)
        rule = MagicMock(); rule.read_bytes.return_value = b"rule"
        with patch.object(broker, "RULE", rule), patch.object(os, "stat", return_value=current), \
             patch.object(os, "getxattr", side_effect=OSError(errno.ENODATA, "No ACL"), create=True), \
             patch.object(broker, "command") as command, patch.object(broker, "foreign_device_owners") as scan:
            lease.verify()
        command.assert_not_called(); scan.assert_not_called()

    def test_helper_input_queue_fails_at_eight_without_dropping(self):
        client = {"out": bytearray(), "sent": 0, "inputEnds": broker.deque()}
        for index in range(8):
            broker.queue_packet(client, {"event": "input", "data": str(index)})
        queued = bytes(client["out"])
        with self.assertRaises(core.BrokerError) as error:
            broker.queue_packet(client, {"event": "input", "data": "9"})
        self.assertEqual(error.exception.code, "client_stalled")
        self.assertEqual(bytes(client["out"]), queued)
        first_end = client["inputEnds"][0]
        broker.advance_output(client, first_end - 1)
        self.assertEqual(len(client["inputEnds"]), 8)
        broker.advance_output(client, 1)
        self.assertEqual(len(client["inputEnds"]), 7)
        broker.queue_packet(client, {"event": "input", "data": "9"})

    @contextmanager
    def import_fixture(self):
        lease = broker.Lease.__new__(broker.Lease)
        lease.pid = 7; lease.import_socket = None; lease.import_device = None
        lease.saved = {}; lease.neutral_sequence = 0; lease.bus_id = None
        lease.verify = MagicMock(); lease.alive = MagicMock(); lease.detach = MagicMock()
        endpoint = MagicMock(); endpoint.connect_ex.return_value = 0
        endpoint.getsockopt.return_value = 0; endpoint.fileno.return_value = 21
        endpoint.send.side_effect = len
        endpoint.recv.side_effect = BlockingIOError()
        now = [0]
        status = MagicMock()
        status.read_text.return_value = "header\nhs 0000 004 000 00000000 000000 0-0\n"
        expected = MagicMock(); expected.exists.return_value = False
        expected.__str__.return_value = "/sys/devices/platform/vhci_hcd.0/usb1/1-1"
        expected.stat.return_value = types.SimpleNamespace(st_dev=1, st_ino=2)
        with ExitStack() as stack:
            for item in (
                patch.object(Path, "iterdir", return_value=[object()]),
                patch.object(Path, "glob", return_value=[status]),
                patch.object(Path, "resolve", return_value=expected),
                patch.object(Path, "read_text", return_value="header\n0 0100007F:4000 0 0A 0 0 0 0 0 99\n"),
                patch.object(os, "readlink", return_value="socket:[99]"),
                patch.object(broker.socket, "SOCK_CLOEXEC", 0, create=True),
                patch.object(broker.socket, "socket", return_value=endpoint),
                patch.object(broker.select, "select", side_effect=lambda read, write, error, timeout: ([], write, [])),
                patch.object(broker.time, "monotonic", side_effect=lambda: now[0]),
                patch.object(broker.time, "sleep", side_effect=AssertionError("Import must yield, never sleep")),
                patch.object(broker, "expected_import_path", return_value=expected),
                patch.object(broker, "store_descriptor"),
                patch.object(broker, "descriptor_barrier_steps", return_value=iter(())),
                patch.object(broker, "atomic_json"),
            ):
                stack.enter_context(item)
            yield lease, endpoint, now, status, expected

    def test_slow_import_handshake_yields_and_has_total_deadline(self):
        with self.import_fixture() as (lease, endpoint, now, _, _):
            operation = lease.attach_steps(16384, "1-1")
            self.assertIsNone(next(operation))  # The exporter has sent no reply.
            lease.verify()  # Main-loop checks remain callable between steps.
            now[0] = 6
            with self.assertRaises(core.BrokerError) as error:
                next(operation)
            self.assertIn("handshake timed out", str(error.exception))
            endpoint.close.assert_called_once()
            lease.detach.assert_not_called()

    def test_owner_loss_cancels_pending_import_handshake(self):
        with self.import_fixture() as (lease, endpoint, _, _, _):
            operation = lease.attach_steps(16384, "1-1")
            next(operation)
            operation.close()
            endpoint.close.assert_called_once()
            lease.detach.assert_not_called()

    def test_enumeration_yields_and_cancellation_tears_down_owned_socket(self):
        for cancel in (False, True):
            with self.subTest(cancel=cancel), self.import_fixture() as (lease, endpoint, _, status, expected):
                metadata = struct.pack("!IIIHHHBBBBBB", 1, 1, 3, 0x054C, 0x0CE6, 0, 0, 0, 0, 1, 1, 1)
                endpoint.recv.side_effect = [struct.pack("!HHI", 0x0111, 3, 0),
                                            bytes(256) + b"1-1".ljust(32, b"\0") + metadata]
                operation = lease.attach_steps(16384, "1-1")
                self.assertIsNone(next(operation))  # Attached, not yet enumerated.
                self.assertIs(lease.import_socket, endpoint)
                if cancel:
                    operation.close()
                    lease.detach.assert_called_once()
                else:
                    status.read_text.return_value = "header\nhs 0000 006 003 00010001 000021 1-1\n"
                    expected.exists.return_value = True
                    with self.assertRaises(StopIteration):
                        next(operation)
                    self.assertEqual(lease.saved["importDevice"]["inode"], 2)
                    lease.detach.assert_not_called()


class ServiceLoopTests(unittest.TestCase):
    def test_idle_peers_expire_and_one_uid_cannot_take_all_slots(self):
        listener = MagicMock()
        endpoints = [MagicMock() for _ in range(4)]
        listener.accept.side_effect = [(endpoint, None) for endpoint in endpoints]
        for endpoint in endpoints:
            endpoint.getsockopt.return_value = struct.pack("3i", 7, 1000, 1000)
        runtime, path = MagicMock(), MagicMock()
        runtime.stat.return_value = types.SimpleNamespace(st_uid=0, st_mode=stat.S_IFDIR | 0o755)
        path.exists.return_value = False
        now = [0]; loops = [0]; handlers = {}
        def select_once(readable, writable, errors, timeout):
            loops[0] += 1
            now[0] = loops[0] if loops[0] < 4 else 15
            if loops[0] <= 4: return [listener], [], []
            handlers[broker.signal.SIGTERM](None, None)
            return [], [], []
        with ExitStack() as stack:
            pidfds = stack.enter_context(patch.object(os, "pidfd_open", return_value=101, create=True))
            for item in (
                patch.object(broker, "RUN", runtime), patch.object(broker, "SOCKET", path),
                patch.object(broker, "recover_imports", return_value=set()), patch.object(broker, "restore_journal"),
                patch.object(broker, "notify"), patch.object(os, "geteuid", return_value=0, create=True),
                patch.object(os, "chmod"), patch.object(os, "close"), patch.object(broker, "process_start", return_value=1),
                patch.object(broker.socket, "AF_UNIX", 1, create=True),
                patch.object(broker.socket, "SOCK_CLOEXEC", 0, create=True),
                patch.object(broker.socket, "SO_PEERCRED", 17, create=True),
                patch.object(broker.socket, "socket", return_value=listener),
                patch.object(broker.select, "select", side_effect=select_once),
                patch.object(broker.signal, "signal", side_effect=lambda kind, handler: handlers.update({kind: handler})),
                patch.object(broker.time, "monotonic", side_effect=lambda: now[0]),
            ):
                stack.enter_context(item)
            broker.serve()
        self.assertEqual(pidfds.call_count, 3)  # Third caller rejected; fourth gets an expired slot.
        for endpoint in endpoints:
            endpoint.close.assert_called_once()

    def test_preflight_reports_unresolved_journal_or_rule_without_a_lease(self):
        for journal_pending, rule_pending in ((True, False), (False, True)):
            with self.subTest(journal=journal_pending, rule=rule_pending):
                listener, endpoint = MagicMock(), MagicMock()
                listener.accept.return_value = (endpoint, None)
                endpoint.getsockopt.return_value = struct.pack("3i", 7, 1000, 1000)
                endpoint.recv.return_value = b'{"id":1,"op":"preflight"}\n'
                frames = []
                def send(data):
                    frames.extend(json.loads(line) for line in bytes(data).splitlines())
                    return len(data)
                endpoint.send.side_effect = send
                runtime, path, journal, rule = (MagicMock() for _ in range(4))
                runtime.stat.return_value = types.SimpleNamespace(st_uid=0, st_mode=stat.S_IFDIR | 0o755)
                path.exists.return_value = False
                journal.exists.return_value = journal_pending
                rule.exists.return_value = rule_pending
                loops, handlers = [0], {}
                def select_once(readable, writable, errors, timeout):
                    loops[0] += 1
                    if loops[0] == 1: return [listener], [], []
                    if loops[0] == 2: return [endpoint], [], []
                    handlers[broker.signal.SIGTERM](None, None)
                    return [], writable, []
                with ExitStack() as stack:
                    for item in (
                        patch.object(broker, "RUN", runtime), patch.object(broker, "SOCKET", path),
                        patch.object(broker, "JOURNAL", journal), patch.object(broker, "RULE", rule),
                        patch.object(broker, "recover_imports", return_value=set()), patch.object(broker, "restore_journal"),
                        patch.object(broker, "notify"),
                        patch.object(broker, "preflight", side_effect=core.BrokerError("recovery_failed", "Cleanup remains pending")),
                        patch.object(os, "geteuid", return_value=0, create=True), patch.object(os, "chmod"),
                        patch.object(os, "pidfd_open", return_value=101, create=True), patch.object(os, "close"),
                        patch.object(broker, "process_start", return_value=1),
                        patch.object(broker.socket, "AF_UNIX", 1, create=True),
                        patch.object(broker.socket, "SOCK_CLOEXEC", 0, create=True),
                        patch.object(broker.socket, "SO_PEERCRED", 17, create=True),
                        patch.object(broker.socket, "socket", return_value=listener),
                        patch.object(broker.select, "select", side_effect=select_once),
                        patch.object(broker.signal, "signal", side_effect=lambda kind, handler: handlers.update({kind: handler})),
                    ):
                        stack.enter_context(item)
                    broker.serve()
                self.assertEqual(len(frames), 1)
                self.assertFalse(frames[0]["result"]["available"])
                self.assertFalse(frames[0]["result"]["leaseActive"])
                self.assertTrue(frames[0]["result"]["recoveryPending"])

    def test_pending_attach_keeps_input_and_owner_eof_cleanup_live(self):
        listener, endpoint, lease = MagicMock(), MagicMock(), MagicMock()
        listener.accept.return_value = (endpoint, None)
        endpoint.getsockopt.return_value = struct.pack("3i", 7, 1000, 1000)
        endpoint.recv.side_effect = [b'{"id":0,"op":"preflight"}\n'
                                     b'{"id":1,"op":"acquire","path":"/dev/hidraw1"}\n'
                                     b'{"id":2,"op":"attach","host":"127.0.0.1","port":16384,"busId":"1-1"}\n'
                                     b'{"id":3,"op":"preflight"}\n', b""]
        frames, events = [], []
        def send(data):
            frames.extend(json.loads(line) for line in bytes(data).splitlines())
            return len(data)
        endpoint.send.side_effect = send
        lease.fd = 100; lease.pidfd = 101; lease.active = True
        lease.acquire.return_value = {"isolationVerified": True}
        def pending_import(*_):
            try:
                events.append("attach-start")
                while True:
                    yield
            finally:
                events.append("cancel-import")
        lease.attach_steps.side_effect = pending_import
        lease.close.side_effect = lambda: events.append("lease-close")
        runtime, path = MagicMock(), MagicMock()
        runtime.stat.return_value = types.SimpleNamespace(st_uid=0, st_mode=stat.S_IFDIR | 0o755)
        path.exists.return_value = False
        now = [0]; loops = [0]; handlers = {}
        def select_once(readable, writable, errors, timeout):
            loops[0] += 1; now[0] += .6
            if loops[0] == 1: return [listener], [], []
            if loops[0] == 2: return [endpoint], writable, []
            if loops[0] in (3, 4, 5): return [lease.fd], writable, []
            if loops[0] == 6: return [endpoint], writable, []
            handlers[broker.signal.SIGTERM](None, None)
            return [], writable, []
        with ExitStack() as stack:
            for item in (
                patch.object(broker, "RUN", runtime), patch.object(broker, "SOCKET", path),
                patch.object(broker, "JOURNAL", path), patch.object(broker, "RULE", path),
                patch.object(broker, "recover_imports", return_value=set()), patch.object(broker, "restore_journal"),
                patch.object(broker, "notify"), patch.object(broker, "preflight"),
                patch.object(broker, "Lease", return_value=lease),
                patch.object(os, "geteuid", return_value=0, create=True), patch.object(os, "chmod"),
                patch.object(os, "pidfd_open", return_value=101, create=True), patch.object(os, "close"),
                patch.object(os, "read", return_value=b"\x01\x02"), patch.object(broker, "process_start", return_value=1),
                patch.object(broker.socket, "AF_UNIX", 1, create=True),
                patch.object(broker.socket, "SOCK_CLOEXEC", 0, create=True),
                patch.object(broker.socket, "SO_PEERCRED", 17, create=True),
                patch.object(broker.socket, "socket", return_value=listener),
                patch.object(broker.select, "select", side_effect=select_once),
                patch.object(broker.signal, "signal", side_effect=lambda kind, handler: handlers.update({kind: handler})),
                patch.object(broker.time, "monotonic", side_effect=lambda: now[0]),
            ):
                stack.enter_context(item)
            broker.serve()
        self.assertEqual(events, ["attach-start", "cancel-import", "lease-close"])
        self.assertEqual(sum(row.get("event") == "input" for row in frames), 3)
        self.assertGreaterEqual(lease.verify.call_count, 3)
        self.assertFalse(any(row.get("id") == 2 for row in frames))
        status = {row["id"]: row["result"] for row in frames if row.get("id") in (0, 3)}
        self.assertTrue(status[0]["available"])
        self.assertFalse(status[0]["leaseActive"])
        self.assertFalse(status[0]["recoveryPending"])
        self.assertTrue(status[3]["available"])
        self.assertTrue(status[3]["leaseActive"], "Availability must not conceal an active import/lease")
        self.assertFalse(status[3]["recoveryPending"])


if __name__ == "__main__":
    unittest.main()
