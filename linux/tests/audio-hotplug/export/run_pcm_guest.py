#!/usr/bin/env python3
"""Fresh private app/export PCM session, only after all four lifecycle cases."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
import time
import uuid
import wave

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import actor_files
from run_export_guest import MarkedActor, descriptor_evidence, verify_result as verify_export
from run_guest import verify_gates, save
from export_desktop import ExportDesktop
from export_policy import IgnoreRule, configuration, publication, snapshot_source, validate_node, alsa_node
from policy import predict_parents
from evidence import compare, metadata_changes, preservation
from pcm_evidence import marked_source, load_capture, verify_phase, verify_negotiation, digest, require
from pcm_node import read_node, set_raw, node_state, exact_node
from consumer_session import process_identity
from pipewire_gain import stream_binding, control_snapshot

PHASES=(('direct',(1,1,1,1)),('export',(1,1,1,1)),('front-gain',(2,4,1,1)),('restored',(1,1,1,1)))

class PcmActor(MarkedActor):
    def mark(self,label):
        self.commands+=1
        actor_files.call(self.actor,self.uid,self.gid,'command',dict(sequence=self.commands,operation='pcm-mark',value=label))
        self.state(lambda _:True)
        value=actor_files.call(self.actor,self.uid,self.gid,'read',dict(name='pcm-mark-'+label+'.json'))
        require(isinstance(value,dict),'App did not publish a PCM bookmark')
        return value

def playback(actor,audio,label,source,identity=None,wanted=None,negotiation=False):
    require(not negotiation or (label=='negotiate' and identity is not None and wanted is None), 'Invalid negotiation phase')
    row=dict(label=label,start=actor.mark(label+'-start'),binding=None)
    audio.result.setdefault('pcmNegotiations' if negotiation else 'pcmPlaybacks',[]).append(row);audio.save()
    command=(['aplay','-q','-D',f'hw:{audio.card},0',source] if identity is None else
        ['pw-play','--remote',audio.env['PIPEWIRE_REMOTE'],'--target',str(identity['serial']),
         '--channel-map','FL,FR,RL,RR',source])
    # Direct ALSA runs before any export exists; there is no competing adapter.
    require((audio.exporter is None)==(identity is None),'Direct PCM and export must not compete')
    # Export-owner and Props commands have their own retained logs. Keep every
    # player in a distinct namespace, including export and front-gain phases.
    child=audio.start('pcm-player-'+label,command);row.update(pid=child.pid,argv=list(map(str,command)),startedMonotonicNs=time.monotonic_ns())
    deadline=time.monotonic()+15;observed=None;failure=None
    try:
        peer=process_identity(child.pid)
        require(peer is not None and peer[0]==(audio.uid,)*4,'Player identity unavailable')
        row['processStart']=int(peer[1]);require(row['processStart']>0,'Invalid player start time')
        attempt=0
        while child.poll() is None:
            require(time.monotonic()<deadline,'PCM player deadline exceeded')
            require(process_identity(child.pid)==peer,'Player identity changed')
            attempt+=1;name=f'pcm-{label}-{attempt:02}'
            observation=audio.observe(name,True)
            require(all(observation['defaultPreserved'].values()),'PCM phase replaced a desktop default')
            if identity is not None:
                snapshot=json.loads((audio.output/(name+'.json')).read_text())
                if not negotiation:
                    state=node_state(snapshot,audio.plan,identity)
                    require(state['volumeProps']['channelVolumes']==wanted['raw']
                        and state['volumeProps']['softVolumes']==wanted['soft'],'Node gains changed during PCM')
                if child.poll() is None:
                    require(process_identity(child.pid)==peer,'Player identity changed during graph snapshot')
                    binding=stream_binding(snapshot,child.pid,audio.uid,audio.gid,{'identity':{'nodeId':identity['id']}})
                    if binding is not None:observed=binding;row['bindingSnapshot']=name
            time.sleep(.05)
        require(child.returncode==0 and (identity is None or observed is not None),'PCM playback or four-channel binding failed')
    except BaseException as error:failure=error
    finally:
        try:
            if child.poll() is None:
                child.terminate()
                try:child.wait(timeout=2)
                except BaseException:child.kill();child.wait(timeout=2)
        except BaseException as error:
            row['playerCleanupError']=str(error)
            if failure is None:failure=error
        row.update(exitCode=child.returncode,binding=observed,endedMonotonicNs=time.monotonic_ns(),error=str(failure) if failure else None)
        # Retain the drain tail inside the bookmark window; compare only the
        # complete marked source, with one common alignment for all channels.
        try:time.sleep(.3);row['end']=actor.mark(label+'-end')
        except BaseException as error:
            row['endBookmarkError']=str(error)
            if failure is None:failure=error
        row['error']=str(failure) if failure else None
        audio.save()
    if failure is not None:raise failure
    return row

def audit(directory,token,gates,export_hash):
    directory=Path(directory);value=json.loads((directory/'result.json').read_text())
    require(value['ownedGuestToken']==token and value['functionalGates']==gates
        and value['lifecycleResultSha256']==export_hash and value['productionBinary'] is False
        and value['scope']=='private-export-quad-ingress' and value['result']=='measured','PCM scope/prerequisite/result differs')
    audio=json.loads((directory/'audio/result.json').read_text());rule=json.loads((directory/'rule/ignore-rule.json').read_text())
    require(value['audioResultSha256']==digest((directory/'audio/result.json').read_bytes())
        and value['captureManifestSha256']==digest((directory/'actor/app/pcm-ingress.json').read_bytes()),'PCM result attribution differs')
    require(audio['ownedGuestToken']==token and audio['accountProcessesAndFilesRemoved'] and not audio.get('cleanupErrors')
        and not audio.get('exportCleanupError') and not audio.get('retirementError') and audio['noPhysicalAccessGranted'],
        'PCM audio ownership/cleanup differs')
    plan=value['plan'];content=configuration(plan['targetParent'],plan['ruleNonce']).encode()
    require(plan['matched'] and plan['targetParent']==plan['actualParent'] and plan['nonce']==plan['ruleNonce']
        and rule['plan']==plan and (directory/'rule/ignore.rules').read_bytes()==content and rule['sha256']==digest(content)
        and rule['removed'] and rule['reloadAfterRemoval'] and rule['reloadAcknowledgedNs']<audio['graphStartedMonotonicNs'],
        'PCM preappearance rule or restoration differs')
    require(json.loads((directory/'actor/actor-cleanup.json').read_text())==value['actorCleanup']
        and value['actorCleanup']['confirmed'] and json.loads((directory/'actor/app/stopped.json').read_text())['cleanupConfirmed']
        and json.loads((directory/'actor/fixture-before.json').read_text())==json.loads((directory/'actor/fixture-restored.json').read_text()),
        'PCM actor/fixture cleanup missing')
    sources=audio['snapshotSources']
    for label,record in sources.items():
        require(label and all(c.isalnum() or c=='-' for c in label),'Invalid snapshot label')
        require(record==snapshot_source((directory/'audio'/(label+'-pw-dump.stdout')).read_bytes(),
            (directory/'audio'/(label+'.json')).read_bytes()),'PCM graph raw attribution differs')
    for observation in audio['observations']:
        require(observation['label'] in sources,'PCM observation has no attributed raw source')
        snapshot=json.loads((directory/'audio'/(observation['label']+'.json')).read_text())
        require(all(observation.get(k)==v for k,v in compare(snapshot,audio['controlNodes']).items()),'PCM defaults differ from snapshot')
        nodes=[validate_node(row,plan,require_format=True) for row in snapshot if row.get('type')=='PipeWire:Interface:Node'
            and alsa_node(row.get('info',{}).get('props',{}))]
        require(len(nodes)==int(observation['expectedExport']),'PCM snapshot has an unexpected export')
        if nodes:
            require(observation['export']==nodes[0] and len(audio['exports'])==1,'PCM snapshot export differs')
            clients=[row for row in snapshot if row.get('type')=='PipeWire:Interface:Client' and row['id']==nodes[0]['clientId']]
            require(len(clients)==1 and str(clients[0]['info']['props'].get('application.process.id'))==str(audio['exports'][0]['pid']),
                'PCM node belongs to another client')
    changes=metadata_changes((directory/'audio/metadata-monitor.stdout').read_text(),audio['controlNodes'])
    preserved=preservation(audio['observations'],changes)
    require(changes==audio['metadataEvents'] and preserved==audio['preservationByKind'] and all(preserved.values()),'PCM defaults changed')
    observed=publication((directory/'audio/graph-monitor.stdout').read_text(),plan,1)
    require(len(audio['arrivals'])==len(audio['retiredSoundEpochs'])==1 and audio['retiredSoundEpochs'][0]['nodesDestroyed']
        and audio['arrivals'][0]['usbParent']==plan['actualParent'],'PCM USB/ACL lifecycle differs')
    arrival=audio['arrivals'][0];retired=audio['retiredSoundEpochs'][0]
    require(audio['graphStartedMonotonicNs']<arrival['attachStartedNs']<=arrival['applicationReadyObservedNs']
        <=arrival['aclStartedNs']<=arrival['aclFinishedNs']<retired['retiredMonotonicNs']<rule['removedMonotonicNs']
        and arrival['udevProperties'].get('ACP_IGNORE')=='1' and arrival['udevProperties'].get('PULSE_IGNORE')=='1'
        and arrival['udevProperties'].get('MADSENSE_AUDIO_LEASE')==plan['nonce'],'PCM node was not ignored before access')
    reference=json.loads((directory/'descriptor-reference.json').read_text())
    expected=bytearray.fromhex(reference['device']+reference['config']);expected[35]=len(reference['strings'])+1
    require((directory/'audio/usb-descriptors-1.bin').read_bytes()==expected
        and (directory/'audio/hid-descriptor-1.bin').read_bytes()==bytes.fromhex(reference['hid'])
        and arrival['identity']['usbSerial'] is None,'PCM source copy changed unrelated descriptors')
    capture,raw=load_capture(directory/'actor/app');source=(directory/'source.pcm').read_bytes()
    require(digest(source)==value['sourceSha256'] and len(source)==48000*8
        and source==marked_source(value['sourceSeed']),'PCM source differs')
    with wave.open(str(directory/'source.wav'),'rb') as stream:
        require((stream.getnchannels(),stream.getsampwidth(),stream.getframerate())==(4,2,48000)
            and stream.readframes(stream.getnframes())==bytes(12000*8)+source+bytes(12000*8),'WAV is not the exact marked quad source')
    require(len(audio['exports'])==1 and audio['exports'][0]['stopKind']=='owner-eof' and audio['exports'][0]['exitCode']==0,'PCM exporter lifetime differs')
    identity=audio['exports'][0]['nodeIdentity']
    precise_reads={}
    for row in audio['pcmNodeReads']:
        label=row['label'];require(label not in precise_reads and label+'-before' in sources and label+'-after' in sources,'Precise node read label differs')
        allow=label=='pcm-pre-negotiation'
        require(row['allowUnnegotiated'] is allow,'Unexpected unnegotiated volume read')
        before=node_state(json.loads((directory/'audio'/(label+'-before.json')).read_text()),plan,identity,allow)
        after=node_state(json.loads((directory/'audio'/(label+'-after.json')).read_text()),plan,identity,allow)
        precise=exact_node(json.loads((directory/'audio'/(label+'.stdout')).read_text()),before)
        require(before==after==row['state'] and precise==row['exact'],'Precise node state differs from raw evidence')
        precise_reads[label]=precise
    require(audio['pcmOriginalExact']==precise_reads['pcm-original'] and audio['pcmRestoredExact']==precise_reads['pcm-restore-confirmed']
        and audio['pcmOriginalExact']['raw']==audio['pcmOriginalExact']['soft']==[1,1,1,1],'PCM unity baseline/restoration differs')
    pre=precise_reads['pcm-pre-negotiation']
    require(audio['pcmPreNegotiationExact']==pre and pre['raw'] in ([1,1],[1,1,1,1])
        and pre['soft']==[1,1,1,1], 'Pre-negotiation state was changed or normalized')
    with wave.open(str(directory/'negotiation.wav'),'rb') as stream:
        require((stream.getnchannels(),stream.getsampwidth(),stream.getframerate(),stream.getnframes())==(4,2,48000,48000)
            and stream.readframes(48000)==bytes(48000*8),'Negotiation source is not exact quad silence')
    require([row['label'] for row in audio['pcmNodeRequests']]==['pcm-front-gain','pcm-restore']
        and [row['wanted'] for row in audio['pcmNodeRequests']]==[[.5,.25,1,1],[1,1,1,1]],'PCM gain request contract differs')
    phases=audio['pcmPlaybacks'];require([row['label'] for row in phases]==[name for name,_ in PHASES],'Missing PCM phases')
    negotiation=audio['pcmNegotiations'];require(len(negotiation)==1 and negotiation[0]['label']=='negotiate','Missing separate negotiation control')
    compared=[]
    silence=None
    for row,(name,divisors) in zip(negotiation+phases,(('negotiate',None),)+PHASES):
        require(row['exitCode']==0 and row['error'] is None,'PCM player did not finish cleanly')
        require(type(row['processStart']) is int and row['processStart']>0,'Player start time missing')
        for side in ('start','end'):
            require(row[side]==json.loads((directory/'actor/app'/f'pcm-mark-{name}-{side}.json').read_text()),'PCM bookmark differs from actor')
        if name=='direct':require(row['binding'] is None and row['argv'][:4]==['aplay','-q','-D',f"hw:{arrival['card']},0"],'Direct control differs')
        else:
            require(isinstance(row['binding'],dict) and row['bindingSnapshot'] in sources and type(row['processStart']) is int and row['processStart']>0,'Player binding lacks identity evidence')
            snapshot=json.loads((directory/'audio'/(row['bindingSnapshot']+'.json')).read_text())
            require(row['binding']==stream_binding(snapshot,row['pid'],audio['account']['uid'],audio['account']['gid'],
                {'identity':{'nodeId':identity['id']}}),'PCM four-channel player binding differs from graph')
        if name=='negotiate':silence=verify_negotiation(capture,raw,row['start'],row['end'])
        else:compared.append(dict(label=name,**verify_phase(capture,raw,source,row['start'],row['end'],divisors)))
    require(phases[0]['end']['timestamp']<negotiation[0]['start']['timestamp']<negotiation[0]['end']['timestamp']
        <phases[1]['start']['timestamp'],'Negotiation did not precede the marked PipeWire phases')
    require(audio['pcmPropsRestored'] and audio['pcmOriginalExact']==audio['pcmRestoredExact']
        and audio['pcmAlsaBefore']==audio['pcmAlsaAfter']
        and audio['pcmAlsaBefore']==control_snapshot((directory/'audio/pcm-alsa-before.stdout').read_text())
        and audio['pcmAlsaAfter']==control_snapshot((directory/'audio/pcm-alsa-after.stdout').read_text()),'Node Props or ALSA controls were not restored')
    return dict(phases=compared,negotiationControl=silence,publication=observed,defaultsPreserved=preserved,cleanCapture=True,
        exactFourChannelIngress=True,transmissionValidated=False,physicalAudioValidated=False)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('functional-results','pcm-probe-directory','export-client','descriptor-reference','output'):
        parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--owned-guest-token',required=True);args=parser.parse_args()
    from ci_environment import validate_environment
    require(os.geteuid()==0 and validate_environment(args.owned_guest_token)['kind']=='owned-qemu-guest','Owned root VM required')
    gates=verify_gates(args.functional_results,args.owned_guest_token)
    verify_export(args.functional_results/'audio-export',args.owned_guest_token,gates)
    export_hash=digest((args.functional_results/'audio-export/result.json').read_bytes())
    output=args.output.resolve();output.mkdir()
    parents,inventory=predict_parents();nonce=uuid.uuid4().hex
    plan=dict(actualParent=parents[0],targetParent=parents[0],nonce=nonce,ruleNonce=nonce,matched=True,vhciBefore=inventory)
    result=dict(schema=1,scope='private-export-quad-ingress',result='invalid',ownedGuestToken=args.owned_guest_token,
        functionalGates=gates,lifecycleResultSha256=export_hash,plan=plan,productionBinary=False,physicalAudioValidated=False)
    actor=audio=None;rule=IgnoreRule(plan,output/'rule')
    result['sourceSeed']=secrets.randbits(64)
    source=marked_source(result['sourceSeed']);(output/'source.pcm').write_bytes(source);result['sourceSha256']=digest(source)
    with wave.open(str(output/'source.wav'),'wb') as stream:
        stream.setnchannels(4);stream.setsampwidth(2);stream.setframerate(48000)
        stream.writeframes(bytes(12000*8)+source+bytes(12000*8))
    with wave.open(str(output/'negotiation.wav'),'wb') as stream:
        stream.setnchannels(4);stream.setsampwidth(2);stream.setframerate(48000);stream.writeframes(bytes(48000*8))
    reference=json.loads(args.descriptor_reference.read_text());save(output/'descriptor-reference.json',reference)
    try:
        with rule:
            with PcmActor(args.owned_guest_token,args.pcm_probe_directory,output/'actor',nonce=nonce) as actor:
                with ExportDesktop(args.owned_guest_token,actor.fixture.parent,output/'audio',plan=plan,executable=args.export_client) as audio:
                    audio.result['exportPlan']=plan;audio.start_graph(False)
                    started=time.monotonic_ns();parent,status=actor.resume();ready=time.monotonic_ns();audio.bind_sound(parent,1)
                    audio.result['arrivals'][-1].update(attachStartedNs=started,applicationReadyObservedNs=ready,identity=actor.last_identity)
                    hid=(Path(actor.last_identity['hidrawSysfs'])/'device/report_descriptor').read_bytes()
                    descriptor_evidence(audio.output,1,reference,(parent/'descriptors').read_bytes(),hid)
                    audio.settle('ignored',True)
                    _,control=audio.command('pcm-alsa-before',['amixer','--card',str(audio.card),'contents'])
                    audio.result['pcmAlsaBefore']=control_snapshot(control.decode())
                    staged=audio.stage(output/'source.wav');playback(actor,audio,'direct',staged)
                    audio.start_export('pcm-export');audio.settle('export-ready',True)
                    identity=audio.exports[-1]['nodeIdentity']
                    _,pre=read_node(audio,identity,'pcm-pre-negotiation',allow_unnegotiated=True)
                    require(pre['raw'] in ([1,1],[1,1,1,1]) and pre['soft']==[1,1,1,1],
                        'Preserve unexpected pre-negotiation gains without rewriting them')
                    audio.result['pcmPreNegotiationExact']=pre;audio.save()
                    playback(actor,audio,'negotiate',audio.stage(output/'negotiation.wav'),identity,negotiation=True)
                    original,precise=read_node(audio,identity,'pcm-original')
                    require(precise['raw']==precise['soft']==[1,1,1,1],'First PCM control requires observed exact unity; preserve other baseline')
                    audio.result['pcmOriginalExact']=precise;audio.result['pcmPropsRestored']=False;audio.save()
                    primary=None
                    try:
                        playback(actor,audio,'export',staged,identity,precise)
                        applied=set_raw(audio,identity,precise,[precise['raw']],'pcm-front-gain',[.5,.25,1.,1.])
                        playback(actor,audio,'front-gain',staged,identity,applied)
                    except BaseException as error:primary=error
                    finally:
                        try:
                            restored=set_raw(audio,identity,precise,[precise['raw'],[.5,.25,1.,1.]],'pcm-restore',precise['raw'])
                            after,_=read_node(audio,identity,'pcm-restore-final')
                            require(after==original,'Visible Node Props changed after restore')
                            audio.result.update(pcmPropsRestored=True,pcmRestoredExact=restored);audio.save()
                        except BaseException as error:
                            audio.result['pcmRestoreError']=str(error);audio.save()
                            if primary is None:primary=error
                    if primary is not None:raise primary
                    playback(actor,audio,'restored',staged,identity,precise)
                    _,control=audio.command('pcm-alsa-after',['amixer','--card',str(audio.card),'contents'])
                    audio.result['pcmAlsaAfter']=control_snapshot(control.decode());audio.save()
                    audio.stop_export();audio.settle('export-closed',True)
                    actor.pause();audio.retire_removed_sound();audio.settle('detached',False);audio.finish_observation()
                require(audio.result['accountProcessesAndFilesRemoved'] and not audio.result.get('cleanupErrors'),'PCM audio cleanup failed')
            require(actor.cleanup['confirmed'],'PCM actor cleanup failed')
        result.update(actorCleanup=actor.cleanup,result='measured',audioResultSha256=digest((output/'audio/result.json').read_bytes()),
            captureManifestSha256=digest((output/'actor/app/pcm-ingress.json').read_bytes()));save(output/'result.json',result)
        result['audit']=audit(output,args.owned_guest_token,gates,export_hash)
    except BaseException as error:result.update(result='invalid',error=f'{type(error).__name__}: {error}')
    finally:
        if actor is not None:result['actorCleanup']=actor.cleanup
        save(output/'result.json',result)
    return 0 if result['result']=='measured' else 1

if __name__=='__main__':raise SystemExit(main())
