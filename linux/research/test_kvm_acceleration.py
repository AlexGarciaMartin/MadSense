import errno
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import MagicMock, patch

import kvm_acceleration as kvm


class AclTests(unittest.TestCase):
    def test_named_grant_preserves_all_existing_principals_even_without_group_access(self):
        for group in ("---", "r--", "rw-", "--x"):
            original = f"user::rw-\ngroup::{group}\nother::---\n"
            result = kvm.acl_for_uid(original, 1001)
            rows = dict((row.rsplit(":", 1)[0], row.rsplit(":", 1)[1]) for row in result.splitlines())
            self.assertEqual(rows["user:"], "rw-")
            self.assertEqual(rows["other:"], "---")
            self.assertEqual(rows["user:1001"], "rw-")
            effective_group = "".join(permission if permission == mask else "-"
                                      for permission, mask in zip(rows["group:"], rows["mask:"]))
            self.assertEqual(effective_group, group)

    def test_extended_acl_is_rejected_without_unmasking_other_users(self):
        with self.assertRaises(RuntimeError):
            kvm.acl_for_uid("user::rw-\nuser:2000:rw-\ngroup::---\nmask::r--\nother::---\n", 1001)

    def saved(self):
        info = types.SimpleNamespace(st_dev=1, st_ino=2, st_rdev=3, st_uid=0, st_gid=993, st_mode=stat.S_IFCHR | 0o660)
        original = "user::rw-\ngroup::rw-\nother::---\n"
        return info, dict(schema=1, uid=1001, identity=kvm.node_identity(info), mode=0o660,
                          original=original, granted=kvm.acl_for_uid(original, 1001))

    def test_replaced_inode_or_unexpected_acl_is_never_restored(self):
        info, saved = self.saved()
        replaced = types.SimpleNamespace(**{**vars(info), "st_ino": 99})
        with patch.object(kvm, "set_acl") as mutate, self.assertRaises(RuntimeError):
            kvm.restore_exact(17, saved, 1001, replaced)
        mutate.assert_not_called()
        device = MagicMock(); device.lstat.return_value = info
        with patch.object(kvm, "DEVICE", device), patch.object(kvm.os, "fstat", return_value=info), \
             patch.object(kvm, "acl_text", return_value="changed by another actor"), \
             patch.object(kvm, "set_acl") as mutate, self.assertRaises(kvm.AclRestorationError) as error:
            kvm.restore_exact(17, saved, 1001, info)
        mutate.assert_not_called()
        self.assertEqual(error.exception.observation["observedAcl"], "changed by another actor")
        self.assertEqual(error.exception.observation["originalAcl"], saved["original"])
        self.assertEqual(error.exception.observation["grantedAcl"], saved["granted"])

    def test_verified_restoration_uses_the_opened_exact_device(self):
        info, saved = self.saved()
        device = MagicMock(); device.lstat.return_value = info
        with patch.object(kvm, "DEVICE", device), patch.object(kvm.os, "fstat", return_value=info), \
             patch.object(kvm, "acl_text", side_effect=[saved["granted"], saved["original"]]), \
             patch.object(kvm, "set_acl") as mutate:
            kvm.restore_exact(17, saved, 1001, info)
        mutate.assert_called_once_with(17, saved["original"])

    def test_named_grant_removed_with_exact_redundant_mask_restores_original(self):
        info, saved = self.saved()
        observed = "user::rw-\ngroup::rw-\nmask::rw-\nother::---\n"
        device = MagicMock(); device.lstat.return_value = info
        with patch.object(kvm, "DEVICE", device), patch.object(kvm.os, "fstat", return_value=info), \
             patch.object(kvm, "acl_text", side_effect=[observed, saved["original"]]), \
             patch.object(kvm, "set_acl") as mutate:
            result = kvm.restore_exact(17, saved, 1001, info)
        mutate.assert_called_once_with(17, saved["original"])
        self.assertEqual(result["observedAcl"], observed)
        self.assertEqual(result["restoreReason"], "original-with-redundant-mask")

    def test_redundant_mask_exception_never_accepts_changed_mask_entries_base_or_mode(self):
        info, saved = self.saved()
        redundant = "user::rw-\ngroup::rw-\nmask::rw-\nother::---\n"
        variants = [(redundant.replace("mask::rw-", "mask::r--"), info),
                    (redundant.replace("mask::rw-", "mask::rwx"), info),
                    (redundant.replace("group::", "user:2000:rw-\ngroup::"), info),
                    (redundant.replace("user::rw-", "user::r--"), info),
                    (redundant.replace("other::---", "other::r--"), info),
                    (redundant, types.SimpleNamespace(**{**vars(info), "st_mode": stat.S_IFCHR | 0o640}))]
        for observed, current in variants:
            with self.subTest(acl=observed, mode=current.st_mode):
                device = MagicMock(); device.lstat.return_value = current
                with patch.object(kvm, "DEVICE", device), patch.object(kvm.os, "fstat", return_value=current), \
                     patch.object(kvm, "acl_text", return_value=observed), patch.object(kvm, "set_acl") as mutate, \
                     self.assertRaises(kvm.AclRestorationError):
                    kvm.restore_exact(17, saved, 1001, current)
                mutate.assert_not_called()

    def test_partial_grant_failure_requires_restore_before_fallback(self):
        evidence = {}; access = kvm.TemporaryKvmAccess(evidence)
        with patch.object(kvm, "hosted"), patch.object(kvm.os, "geteuid", return_value=1001, create=True), \
             patch.object(kvm, "api_probe", return_value={"errno": errno.EACCES}), \
             patch.object(access, "root", side_effect=[RuntimeError("grant interrupted"), {"restored": True}]) as root:
            self.assertFalse(access.prepare())
        self.assertEqual(root.call_args_list, [unittest.mock.call("grant"), unittest.mock.call("restore")])
        self.assertTrue(evidence["temporaryAclRestored"])

    def test_root_error_observation_survives_nonzero_helper_exit(self):
        evidence = {}; access = kvm.TemporaryKvmAccess(evidence)
        response = dict(error=dict(message="external ACL change", observation={"observedAcl": "unexpected"}))
        completed = subprocess.CompletedProcess([], 1, json.dumps(response), "diagnostic\n")
        with patch.object(kvm.subprocess, "run", return_value=completed), \
             patch.object(kvm.os, "getuid", return_value=1001, create=True), self.assertRaises(RuntimeError):
            access.root("restore")
        self.assertEqual(evidence["rootOperations"][0]["response"], response)
        self.assertEqual(evidence["rootOperations"][0]["exitCode"], 1)

    @unittest.skipUnless(sys.platform == "linux" and Path("/usr/bin/getfacl").exists()
                         and Path("/usr/bin/setfacl").exists(), "Real Linux ACL tools required")
    def test_real_acl_roundtrip_on_own_temporary_inode_via_opath(self):
        # Exercise the actual external ACL tools and /proc/self/fd interface;
        # neither /dev/kvm nor any other existing machine permissions change.
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "owned-acl-node")
            path.touch(mode=0o640)
            fd = os.open(path, os.O_PATH | os.O_CLOEXEC | os.O_NOFOLLOW)
            try:
                info = os.fstat(fd)
                original = kvm.acl_text(fd)
                uid = 1001 if os.getuid() != 1001 else 1002
                granted = kvm.acl_for_uid(original, uid)
                saved = dict(schema=1, uid=uid, identity=kvm.node_identity(info), mode=stat.S_IMODE(info.st_mode),
                             original=original, granted=granted)
                kvm.set_acl(fd, granted)
                self.assertEqual(kvm.acl_text(fd), granted)
                with patch.object(kvm, "DEVICE", path):
                    observed = kvm.restore_exact(fd, saved, uid, os.fstat(fd))
                self.assertEqual(observed["restoredAcl"], original)
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), saved["mode"])
                # Reproduce the actual native failure: deleting only a named
                # user leaves a mask. It must equal the original group exactly.
                kvm.set_acl(fd, granted)
                subprocess.run(["/usr/bin/setfacl", "--remove", f"u:{uid}", "/proc/self/fd/" + str(fd)],
                               pass_fds=(fd,), check=True, capture_output=True, timeout=5)
                with patch.object(kvm, "DEVICE", path):
                    observed = kvm.restore_exact(fd, saved, uid, os.fstat(fd))
                self.assertEqual(observed["restoreReason"], "original-with-redundant-mask")
                self.assertEqual(kvm.acl_text(fd), original)
            finally:
                os.close(fd)


