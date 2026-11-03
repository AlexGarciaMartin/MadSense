"""Fresh ordinary account for fixed reports; invoked only by the copied runner."""
import hashlib
import json
import os
from pathlib import Path
import stat
import time

from consumer_session import ConsumerSession, bounded_bytes, read_rows, require
from control_evidence import CASES, observe


def usbfs_node(parent):
    parent=Path(parent).resolve()
    require(str(parent).startswith('/sys/devices/platform/vhci_hcd.'), 'Owned virtual USB parent required')
    bus=int((parent/'busnum').read_text());device=int((parent/'devnum').read_text())
    require(1<=bus<=255 and 1<=device<=127, 'Invalid virtual USB bus/device')
    path=Path(f'/dev/bus/usb/{bus:03d}/{device:03d}');info=path.lstat()
    require(stat.S_ISCHR(info.st_mode) and info.st_uid==0 and os.major(info.st_rdev)==189
            and os.minor(info.st_rdev)==(bus-1)*128+device-1,
            'USBFS node identity differs from the virtual USB device')
    require(Path(f'/sys/dev/char/{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}').resolve()==parent,
            'USBFS node does not resolve to the exact VHCI device')
    return path


def run_suite(ready_path,binaries,wine_exe,fixture_state_callback,evidence,physical_parent,**options):
    require(wine_exe is None and all(value is None or value is False for value in options.values()),
            'Control observation cannot be combined with other consumer experiments')
    with ConsumerSession(ready_path,evidence,physical_parent) as session:
        session.result.update(scope='owned-source-copy-control-observation',productionBinary=False,
                              observationComplete=False,protocolCompatibilityPassed=False)
        require(session.ready['rawOutputObservationEnabled'] is True, 'Raw output observer required')
        core=Path(session.ready['coreOutputDirectory'])
        core_ready=json.loads(bounded_bytes(core/'ready.json'))
        require(core_ready.get('ownedGuestToken')==session.ready['ownedGuestToken']
                and core_ready.get('pid')==session.ready['actorPid']
                and core_ready.get('timestampFrequency')==1_000_000_000,
                'Control actor identity/clock differs from consumer ready')
        fixture_state_callback('neutral')
        usbfs=usbfs_node(session.parent)
        session.check_device()
        # Record pre-existing read permission on USB root hubs. Do not grant
        # anything outside the target or mistake existing read permission for
        # permission added by this experiment.
        all_usbfs=sorted(str(path) for path in Path('/dev/bus/usb').glob('*/*'))
        code='import os,json,sys;print(json.dumps({p:[os.access(p,os.R_OK),os.access(p,os.W_OK)] for p in sys.argv[1:]}))'
        _,output=session.command('usbfs-access-before',['python3','-c',code,*all_usbfs])
        before=json.loads(output)
        session.grant(usbfs)
        _,output=session.command('usbfs-access-after',['python3','-c',code,*all_usbfs])
        access=json.loads(output)
        require(set(access)==set(before) and str(usbfs) in access
                and all(value==[True,True] if path==str(usbfs) else value==before[path] and not value[1]
                        for path,value in access.items()),
                'Consumer has unexpected USBFS access')
        session.result['usbfsAccessAudit']=dict(before=before,after=access,onlyGrantedPath=str(usbfs))
        binary=session.stage(Path(binaries)/'control_probe')
        session.result['probeSha256']=hashlib.sha256(binary.read_bytes()).hexdigest()
        session.result['controlCases']=[]
        cases=CASES+(('feature-edge',) if session.ready['productId']==0x0df2 else ())
        for case in cases:
            session.check_device();require(usbfs_node(session.parent)==usbfs,'Virtual USBFS node changed')
            row,output=session.command(case,[binary,session.ready['ownedGuestToken'],session.parent,
                session.ready['evdevPath'],session.ready['hidrawPath'],usbfs,session.ready['virtualMacHex'],case],
                timeout=15,required=False)
            api=json.loads(output)
            # The USB ACK can precede physical dispatch. This isolated window
            # includes a bounded drain interval before the next fixed marker.
            time.sleep(.5);end=time.monotonic_ns()
            observed=observe(api,row['startedMonotonicNs'],end,
                read_rows(core/'hid-control-trace.jsonl'),read_rows(core/'events.jsonl'),
                read_rows(Path(session.ready['uhidEventsPath'])))
            observed.update(observationStartedMonotonicNs=row['startedMonotonicNs'],observationEndedMonotonicNs=end,
                            api=api,apiExitCode=row['exitCode'])
            session.result['controlCases'].append(observed);session.save()
        fixture_state_callback('neutral')
        session.result.update(result='passed',observationComplete=True,
            protocolCompatibilityPassed=all(row['behaviorMatchesExpected'] is True for row in session.result['controlCases']))
        session.save()
    return session.result
