import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import ge_umu_standalone as ge


class StandaloneTests(unittest.TestCase):
    def fixtures(self, directory):
        base = Path(directory)
        scope = dict(result="passed", physicalDevices=False, radioInitialized=False,
                     environment=dict(kind="owned-qemu-guest", instance="token"))
        values = {"result.json": scope | dict(retainedGrabSamples=3, cases=[dict(name=x,passed=True) for x in ge.BROKER_CASES]),
            "core-experiment/result.json":scope | dict(applicationUiExercised=False, cases=[dict(name=x,passed=True) for x in ge.CORE_CASES]),
            "core-experiment/core/ready.json":dict(ownedGuestToken="token",pid=100),
            "core-experiment/core/stopped.json":dict(completed=True,cleanupConfirmed=True,evidenceOverflow=False,metrics=dict(inputOverflows=0))}
        for name,value in values.items():
            path=base/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value))
        return base

    def test_prior_actor_must_be_disposed_and_not_reused(self):
        with tempfile.TemporaryDirectory() as temp:
            base=self.fixtures(temp)
            self.assertEqual(ge.prior_gates(base,"token",200)["priorActorPid"],100)
            with self.assertRaisesRegex(RuntimeError,"fresh second"):
                ge.prior_gates(base,"token",100)
            with self.assertRaises(RuntimeError): ge.prior_gates(base,"another",200)
            path=base/"core-experiment/core/stopped.json";v=json.loads(path.read_text());v["cleanupConfirmed"]=False;path.write_text(json.dumps(v))
            with self.assertRaisesRegex(RuntimeError,"fully disposed"): ge.prior_gates(base,"token",200)

    def test_missing_or_failed_gate_prevents_ge_call(self):
        with tempfile.TemporaryDirectory() as temp:
            base=self.fixtures(temp);ready=base/"second-ready.json";ready.write_text(json.dumps(dict(ownedGuestToken="token",actorPid=200)))
            path=base/"result.json";v=json.loads(path.read_text());v["cases"].pop();path.write_text(json.dumps(v))
            with patch.object(ge,"_run_ge_suite") as invoke, self.assertRaises(RuntimeError):
                ge.run(ready,"pe",lambda _:None,base/"ge","physical","cache",base)
            invoke.assert_not_called()

    def test_existing_wine_failure_is_neither_required_nor_promoted(self):
        with tempfile.TemporaryDirectory() as temp:
            base=self.fixtures(temp);(base/"old-wine.json").write_text('{"result":"failed"}')
            self.assertEqual(ge.prior_gates(base,"token",200)["ownedGuestToken"],"token")
            self.assertEqual((base/"old-wine.json").read_text(),'{"result":"failed"}')

    def test_original_runtime_failure_and_cleanup_record_survive_wrapper(self):
        with tempfile.TemporaryDirectory() as temp:
            base=self.fixtures(temp);ready=base/"second-ready.json";ready.write_text(json.dumps(dict(ownedGuestToken="token",actorPid=200)))
            destination=base/"ge"
            original=RuntimeError("actual runtime failure")
            def fail(*args,**kwargs):
                destination.mkdir();(destination/"result.json").write_text(json.dumps(dict(result="failed",cleanupErrors=["retained node"])))
                raise original
            with patch.object(ge,"_run_ge_suite",side_effect=fail),self.assertRaises(RuntimeError) as caught:
                ge.run(ready,"pe",lambda _:None,destination,"physical","cache",base)
            self.assertIs(caught.exception,original)
            value=json.loads((destination/"result.json").read_text())
            self.assertEqual(value["cleanupErrors"],["retained node"])
            self.assertFalse(value["distributionWineExecuted"])
            self.assertFalse(value["priorDistributionResultsReused"])

    def test_same_strict_runtime_and_cleanup_assertions_remain_required(self):
        ready=dict(actorPid=200);gates=dict(priorActorPid=100)
        value=dict(scope=ge.SCOPE,geOnlyExperiment=True,distributionWineExecuted=False,priorDistributionResultsReused=False,
            replacesDistributionWineResult=False,officialValveProton=False,runtimeCacheCollected=False,
            priorFunctionalGates=gates,ready=ready,result="passed",accountProcessesAndFilesRemoved=True,
            cleanup=[dict(aclRestored=True)],geUmuSession=dict(consumerAssertionsPassed=True,runtimeExecutionConfirmed=True,
                freshPrefix=True,observedWineBuildId="actual exported build ID",cases=[dict(name=x,passed=True)
                    for x in ("ge-umu-sony-south","ge-umu-sony-neutral","ge-umu-wasapi")]))
        ge.verify_result(value,ready,gates)
        for change in (dict(distributionWineExecuted=True),dict(accountProcessesAndFilesRemoved=False),
                       dict(cleanupErrors=["failed"]),dict(result="failed")):
            bad=copy.deepcopy(value);bad.update(change)
            with self.assertRaises(RuntimeError):ge.verify_result(bad,ready,gates)
        bad=copy.deepcopy(value);bad["geUmuSession"]["cases"][2]["passed"]=False
        with self.assertRaises(RuntimeError):ge.verify_result(bad,ready,gates)

    def test_ge_prepare_error_survives_failure_to_reset_a_lost_fixture(self):
        from unittest.mock import Mock
        from ge_umu_consumer import _run_ge_suite
        with tempfile.TemporaryDirectory() as temp:
            cache=Path(temp)/"cache";cache.mkdir()
            session=Mock();session.result={}
            session.__enter__=Mock(return_value=session);session.__exit__=Mock(return_value=False)
            original=RuntimeError("actual prepare failure")
            def reset(_state):raise RuntimeError("fixture disappeared")
            with patch("ge_umu_consumer.ConsumerSession",return_value=session), \
                 patch("ge_umu_consumer.prepare",side_effect=original),self.assertRaises(RuntimeError) as caught:
                _run_ge_suite("ready","pe",reset,Path(temp)/"evidence","physical",cache)
            self.assertIs(caught.exception,original)
            self.assertIn("fixture disappeared",session.result["geNeutralResetError"])