class QmpAndLifecycleTests(unittest.TestCase):
    def test_qmp_command_ignores_event_and_returns_matching_real_socket_response(self):
        client, server = socket.socketpair()
        errors = []
        def respond():
            try:
                request = json.loads(server.recv(4096))
                server.sendall(b'{"event":"STOP"}\n' + json.dumps(
                    {"id": request["id"], "return": {"enabled": True, "present": True}}).encode() + b"\n")
            except BaseException as error:
                errors.append(error)
        thread = threading.Thread(target=respond)
        qmp = kvm.Qmp.__new__(kvm.Qmp); qmp.socket = client; qmp.buffer = bytearray(); qmp.next_id = 0
        client.settimeout(2)
        try:
            thread.start()
            self.assertEqual(qmp.execute("query-kvm"), {"enabled": True, "present": True})
            thread.join(timeout=3)
            self.assertFalse(thread.is_alive()); self.assertFalse(errors)
        finally:
            client.close(); server.close()

    def test_api_candidate_without_qmp_kvm_is_reaped_and_restored_before_tcg(self):
        events = []
        def spawn(argv, **_):
            accelerator = argv[0]
            events.append("start-" + accelerator)
            child = MagicMock(); child.poll.return_value = None
            child.terminate.side_effect = lambda: events.append("terminate-" + accelerator)
            child.wait.side_effect = lambda **_: events.append("reap-" + accelerator)
            return child
        connection = MagicMock()
        connection.execute.side_effect = lambda command: {"enabled": False, "present": True} if command == "query-kvm" else {}
        with tempfile.TemporaryDirectory() as output:
            provenance = {}; owned = kvm.AcceleratedQemu(output, provenance)
            owned.access = MagicMock(); owned.access.prepare.return_value = True
            owned.access.restore.side_effect = lambda: events.append("restore")
            try:
                with patch.object(kvm.subprocess, "Popen", side_effect=spawn), patch.object(kvm, "Qmp", return_value=connection):
                    self.assertEqual(owned.start(lambda accelerator: [accelerator]), "tcg")
                self.assertEqual(events[:5], ["start-kvm", "terminate-kvm", "reap-kvm", "restore", "start-tcg"])
                self.assertFalse(provenance["kvmAcceleration"]["qmpKvmConfirmed"])
                self.assertEqual(provenance["accelerator"], "tcg")
            finally:
                owned.close()

    def test_failed_reap_prevents_acl_restoration_and_success_claim(self):
        with tempfile.TemporaryDirectory() as output:
            provenance = {"result": "passed"}; owned = kvm.AcceleratedQemu(output, provenance)
            owned.process = MagicMock(); owned.process.poll.return_value = None
            owned.process.wait.side_effect = subprocess.TimeoutExpired("owned qemu", 5)
            owned.access = MagicMock()
            try:
                with self.assertRaises(subprocess.TimeoutExpired):
                    owned.close()
                owned.access.restore.assert_not_called()
                self.assertEqual(provenance["result"], "failed")
                self.assertIsNot(provenance.get("ownedQemuStopped"), True)
            finally:
                owned.directory.cleanup()


if __name__ == "__main__":
    unittest.main()
