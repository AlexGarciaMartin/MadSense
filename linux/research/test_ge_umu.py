import copy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from unittest.mock import patch

from ge_umu_acquire import (UpstreamRedirect, checked_url, checksum_entry, copy_response,
                            inventory_archive, load_lock, safe_name, verify_asset)
from ge_umu_session import (WRITE_MARKER_PROGRAM, READ_PROTON_LOGS_PROGRAM, extract_payload,
                            offline_command, private_environment, run_consumer, capture_proton_logs)


def archive_with(path, name, data=b"payload", kind=tarfile.REGTYPE, link=""):
    with tarfile.open(path, "w") as archive:
        member = tarfile.TarInfo(name)
        member.type = kind
        member.linkname = link
        member.size = len(data) if kind == tarfile.REGTYPE else 0
        archive.addfile(member, io.BytesIO(data) if member.size else None)


class AcquisitionTests(unittest.TestCase):
    def test_lock_binds_concrete_sources_and_runtime_and_fits_budget(self):
        lock = load_lock()
        self.assertEqual(lock["runtimeAppId"], "4183110")
        self.assertEqual(lock["runtimeBuild"], "4.0.20261021.265997")
        self.assertNotIn("latest", json.dumps(lock))
        source = {item["name"]: item["url"] for item in lock["downloads"]}
        self.assertTrue(source["ge-source.tar.gz"].endswith(lock["geCommit"]))
        self.assertTrue(source["umu-source.tar.gz"].endswith(lock["umuCommit"]))

    def test_invalid_lock_duplicate_or_size_or_untrusted_url_rejected(self):
        original = load_lock()
        for change in (lambda lock: lock["downloads"].append(lock["downloads"][0]),
                       lambda lock: lock["downloads"][0].update(size=True),
                       lambda lock: lock["downloads"][0].update(url="https://example.org/payload")):
            altered = copy.deepcopy(original); change(altered)
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "lock.json"
                path.write_text(json.dumps(altered))
                with self.assertRaises(ValueError):
                    load_lock(path)

    def test_url_boundary_applies_to_redirects_and_credentials(self):
        good = "https://repo.steampowered.com/steamrt4/images/4.0.20261021.265997/SHA256SUMS"
        self.assertEqual(checked_url(good), good)
        for url in ("http://github.com/a", "https://github.com.evil.invalid/a",
                    "https://secret@github.com/a", "https://github.com:444/a", "file:///etc/passwd"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                UpstreamRedirect().redirect_request(None, None, 302, "", {}, url)

    def test_download_rejects_overflow_truncation_and_expired_budget(self):
        for data, size, deadline in ((b"abc", 2, time.monotonic()+2),
                                     (b"ab", 3, time.monotonic()+2),
                                     (b"ab", 2, time.monotonic()-1)):
            with self.assertRaises(ValueError):
                copy_response(io.BytesIO(data), io.BytesIO(), size, deadline)
        result = io.BytesIO()
        copy_response(io.BytesIO(b"ab"), result, 2, time.monotonic()+2)
        self.assertEqual(result.getvalue(), b"ab")

    def test_corruption_same_size_never_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "a"
            path.write_bytes(b"abc")
            item = dict(name="a", size=3, sha256=hashlib.sha256(b"abc").hexdigest())
            verify_asset(path, item)
            path.write_bytes(b"abd")
            with self.assertRaises(ValueError):
                verify_asset(path, item)

    def test_checksum_exact_filename_and_duplicate_rejection(self):
        sha = "a"*64
        self.assertEqual(checksum_entry(sha+" *payload.tar\n", "payload.tar"), sha)
        for text in (sha+" *other/payload.tar\n", (sha+"  payload.tar\n")*2,
                     sha+"  payload.tar.backup\n"):
            with self.assertRaises(ValueError):
                checksum_entry(text, "payload.tar")

    def test_archive_paths_cannot_escape_or_use_windows_names(self):
        for name in ("../escape", "/absolute", "C:/bad", "a\\b", "a/../../bad", ""):
            with self.subTest(name=name), self.assertRaises(ValueError):
                safe_name(name)

    def test_inventory_retains_exact_notice_bytes_and_does_not_follow_source_links(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); path = root / "source.tar"
            archive_with(path, "root/LICENSE", b"Exact notice\n")
            result = inventory_archive(path, root / "evidence")
            sha = result["entries"][0]["noticeSha256"]
            self.assertEqual((root / "evidence/notices" / sha).read_bytes(), b"Exact notice\n")
            archive_with(path, "root/upstream-packaging-link", kind=tarfile.SYMTYPE, link="/outside")
            result = inventory_archive(path, root / "evidence2")
            self.assertEqual(result["entries"][0]["link"], "/outside")
            self.assertFalse((root / "evidence2/root").exists())

    def test_payload_extraction_rejects_devices_and_traversal(self):
        for name, kind in (("../escape", tarfile.REGTYPE), ("root/device", tarfile.CHRTYPE)):
            with tempfile.TemporaryDirectory() as directory, patch("ge_umu_session.platform.system", return_value="Linux"):
                root = Path(directory)
                archive_with(root / "bad.tar", name, kind=kind)
                with self.assertRaises(ValueError):
                    extract_payload(root / "bad.tar", root / "new")
                self.assertFalse((root / "escape").exists())

    def test_data_filter_rejects_absolute_payload_symlink(self):
        with tempfile.TemporaryDirectory() as directory, patch("ge_umu_session.platform.system", return_value="Linux"):
            root = Path(directory)
            archive_with(root / "bad.tar", "root/link", kind=tarfile.SYMTYPE, link="/outside")
            with self.assertRaises((tarfile.AbsoluteLinkError, tarfile.LinkOutsideDestinationError)):
                extract_payload(root / "bad.tar", root / "new")

    def test_private_environment_has_explicit_paths_no_runtime_or_security_bypass(self):
        home = Path(tempfile.gettempdir()).resolve() / "owned-account"
        env = private_environment(home, home.parent / "owned-ge")
        self.assertEqual(env["WINEPREFIX"], str(home / "ge-prefix"))
        self.assertEqual(env["UMU_RUNTIME_UPDATE"], "0")
        self.assertEqual(env["UMU_HTTP_RETRIES"], "0")
        self.assertNotIn("UMU_NO_RUNTIME", env)
        self.assertNotIn("UMU_NO_PROTON", env)
        self.assertFalse(any("BWRAP" in key or "SANDBOX" in key for key in env))

    def test_offline_command_does_not_use_shell_or_root_mapping(self):
        argv = offline_command("/owned path/umu-run", "/owned path/probe.exe", ["sony", "a;echo b"])
        self.assertEqual(argv[:5], ["unshare", "--user", "--map-current-user", "--net", "--"])
        self.assertNotIn("--map-root-user", argv)
        self.assertEqual(argv[5:8], ["python3", "/owned path/umu-run", "Z:\\owned path\\probe.exe"])
        self.assertEqual(argv[-1], "a;echo b")
        self.assertNotIn("sh", argv)

    def test_console_path_mapping_rejects_ambiguous_or_non_pe_paths(self):
        for program in ("probe.exe", "/owned/../probe.exe", "/owned//probe.exe", "/owned/./probe.exe",
                        "/owned/a:probe.exe", "/owned/a\\probe.exe", "/owned/probe.exe\n", "/owned/probe.cmd",
                        '/owned/"probe.exe', '/owned/p*.exe', '/owned/p?.exe'):
            with self.subTest(program=program), self.assertRaises(ValueError):
                offline_command("/owned/umu-run", program, [])

    def test_secondary_log_snapshot_error_does_not_replace_runtime_failure(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);program=root/'consumer.exe';program.write_bytes(b'MZ-owned-test')
            original=RuntimeError('Original runtime timeout')
            session=SimpleNamespace(root=root,result={"geUmuSession":{}},save=lambda:None,
                command=Mock(side_effect=[original,RuntimeError('Diagnostic unavailable')]))
            # This orchestration test runs on Windows too; path conversion is
            # separately tested against the actual Linux-only input contract.
            with patch('ge_umu_session.offline_command',return_value=['unshare','--user','--map-current-user',
                    '--net','--','python3','umu','Z:\\owned\\consumer.exe']), self.assertRaises(RuntimeError) as caught:
                run_consumer(session,dict(launcher='umu',environment={}),program,[],label='case')
            self.assertIs(caught.exception,original)
            self.assertIn('Diagnostic unavailable',session.result['protonLogSnapshots'][0]['unavailable'])

    def test_collected_log_bytes_stay_outside_result_json_and_paths_are_fixed(self):
        import base64
        from types import SimpleNamespace
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);data=b'Owned diagnostic\n'
            value=dict(schema=1,uid=1001,logs=[dict(name='steam-default.log',bytes=len(data),capturedBytes=len(data),
                truncated=False,sha256=hashlib.sha256(data).hexdigest(),dataBase64=base64.b64encode(data).decode())])
            session=SimpleNamespace(root=root,output=root,uid=1001,result={},save=lambda:None,
                command=Mock(return_value=({},json.dumps(value).encode())))
            capture_proton_logs(session,'case')
            row=session.result['protonLogSnapshots'][0]['snapshot']['logs'][0]
            self.assertNotIn('dataBase64',row)
            self.assertEqual((root/row['evidenceFile']).read_bytes(),data)
            session.command.reset_mock();capture_proton_logs(session,'../escape')
            session.command.assert_not_called()
            self.assertIn('Unsafe Proton diagnostic label',session.result['protonLogSnapshots'][-1]['unavailable'])

    @unittest.skipUnless(sys.platform == 'linux', 'Real UID/dirfd/O_NOFOLLOW log snapshot requires Linux')
    def test_log_snapshot_is_bounded_and_does_not_read_prefix_or_payload(self):
        import base64
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);data=b'x'*(1024*1024+3)
            (root/'steam-default.log').write_bytes(data);(root/'prefix-secret').write_bytes(b'not a log')
            result=subprocess.run([sys.executable,'-c',READ_PROTON_LOGS_PROGRAM,root],capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr)
            value=json.loads(result.stdout);self.assertEqual(value['uid'],os.getuid())
            self.assertEqual(len(value['logs']),1);row=value['logs'][0]
            self.assertTrue(row['truncated']);self.assertEqual(row['bytes'],len(data))
            observed=base64.b64decode(row['dataBase64']);self.assertEqual(observed,data[:1024*1024])
            self.assertEqual(row['sha256'],hashlib.sha256(observed).hexdigest())

    @unittest.skipUnless(sys.platform == 'linux', 'Real Linux link and descriptor rejection requires Linux')
    def test_log_snapshot_rejects_directory_symlink_file_symlink_and_hardlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);home=root/'home';home.mkdir();outside=root/'outside';outside.write_bytes(b'retain')
            alias=root/'alias';alias.symlink_to(home,target_is_directory=True)
            for path in (alias,):
                result=subprocess.run([sys.executable,'-c',READ_PROTON_LOGS_PROGRAM,path],capture_output=True)
                self.assertNotEqual(result.returncode,0)
            log=home/'steam-default.log';log.symlink_to(outside)
            result=subprocess.run([sys.executable,'-c',READ_PROTON_LOGS_PROGRAM,home],capture_output=True)
            self.assertNotEqual(result.returncode,0);log.unlink();os.link(outside,log)
            result=subprocess.run([sys.executable,'-c',READ_PROTON_LOGS_PROGRAM,home],capture_output=True)
            self.assertNotEqual(result.returncode,0);self.assertEqual(outside.read_bytes(),b'retain')

    @unittest.skipUnless(sys.platform == "linux", "Real O_NOFOLLOW marker creation requires Linux")
    def test_marker_is_exclusive_regular_file_and_never_replaces_existing_data(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / ".installed.ok"
            first = subprocess.run([sys.executable, "-c", WRITE_MARKER_PROGRAM, marker], capture_output=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(marker.read_bytes(), b"ok\n")
            self.assertEqual(marker.stat().st_uid, os.geteuid())
            marker.write_bytes(b"retain existing evidence")
            second = subprocess.run([sys.executable, "-c", WRITE_MARKER_PROGRAM, marker], capture_output=True)
            self.assertNotEqual(second.returncode, 0)
            self.assertEqual(marker.read_bytes(), b"retain existing evidence")

    @unittest.skipUnless(sys.platform == "linux", "Real O_NOFOLLOW symlink rejection requires Linux")
    def test_marker_does_not_follow_user_symlink_or_touch_its_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "outside-runtime"
            target.write_bytes(b"unrelated data")
            before = target.stat()
            runtime = root / "runtime"; runtime.mkdir()
            marker = runtime / ".installed.ok"
            marker.symlink_to(target)
            result = subprocess.run([sys.executable, "-c", WRITE_MARKER_PROGRAM, marker], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue(marker.is_symlink())
            self.assertEqual(target.read_bytes(), b"unrelated data")
            after = target.stat()
            self.assertEqual((before.st_uid, before.st_gid, before.st_ino, before.st_mtime_ns),
                             (after.st_uid, after.st_gid, after.st_ino, after.st_mtime_ns))


if __name__ == "__main__":
    unittest.main()
