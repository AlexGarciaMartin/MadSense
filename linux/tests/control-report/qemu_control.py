#!/usr/bin/env python3
"""Independent opt-in VM for class-report framing; production dispatch unchanged."""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
sys.path[:0]=[str(ROOT/'linux/research'),str(ROOT/'linux/platform/tests'),str(ROOT/'linux/platform/integration'),
              str(ROOT/'linux/tests/app-integration')]
from qemu_broker import prepare_payload, verify_result as verify_broker
from qemu_core import verify_core_result
from stage_control import RUNNER, HELPER, edits, transform, verify_overlay
from control_evidence import CASES, final_capture, observe
from consumer_session import read_rows


def read(path):return json.loads(Path(path).read_text())
def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_build(directory,commit):
    directory=Path(directory).resolve();manifest=read(directory/'source-overlay.json')
    if manifest.get('sourceCommit')!=commit:raise RuntimeError('Control source copy differs from current commit')
    originals={name:subprocess.check_output(['git','show',f'{commit}:{name}'],cwd=ROOT) for name in edits()}
    helper=subprocess.check_output(['git','show',f'{commit}:{HELPER}'],cwd=ROOT)
    verify_overlay(manifest,originals,helper)
    archive=subprocess.check_output(['git','archive','--format=tar',commit],cwd=ROOT)
    if hashlib.sha256(archive).hexdigest()!=manifest['sourceArchiveSha256']:
        raise RuntimeError('Control source archive differs from committed tree')
    expected={path.relative_to(directory).as_posix() for base in ('core-probe','consumer-bin')
              for path in (directory/base).rglob('*') if path.is_file()}
    recorded={}
    for line in (directory/'binary-sha256.txt').read_text().splitlines():
        value,name=line.split('  ',1)
        if name in recorded or name not in expected or digest(directory/name)!=value:
            raise RuntimeError('Control executable manifest mismatch')
        recorded[name]=value
    if set(recorded)!=expected or not {'consumer-bin/control_probe','core-probe/MadSense.Linux.CoreProbe'}<=expected:
        raise RuntimeError('Control executable manifest is incomplete')
    return manifest,recorded,originals


