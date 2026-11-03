"""Read-only mixer evidence, including the kernel's current playback channel map."""
import json
from pathlib import Path
import re
import stat

from pcm_evidence import require
from pipewire_gain import control_snapshot

READ_PROGRAM = r'''import json,os,pathlib,stat,sys,time
card=int(sys.argv[1]); parent=pathlib.Path(sys.argv[2]); card_sysfs=pathlib.Path(sys.argv[3])
node=pathlib.Path('/dev/snd/controlC'+str(card)); value=node.lstat()
assert os.geteuid()>0 and stat.S_ISCHR(value.st_mode) and value.st_uid==0
resolved=pathlib.Path('/sys/class/sound/'+node.name).resolve(strict=True)
assert parent.resolve(strict=True)==parent and card_sysfs.resolve(strict=True)==card_sysfs
assert resolved.parent==card_sysfs and card_sysfs.is_relative_to(parent)
assert pathlib.Path('/sys/dev/char/'+str(os.major(value.st_rdev))+':'+str(os.minor(value.st_rdev))).resolve(strict=True)==resolved
identity=dict(card=card,cardSysfs=str(card_sysfs),usbParent=str(parent),path=str(node),
    dev=value.st_dev,inode=value.st_ino,rdev=value.st_rdev,uid=value.st_uid,gid=value.st_gid)
start=int(pathlib.Path('/proc/self/stat').read_text().rsplit(')',1)[1].split()[19])
print(json.dumps(dict(schema=1,readerUid=list(os.getresuid()),readerGid=list(os.getresgid()),
    groups=os.getgroups(),pid=os.getpid(),processStart=start,monotonicNs=time.monotonic_ns(),identity=identity)),flush=True)
os.execv('/usr/bin/amixer',['amixer','--card',str(card),'contents'])
'''

def parse_capture(raw,identity,uid,gid):
    first,separator,body=raw.partition(b'\n');require(separator,'ALSA reader identity missing')
    reader=json.loads(first)
    require(reader.get('schema')==1 and reader.get('identity')==identity and uid>0 and gid>0
        and reader.get('readerUid')==[uid]*3 and reader.get('readerGid')==[gid]*3 and reader.get('groups')==[],
        'ALSA reader or device identity differs')
    require(all(type(reader.get(key)) is int and reader[key]>0 for key in ('pid','processStart','monotonicNs')),
        'ALSA reader lifetime identity missing')
    # Canonical JSON keys; shared control_snapshot intentionally uses integer numids.
    controls={str(key):value for key,value in control_snapshot(body.decode()).items()}
    return dict(reader=reader,controls=controls)

def expected_identity(arrival,grant):
    require(grant['path']==f"/dev/snd/controlC{arrival['card']}" and grant['uid']==0,'Exact root-owned card control required')
    return dict(card=arrival['card'],cardSysfs=arrival['cardSysfs'],usbParent=arrival['usbParent'],
        **{key:grant[key] for key in ('path','dev','inode','rdev','uid','gid')})

def node_identity(value):
    return dict(dev=value.st_dev,inode=value.st_ino,rdev=value.st_rdev,uid=value.st_uid,gid=value.st_gid)

def capture(session,label):
    session.check_device();arrival=session.result['arrivals'][-1]
    grants=[row for row in session.grants if row['path']==f'/dev/snd/controlC{session.card}']
    require(len(grants)==1,'One attributed ALSA control grant required')
    identity=expected_identity(arrival,grants[0]);node=Path(identity['path'])
    def same_node():
        value=node.lstat()
        require(stat.S_ISCHR(value.st_mode) and all(observed==identity[key]
            for key,observed in node_identity(value).items()), 'ALSA control node replaced')
    same_node()
    command,raw=session.command(label,['python3','-c',READ_PROGRAM,str(session.card),session.parent,session.card_sysfs])
    same_node();value=parse_capture(raw,identity,session.uid,session.gid)
    require(command['startedMonotonicNs']<=value['reader']['monotonicNs']<=command['endedMonotonicNs'],'ALSA reader clock differs')
    session.result.setdefault('pcmAlsaReads',[]).append(dict(label=label,**value));session.save()
    return value['controls']

def verify_controls(initial,negotiated,after,after_eof):
    require(set(initial)==set(negotiated)==set(after)==set(after_eof),'ALSA control inventory changed')
    matching=[key for key,row in initial.items() if re.fullmatch(r"numid=\d+,iface=PCM,name='Playback Channel Map'",row['metadata'][0])]
    require(len(matching)==1,'One exact playback channel-map control required')
    channel=matching[0];row=initial[channel]
    require(row['writable'] is False and len(row['metadata'])==4
        and re.fullmatch(r'  ; type=INTEGER,access=r----R--,values=4,min=0,max=\d+,step=0',row['metadata'][1])
        and row['metadata'][2:]==['  | container','    | chmap-fixed=FL,FR,RL,RR'],
        'Unexpected playback map type/access/count/positions')
    require(row['values']=='0,0,0,0' and negotiated[channel]==dict(row,values='3,4,5,6'),
        'Unexpected current-format channel-map transition')
    require(all(initial[key]==negotiated[key] for key in initial if key!=channel),
        'An ALSA control other than the current playback map changed')
    require(negotiated==after,'ALSA controls changed during the marked PCM/gain phases')
    require(initial==after_eof,'ALSA controls did not return to the unopened state after owner EOF')
    return dict(channelMapControl=channel,initialMap=row['values'],negotiatedMap=negotiated[channel]['values'],
        writableControlsUnchanged=True,otherReadOnlyControlsUnchanged=True,negotiatedControlsPreserved=True,
        initialControlsRestoredAfterEof=True)
