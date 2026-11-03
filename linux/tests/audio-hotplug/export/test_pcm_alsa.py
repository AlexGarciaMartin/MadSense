import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from pcm_alsa import parse_capture, verify_controls, node_identity

TEXT="""numid=1,iface=PCM,name='Playback Channel Map'
  ; type=INTEGER,access=r----R--,values=4,min=0,max=36,step=0
  : values=0,0,0,0
  | container
    | chmap-fixed=FL,FR,RL,RR
numid=2,iface=MIXER,name='PCM Playback Volume'
  ; type=INTEGER,access=rw---R--,values=1,min=0,max=100,step=0
  : values=80
  | dBminmax-min=-100.00dB,max=0.00dB
numid=3,iface=CARD,name='Headphone Jack'
  ; type=BOOLEAN,access=r-------,values=1
  : values=off
"""
IDENTITY=dict(card=0,cardSysfs='/sys/devices/platform/vhci_hcd.0/usb1/1-1/1-1:1.0/sound/card0',
    usbParent='/sys/devices/platform/vhci_hcd.0/usb1/1-1',path='/dev/snd/controlC0',dev=1,inode=2,rdev=3,uid=0,gid=29)
READER=dict(schema=1,readerUid=[1001]*3,readerGid=[1002]*3,groups=[],pid=100,processStart=200,monotonicNs=300,identity=IDENTITY)

def raw(reader=READER,text=TEXT):return (json.dumps(reader)+'\n'+text).encode()
def controls():return parse_capture(raw(),IDENTITY,1001,1002)['controls']
def negotiated():
    value=controls();value['1']['values']='3,4,5,6';return value

class AlsaStateTests(unittest.TestCase):
    def test_readonly_map_transition_and_exact_return_are_separate_from_gains(self):
        value=verify_controls(controls(),negotiated(),negotiated(),controls())
        self.assertTrue(value['writableControlsUnchanged'])
        self.assertTrue(value['initialControlsRestoredAfterEof'])
        self.assertEqual(value['negotiatedMap'],'3,4,5,6')

    def test_every_other_value_and_metadata_remain_strict(self):
        for key,field,bad in (('2','values','79'),('3','values','on'),('1','values','4,3,5,6'),
                              ('1','writable',True),('1','metadata',['wrong']),('2','metadata',['changed'])):
            changed=negotiated();changed[key][field]=bad
            with self.subTest(key=key,field=field),self.assertRaises(RuntimeError):
                verify_controls(controls(),changed,changed,controls())
        changed=negotiated();changed.pop('3')
        with self.assertRaises(RuntimeError):verify_controls(controls(),changed,changed,controls())
        with self.assertRaises(RuntimeError):verify_controls(controls(),negotiated(),negotiated(),negotiated())

    def test_initial_map_needs_exact_readonly_pcm_metadata(self):
        for index,text in ((0,"numid=1,iface=MIXER,name='Playback Channel Map'"),
                           (1,'  ; type=INTEGER,access=rw---R--,values=4,min=0,max=36,step=0'),
                           (1,'  ; type=INTEGER,access=r----R--,values=2,min=0,max=36,step=0'),
                           (3,'    | chmap-fixed=FR,FL,RL,RR')):
            before=controls();after=negotiated()
            before['1']['metadata'][index]=text;after['1']['metadata'][index]=text
            with self.assertRaises(RuntimeError):verify_controls(before,after,after,before)

    def test_saved_json_and_raw_numids_have_one_canonical_representation(self):
        value=parse_capture(raw(),IDENTITY,1001,1002)
        self.assertEqual(json.loads(json.dumps(value)),value)
        self.assertEqual(set(value['controls']),{'1','2','3'})

    def test_reader_credentials_and_exact_device_cannot_be_substituted(self):
        for change in (dict(readerUid=[0]*3),dict(readerGid=[1001]*3),dict(groups=[29]),
                       dict(processStart='200'),dict(pid=0),dict(identity=IDENTITY|dict(card=1)),
                       dict(identity=IDENTITY|dict(inode=99)),dict(identity=IDENTITY|dict(usbParent='/foreign'))):
            with self.subTest(change=change),self.assertRaises(RuntimeError):
                parse_capture(raw(READER|change),IDENTITY,1001,1002)

    def test_stat_identity_uses_real_inode_field(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'data';path.write_bytes(b'a');value=path.lstat()
            # Windows has no st_rdev; only that Linux field is supplied here.
            fields=SimpleNamespace(st_dev=value.st_dev,st_ino=value.st_ino,st_uid=value.st_uid,
                st_gid=value.st_gid,st_rdev=getattr(value,'st_rdev',0))
            self.assertEqual(node_identity(fields),dict(dev=value.st_dev,inode=value.st_ino,
                rdev=fields.st_rdev,uid=value.st_uid,gid=value.st_gid))

if __name__=='__main__':unittest.main()