def payload_stage(payload,neutral,core,artifact,commit):
    manifest,binaries,originals=verify_build(artifact,commit)
    prepare_payload(payload,neutral)
    shutil.copytree(core,payload/'core-probe')
    shutil.copytree(artifact/'core-probe',payload/'control-core-probe')
    shutil.copytree(artifact/'consumer-bin',payload/'consumer-bin')
    shutil.copytree(HERE,payload/'linux/tests/control-report',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    # Needed only because the original core verifier imports the app verifier;
    # this experiment never runs an app/UI or a desktop-profile observer.
    shutil.copytree(ROOT/'linux/tests/app-integration',payload/'linux/tests/app-integration',
                    ignore=shutil.ignore_patterns('__pycache__','*.pyc','bin','obj'))
    research=ROOT/'linux/research'
    for name in ('host_probe.c','consumer_session.py','run_consumers.py','consumer_guard.h',
                 'prepare_debian_kernel.sh','run_debian_broker_guest.sh','qemu_kernel.py','kvm_acceleration.py'):
        shutil.copy2(research/name,payload/'linux/research'/name)
    (payload/'linux/platform/integration/run_control_guest.py').write_text(
        transform(RUNNER,originals[RUNNER].decode().replace('\r\n','\n')),encoding='utf8',newline='\n')
    hashes={path.relative_to(payload).as_posix():digest(path) for path in sorted(payload.rglob('*'))
            if path.is_file() and path.name!='PAYLOAD-SHA256SUMS'}
    (payload/'PAYLOAD-SHA256SUMS').write_text(''.join(f'{value}  {name}\n' for name,value in hashes.items()))
    return dict(sourceOverlay=manifest,controlBinaries=binaries,payloadHashes=hashes)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-control-experiment',action='store_true')
    parser.add_argument('--guest-image',choices=('ubuntu','debian13'),default='debian13')
    for name in ('neutral-probe-directory','core-probe-directory','control-artifact','output','work'):
        parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args()
    if not args.run_control_experiment:parser.error('Explicit isolated control experiment required')
    if args.guest_image=='debian13':
        from debian_qemu import OwnedDebianQemu
        guest=OwnedDebianQemu(args.output,args.work)
    else:
        from owned_qemu import OwnedQemu
        guest=OwnedQemu(args.output,args.work)
    p=guest.provenance
    p.update(experiment='source-copy-hid-class-framing',productionBinary=False,dispatchModified=False,
             latencyValidated=False,physicalDevices=False,driverDetachAttempted=False,guestImageSelection=args.guest_image)
    try:
        commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
        p['sourceCommit']=commit
        payload=guest.work/'payload'
        p.update(payload_stage(payload,args.neutral_probe_directory.resolve(),args.core_probe_directory.resolve(),
                               args.control_artifact.resolve(),commit))
        guest.boot();guest.upload(payload)
        guest.run('payload-check','cd /home/ubuntu/madsense && sha256sum --check --strict PAYLOAD-SHA256SUMS')
        guest.run('probe-permissions','chmod +x /home/ubuntu/madsense/probe/MadSense.Linux.Probe')
        try:
            script=('linux/research/run_debian_broker_guest.sh' if args.guest_image=='debian13'
                    else 'linux/platform/tests/run_broker_experiment.sh')
            baseline=guest.run('baseline-run','bash /home/ubuntu/madsense/'+script+' '+shlex.quote(guest.token),
                               timeout=900,required=False)
            p['baselineExit']=baseline.returncode
            if baseline.returncode==0:
                response=guest.run('control-run','bash /home/ubuntu/madsense/linux/tests/control-report/run_control_guest.sh '
                                   +shlex.quote(guest.token),timeout=600,required=False)
                p['controlExit']=response.returncode
        finally:
            for name in ('prepare.log','usbip-binary.txt','result'):guest.collect(name)
        result=guest.output/'result'
        verify_broker(read(result/'result.json'),guest.token)
        verify_core_result(read(result/'core-experiment/result.json'),guest.token)
        experiment=result/'control-experiment'
        verify_core_result(read(experiment/'result.json'),guest.token)
        ready=read(experiment/'consumer-ready.json');stopped=read(experiment/'core/stopped.json')
        baseline_ready=read(result/'core-experiment/consumer-ready.json')
        if ready.get('actorPid')==baseline_ready.get('actorPid'):
            raise RuntimeError('Control observation did not use a fresh second core actor')
        final_capture(stopped)
        observed=read(experiment/'consumer-suite/result.json')
        if (observed.get('scope')!='owned-source-copy-control-observation'
                or observed.get('ready')!=ready or observed.get('observationComplete') is not True
                or observed.get('accountProcessesAndFilesRemoved') is not True or observed.get('cleanupErrors')
                or not observed.get('cleanup') or not all(row.get('aclRestored') is True for row in observed['cleanup'])):
            raise RuntimeError('Control session did not retain complete observations and cleanup')
        cases=observed.get('controlCases',[])
        if [case.get('case') for case in cases]!=list(CASES):
            raise RuntimeError('Control observation did not retain the four fixed DualSense cases')
        controls=read_rows(experiment/'core/hid-control-trace.jsonl')
        raw=read_rows(experiment/'core/events.jsonl');bt=read_rows(experiment/'uhid-events.jsonl')
        replay=[]
        for case in cases:
            independent=observe(case['api'],case['observationStartedMonotonicNs'],case['observationEndedMonotonicNs'],controls,raw,bt)
            if any(case.get(key)!=value for key,value in independent.items()):
                raise RuntimeError('Control result differs from independent retained-capture replay')
            replay.append(independent)
        compatibility=all(case['behaviorMatchesExpected'] is True for case in replay)
        if observed.get('protocolCompatibilityPassed') is not compatibility:
            raise RuntimeError('Control compatibility summary differs from retained evidence')
        (guest.output/'control-independent-replay.json').write_text(json.dumps(replay,indent=2)+'\n')
        p.update(observationComplete=True,protocolCompatibilityPassed=observed['protocolCompatibilityPassed'],
                 controlResultSha256=digest(experiment/'consumer-suite/result.json'))
        if p.get('controlExit')!=0 or not p['protocolCompatibilityPassed']:
            raise RuntimeError('Control framing observation completed with a retained protocol mismatch or kernel refusal')
        p['result']='passed'
    except BaseException as error:p['reason']=f'{type(error).__name__}: {error}'
    finally:
        try:guest.close()
        except BaseException as error:p.update(result='failed',cleanupError=str(error))
        (guest.output/'provenance.json').write_text(json.dumps(p,indent=2)+'\n')
        print(json.dumps(p),flush=True)
    return 0 if p['result']=='passed' and p['ownedQemuStopped'] else 1


if __name__=='__main__':raise SystemExit(main())
