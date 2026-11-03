import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace, MethodType
from unittest.mock import Mock, patch

from pcm_evidence import marked_source, expected_gain, verify_phase, verify_negotiation, load_capture, digest, boundary_offset
from stage_pcm import (ROOT, DEVICE, ACTOR, HELPER, RECORDER, transform_device, transform_actor,
    OBSERVE_OLD, OBSERVE_NEW, ACTOR_HOOKS, expected_changes, verify_pcm_overlay)
from stage_export import OLD, NEW

def phase(raw):
    return {"packets":[dict(offset=0,length=len(raw),timestamp=10)]}, dict(packets=0,bytes=0,timestamp=1), dict(packets=1,bytes=len(raw),timestamp=20)

class PcmEvidenceTests(unittest.TestCase):
    def test_overlay_only_adds_exact_descriptor_capture_and_actor_hooks(self):
        original=(ROOT/DEVICE).read_text(); actor=(ROOT/ACTOR).read_text()
        changed=transform_device(original)
        self.assertEqual(changed.replace(NEW,OLD).replace(OBSERVE_NEW,OBSERVE_OLD),original)
        changed=transform_actor(actor)
        for old,new in ACTOR_HOOKS:changed=changed.replace(new,old)
        self.assertEqual(changed,actor)
        with self.assertRaises(ValueError):transform_device(transform_device(original))
        with self.assertRaises(ValueError):transform_actor(transform_actor(actor))

    def test_pcm_overlay_hashes_require_all_four_committed_sources(self):
        sources=[(ROOT/name).read_bytes() for name in (DEVICE,ACTOR,HELPER,RECORDER)]
        value=dict(schema=1,sourceCommit='a'*40,sourceArchiveSha256='b'*64,productionBinary=False,
            kind='audio-interface-pcm-research-copy',changes=expected_changes(*sources))
        self.assertTrue(verify_pcm_overlay(value,*sources))
        with self.assertRaises(ValueError):verify_pcm_overlay(value,*sources[:-1],sources[-1]+b'changed')
        value['changes']['unrelated.cs']={}
        with self.assertRaises(ValueError):verify_pcm_overlay(value,*sources)

    def test_one_alignment_retains_all_channels_and_exact_front_gain(self):
        source=marked_source(42,256)
        for divisors in ((1,1,1,1),(2,4,1,1)):
            expected=expected_gain(source,divisors); raw=bytes(80)+expected+bytes(16)
            capture,start,end=phase(raw)
            result=verify_phase(capture,raw,source,start,end,divisors)
            self.assertEqual(result['alignmentFrames'],10)
            self.assertTrue(all(result['exactChannels'].values()))
            self.assertFalse(result['alignmentIsLatency'])

    def test_missing_repeated_swapped_and_shifted_frames_cannot_realign(self):
        source=marked_source(41,256)
        swapped=b''.join(source[i+2:i+4]+source[i:i+2]+source[i+4:i+8] for i in range(0,len(source),8))
        shifted=b''.join(source[i:i+2]+source[(i+8)%len(source)+2:(i+8)%len(source)+4]+source[i+4:i+8] for i in range(0,len(source),8))
        for raw in (source[:800]+source[808:],source[:800]+source[792:800]+source[800:],
                    swapped,shifted,source[:-8],source+source):
            capture,start,end=phase(raw)
            with self.subTest(raw=digest(raw)),self.assertRaises(RuntimeError):verify_phase(capture,raw,source,start,end)

    def test_wrong_gain_and_nonintegral_reference_are_rejected(self):
        source=marked_source(43,256); raw=expected_gain(source,(2,4,1,1))
        capture,start,end=phase(raw)
        with self.assertRaises(RuntimeError):verify_phase(capture,raw,source,start,end)
        with self.assertRaises(RuntimeError):expected_gain(b'\x01\0'*4,(2,4,1,1))

    def test_prefix_bookmarks_cannot_select_unattributed_bytes(self):
        source=marked_source(44,256); capture,start,end=phase(source)
        for bad in (start|{'bytes':8},start|{'packets':2},end|{'timestamp':0}):
            with self.assertRaises(RuntimeError):verify_phase(capture,source,source,bad,end)

    def test_bookmark_time_covers_its_prefix_but_not_an_inflight_next_packet(self):
        capture={'packets':[dict(offset=0,length=8,timestamp=10),dict(offset=8,length=8,timestamp=11)]}
        with self.assertRaises(RuntimeError):boundary_offset(capture,dict(packets=1,bytes=8,timestamp=9))
        self.assertEqual(boundary_offset(capture,dict(packets=1,bytes=8,timestamp=12)),8)

    def test_failed_player_preserves_first_error_when_end_bookmark_also_fails(self):
        import run_pcm_guest
        actor=SimpleNamespace(mark=Mock(side_effect=[{'packets':0,'bytes':0,'timestamp':1},RuntimeError('owner already closed')]))
        child=SimpleNamespace(pid=123,returncode=1,poll=lambda:1)
        audio=SimpleNamespace(result={},card=3,exporter=None,uid=1000,save=Mock(),start=Mock(return_value=child))
        with patch.object(run_pcm_guest,'process_identity',return_value=((1000,)*4,'8')),patch.object(run_pcm_guest.time,'sleep'):
            with self.assertRaisesRegex(RuntimeError,'PCM playback or four-channel binding failed'):
                run_pcm_guest.playback(actor,audio,'direct','source.wav')
        row=audio.result['pcmPlaybacks'][0]
        self.assertEqual(row['error'],'PCM playback or four-channel binding failed')
        self.assertEqual(row['endBookmarkError'],'owner already closed')
        self.assertEqual(row['processStart'],8)
        self.assertIs(type(row['processStart']),int)

    def test_negotiation_silence_is_observed_but_not_promoted_to_gain_or_continuity(self):
        raw=bytes(1024);capture,start,end=phase(raw)
        result=verify_negotiation(capture,raw,start,end)
        self.assertEqual(result['capturedFrames'],128)
        self.assertFalse(result['gainValidated']);self.assertFalse(result['sampleContinuityValidated'])
        with self.assertRaises(RuntimeError):verify_negotiation(capture,bytes(1023)+b'\x01',start,end)
        with self.assertRaises(RuntimeError):verify_negotiation(capture,raw,start,start)

    def test_player_logs_do_not_collide_with_live_exporter_or_gain_command_logs(self):
        import run_pcm_guest
        from consumer_session import ConsumerSession
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            for label in ('pcm-export','pcm-front-gain'):
                for stream in ('stdout','stderr'):(root/(label+'.'+stream)).write_bytes(b'owner evidence')
            child=SimpleNamespace(pid=123,returncode=1,poll=lambda:1)
            audio=SimpleNamespace(output=root,root=root,env={},logs=[],processes=[],uid=1000,gid=1000,
                result={},card=3,exporter=None,save=Mock())
            audio.start=MethodType(ConsumerSession.start,audio)
            actor=SimpleNamespace(mark=Mock(return_value={'packets':0,'bytes':0,'timestamp':1}))
            try:
                with patch('consumer_session.subprocess.Popen',return_value=child), \
                     patch.object(run_pcm_guest,'process_identity',return_value=((1000,)*4,'8')), \
                     patch.object(run_pcm_guest.time,'sleep'):
                    for label in ('export','front-gain'):
                        with self.assertRaisesRegex(RuntimeError,'PCM playback or four-channel binding failed'):
                            run_pcm_guest.playback(actor,audio,label,'source.wav')
                        self.assertTrue((root/('pcm-player-'+label+'.stdout')).exists())
                        self.assertEqual((root/('pcm-'+label+'.stdout')).read_bytes(),b'owner evidence')
            finally:
                for stream in audio.logs:stream.close()

    def test_file_hash_and_rejection_counters_are_required(self):
        source=marked_source(45,256)
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);path=root/'pcm-ingress-000.bin';path.write_bytes(source)
            value=dict(schema=1,scope='research-copy-usb-quad-ingress',productionBinary=False,clean=True,
                attempts=1,capacityRejected=0,concurrentRejected=0,malformedRejected=0,timestampFrequency=1000000000,
                bytes=len(source),packets=[dict(offset=0,length=len(source),timestamp=10)],
                chunks=[dict(name=path.name,offset=0,length=len(source),sha256=digest(source))])
            manifest=root/'pcm-ingress.json';manifest.write_text(json.dumps(value))
            self.assertEqual(load_capture(root)[1],source)
            for field in ('attempts','capacityRejected','concurrentRejected','malformedRejected'):
                manifest.write_text(json.dumps(value|{field:99}))
                with self.subTest(field=field),self.assertRaises(RuntimeError):load_capture(root)
            manifest.write_text(json.dumps(value));path.write_bytes(source[:-8]+bytes(8))
            with self.assertRaises(RuntimeError):load_capture(root)

if __name__=='__main__':unittest.main()
