from contextlib import contextmanager
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import qemu_control as q
from stage_control import ROOT, RUNNER, edits


class PayloadTests(unittest.TestCase):
    @contextmanager
    def staged(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp)
            neutral=base/'neutral';core=base/'core';artifact=base/'artifact'
            for path in (neutral,core,artifact/'core-probe',artifact/'consumer-bin'):path.mkdir(parents=True)
            (neutral/'MadSense.Linux.Probe').write_bytes(b'neutral')
            (core/'MadSense.Linux.CoreProbe').write_bytes(b'core')
            (artifact/'core-probe/MadSense.Linux.CoreProbe').write_bytes(b'observed')
            (artifact/'consumer-bin/control_probe').write_bytes(b'control')
            originals={name:(ROOT/name).read_bytes() for name in edits()}
            with patch.object(q,'verify_build',return_value=({}, {}, originals)):
                value=q.payload_stage(base/'payload',neutral,core,artifact,'a'*40)
            payload=base/'payload'
            yield payload,value

    def test_staged_payload_uses_only_copied_observer_runner(self):
        with self.staged() as (payload,value):
            runner=payload/'linux/platform/integration/run_control_guest.py'
            self.assertIn('from control_session import run_suite',runner.read_text())
            self.assertNotIn('from control_session import run_suite',(payload/RUNNER).read_text())
            self.assertEqual((payload/'core-probe/MadSense.Linux.CoreProbe').read_bytes(),b'core')
            self.assertEqual((payload/'control-core-probe/MadSense.Linux.CoreProbe').read_bytes(),b'observed')
            compile(runner.read_text(),str(runner),'exec')
            for name,digest in value['payloadHashes'].items():self.assertEqual(q.digest(payload/name),digest)

    @unittest.skipUnless(sys.platform=='linux','Guest imports require Linux fcntl/pwd')
    def test_guest_imports_from_staged_tree_without_checkout(self):
        with self.staged() as (payload,_):
            program='import sys;sys.path[:0]='+repr([str(payload/path) for path in (
                'linux/platform/integration','linux/platform/tests','linux/research','linux/tests/control-report')])+';import run_control_guest,control_session,qemu_core;print("payload imports passed")'
            result=subprocess.run([sys.executable,'-I','-c',program],cwd=payload.parent,text=True,capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('payload imports passed',result.stdout)


if __name__=='__main__':unittest.main()