class StandalonePayloadTests(unittest.TestCase):
    def test_real_staged_ge_only_tree_imports_without_checkout_fallback(self):
        import hashlib, subprocess, sys
        from test_ge_umu_payload import GePrivatePayloadTests
        from qemu_ge_only import stage_payload
        fixture=GePrivatePayloadTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        fixture.payload.rmdir()  # stage_payload creates its own fresh tree.
        neutral=fixture.root/"neutral";neutral.mkdir();(neutral/"MadSense.Linux.Probe").write_bytes(b"probe")
        core=fixture.root/"core";core.mkdir();(core/"MadSense.Linux.CoreProbe").write_bytes(b"core")
        artifacts=[]
        for kind,name in (("windows","wine_consumer.exe"),("hidapi","hidapi_windows_consumer.exe")):
            path=fixture.root/kind;(path/"bin").mkdir(parents=True);data=b"MZ"+kind.encode()
            (path/"bin"/name).write_bytes(data)
            (path/"provenance.txt").write_text("MadSense commit: commit\nCompilation: passed\n")
            (path/"binary-sha256.txt").write_text(hashlib.sha256(data).hexdigest()+"  "+name+"\n")
            artifacts.append(path)
        with patch("ge_umu_payload.load_lock",return_value=fixture.lock), \
             patch("ge_umu_payload.verify_bundle",side_effect=fixture.verify):
            metadata,hashes=stage_payload(fixture.payload,neutral,core,*artifacts,fixture.cache,fixture.output,"commit")
        self.assertFalse(metadata["geUmuPrivatePayload"]["runtimeCacheCollected"])
        self.assertFalse(fixture.output.exists())
        research=fixture.payload/"linux/research"
        command=("import pathlib,sys,unittest; p=pathlib.Path(sys.argv[1]);sys.path.insert(0,str(p));"
                 "import ge_umu_standalone as m;assert pathlib.Path(m.__file__).parent==p;"
                 "s=unittest.defaultTestLoader.loadTestsFromName('test_ge_umu_standalone.StandaloneTests');"
                 "assert s.countTestCases()==6;r=unittest.TextTestRunner().run(s);sys.exit(not r.wasSuccessful())")
        result=subprocess.run([sys.executable,"-I","-c",command,str(research)],cwd=fixture.root,capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)
        for name in ("linux/research/ge_umu_standalone.py","linux/research/run_ge_only_guest.sh",
                     "consumer-bin/hidapi_windows_consumer.exe","ge-umu-private-cache/archive.tar"):
            self.assertEqual(hashes[name],hashlib.sha256((fixture.payload/name).read_bytes()).hexdigest())


if __name__=="__main__":unittest.main()
