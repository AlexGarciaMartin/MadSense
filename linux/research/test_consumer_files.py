import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest

from consumer_files import WRITE_PROGRAM, write_private_file


class PrivateFileContractTests(unittest.TestCase):
    def test_secret_uses_inherited_pipe_not_argv_environment_or_output(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = []
            session = SimpleNamespace(root=Path(directory), uid=12345, gid=12345)
            secret = b"unique-private-Xauthority-cookie-8e72"
            def command(label, argv, **kwargs):
                self.assertNotIn(secret.decode(), repr(argv))
                self.assertNotIn("env", kwargs)
                self.assertEqual(len(kwargs["pass_fds"]), 1)
                self.assertEqual(os.read(kwargs["pass_fds"][0], 4097), secret)
                calls.append(label)
                return {}, json.dumps(dict(bytes=len(secret), uid=session.uid, gid=session.gid,
                                           mode="0600", operation=argv[-1])).encode()
            session.command = command
            write_private_file(session, session.root / "home/Xauthority", secret)
            write_private_file(session, session.root / "home/Xauthority", secret, replace=True)
            self.assertEqual(calls, ["private-config-1", "private-config-2"])
            self.assertFalse((session.root / "home").exists(), "Parent must not create user files itself")

    def test_escape_and_large_content_rejected_before_child(self):
        with tempfile.TemporaryDirectory() as directory:
            session = SimpleNamespace(root=Path(directory), uid=12345, gid=12345,
                                      command=lambda *_a, **_k: self.fail("must not execute"))
            for path, content in ((session.root / "outside", b"a"),
                                  (session.root / "home/../outside", b"a"),
                                  (session.root / "home/a", b"a"*4097)):
                with self.assertRaises(RuntimeError):
                    write_private_file(session, path, content)


@unittest.skipUnless(sys.platform == "linux" and os.geteuid() != 0,
                     "Real dir_fd/O_NOFOLLOW tests require an ordinary Linux account")
class NativePrivateFileTests(unittest.TestCase):
    def invoke(self, home, relative, data=b"new-private-data", operation="create"):
        read_fd, write_fd = os.pipe()
        try:
            os.write(write_fd, data); os.close(write_fd); write_fd = None
            return subprocess.run([sys.executable, "-c", WRITE_PROGRAM, str(home), relative, str(read_fd), operation],
                                  pass_fds=(read_fd,), capture_output=True, timeout=10)
        finally:
            os.close(read_fd)
            if write_fd is not None: os.close(write_fd)

    def test_create_and_replace_keep_exact_content_ordinary_owner_and_private_modes(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "home"; home.mkdir(mode=0o700)
            first = self.invoke(home, ".config/pulse/cookie", b"first")
            self.assertEqual(first.returncode, 0, first.stderr)
            target = home / ".config/pulse/cookie"
            self.assertEqual(target.read_bytes(), b"first")
            self.assertEqual((target.stat().st_uid, target.stat().st_gid), (os.geteuid(), os.getegid()))
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(target.parent.stat().st_mode), 0o700)
            second = self.invoke(home, ".config/pulse/cookie", b"replacement", "replace")
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(target.read_bytes(), b"replacement")

    def test_parent_symlink_never_creates_or_changes_outside_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); home = root / "home"; home.mkdir(mode=0o700)
            outside = root / "outside"; outside.mkdir(mode=0o755)
            before = outside.stat()
            (home / "redirect").symlink_to(outside, target_is_directory=True)
            result = self.invoke(home, "redirect/config")
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((outside / "config").exists())
            self.assertEqual((before.st_mode, before.st_uid, before.st_gid),
                             (outside.stat().st_mode, outside.stat().st_uid, outside.stat().st_gid))

    def test_final_symlink_rejected_for_create_and_replace_without_touching_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); home = root / "home"; home.mkdir(mode=0o700)
            target = root / "unrelated"; target.write_bytes(b"keep"); target.chmod(0o600)
            before = target.stat()
            (home / "Xauthority").symlink_to(target)
            for operation in ("create", "replace"):
                result = self.invoke(home, "Xauthority", operation=operation)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(target.read_bytes(), b"keep")
                after = target.stat()
                self.assertEqual((before.st_mode, before.st_uid, before.st_gid, before.st_mtime_ns),
                                 (after.st_mode, after.st_uid, after.st_gid, after.st_mtime_ns))

    def test_hardlinked_or_nonprivate_existing_file_is_not_truncated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); home = root / "home"; home.mkdir(mode=0o700)
            outside = root / "outside"; outside.write_bytes(b"keep"); outside.chmod(0o600)
            os.link(outside, home / "hardlink")
            result = self.invoke(home, "hardlink", operation="replace")
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(outside.read_bytes(), b"keep")
            target = home / "public"; target.write_bytes(b"keep-public"); target.chmod(0o644)
            result = self.invoke(home, "public", operation="replace")
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(target.read_bytes(), b"keep-public")
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o644)


if __name__ == "__main__":
    unittest.main()
