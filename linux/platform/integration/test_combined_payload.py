import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest

from qemu_core import prepare_combined_payload, run_stages


class CombinedPayloadTests(unittest.TestCase):
    def test_staged_guest_entrypoints_import_with_no_host_module_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            probes = []
            for name in ("Probe", "CoreProbe", "AppProbe"):
                probe = root / name
                probe.mkdir()
                (probe / ("MadSense.Linux." + name)).write_bytes(b"packaging fixture, never executed")
                probes.append(probe)
            payload = root / "payload"
            hashes = prepare_combined_payload(payload, *probes)
            self.assertIn("linux/research/kvm_acceleration.py", hashes)
            self.assertIn("linux/research/test_kvm_acceleration.py", hashes)
            self.assertIn("linux/research/host_probe.c", hashes)
            for name in ("ff_probe.c", "consumer_guard.h", "consumer_session.py", "run_consumers.py"):
                self.assertIn("linux/research/" + name, hashes)
            for name, digest in hashes.items():
                self.assertEqual(hashlib.sha256((payload / name).read_bytes()).hexdigest(), digest)
            code = "import runpy,sys,types,pathlib\n"
            if sys.platform != "linux":
                code += "sys.modules['fcntl']=types.ModuleType('fcntl')\nsys.modules['pwd']=types.ModuleType('pwd')\n"
            # Match normal `python script.py`: only that staged script's
            # directory is added, never a source-tree fallback.
            code += "sys.path.insert(0,str(pathlib.Path(sys.argv[1]).parent))\n"
            code += "runpy.run_path(sys.argv[1], run_name='payload_import_check')\n"
            for entry in ("linux/platform/integration/run_core_guest.py", "linux/tests/app-integration/run_app_guest.py",
                          "linux/platform/integration/qemu_core.py", "linux/tests/app-integration/xbox_ff.py"):
                result = subprocess.run([sys.executable, "-I", "-c", code, str(payload / entry)],
                                        cwd=payload, capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 0, entry + "\n" + result.stderr)

    def test_failed_stage_never_starts_a_later_actor(self):
        for exits, expected in (([1], ["baseline"]), ([0, 1], ["baseline", "core"]),
                                ([0, 0, 1], ["baseline", "core", "app"]), ([0, 0, 0], ["baseline", "core", "app"])):
            calls = []
            def run(name, command, timeout, required):
                self.assertFalse(required)
                self.assertGreater(timeout, 0)
                calls.append(name.removesuffix("-run"))
                if name == "app-run":
                    self.assertTrue(command.endswith(" --require-core"))
                return SimpleNamespace(returncode=exits[len(calls) - 1])
            guest = SimpleNamespace(token="owned", provenance={}, run=run)
            run_stages(guest)
            self.assertEqual(calls, expected)
            self.assertEqual(guest.provenance, {name + "Exit": code for name, code in zip(expected, exits)})

    def test_protocol_is_opt_in_and_runs_only_after_all_original_actors_pass(self):
        for exits in ([1], [0, 1], [0, 0, 1], [0, 0, 0, 0], [0, 0, 0, 1]):
            calls = []
            def run(name, command, timeout, required):
                calls.append(name.removesuffix("-run"))
                return SimpleNamespace(returncode=exits[len(calls) - 1])
            guest = SimpleNamespace(token="owned", provenance={}, run=run)
            run_stages(guest, extended_protocol=True)
            self.assertEqual(calls, ["baseline", "core", "app", "protocol"][:len(exits)])

    def test_xbox_ff_stays_separate_and_never_runs_after_failed_prior_stage(self):
        for exits in ([1], [0, 1], [0, 0, 1], [0, 0, 0, 1], [0, 0, 0, 0, 0]):
            calls = []
            def run(name, command, timeout, required):
                calls.append(name.removesuffix("-run"))
                return SimpleNamespace(returncode=exits[len(calls) - 1])
            guest = SimpleNamespace(token="owned", provenance={}, run=run)
            run_stages(guest, extended_protocol=True, xbox_force_feedback=True)
            self.assertEqual(calls, ["baseline", "core", "app", "protocol", "xbox-ff"][:len(exits)])


if __name__ == "__main__":
    unittest.main()
