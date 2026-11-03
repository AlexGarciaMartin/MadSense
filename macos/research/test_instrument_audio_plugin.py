import hashlib
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from instrument_audio_plugin import instrument


class InstrumentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = pathlib.Path(__file__).resolve().parents[1] / 'audio' / 'plugin.cpp'
        cls.source = cls.path.read_text(encoding='utf-8')

    def test_production_bytes_unchanged_and_original_push_retained(self):
        before = self.path.read_bytes()
        result = instrument(self.source)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(result.count('ring.push(static_cast<const float*>(main), frames, generation);'), 1)
        self.assertIn('if (!isOwner(pid) || running.load(std::memory_order_acquire))', result)
        self.assertIn('snapshot.generation != pcmGeneration.load', result)
        self.assertIn('trace.output = stamp(cycle->mOutputTime);', result)

    def test_missing_site_fails_closed(self):
        with self.assertRaises(ValueError):
            instrument(self.source.replace('AudioRing ring;', 'AudioRing changed;'))

    def test_ambiguous_site_fails_closed(self):
        with self.assertRaises(ValueError):
            instrument(self.source + '\nAudioRing ring;\n')

    def test_instrumentation_is_not_applied_twice(self):
        with self.assertRaises(ValueError):
            instrument(instrument(self.source))

    def test_only_clock_period_differs_between_variants(self):
        baseline = instrument(self.source, 256)
        compliant = instrument(self.source, 16384)
        self.assertEqual(baseline.replace('kResearchTimestampPeriod = 256;',
                                          'kResearchTimestampPeriod = 16384;'), compliant)
        self.assertIn('copyValue<UInt32>(kResearchTimestampPeriod, capacity, written, out)', compliant)
        self.assertIn('ticksPerFrame * kResearchTimestampPeriod', compliant)
        self.assertIn('*sample = periods * kResearchTimestampPeriod;', compliant)
        self.assertNotIn('kTimestampPeriodFrames', baseline)
        self.assertNotIn('kTimestampPeriodFrames', compliant)

    def test_arbitrary_period_not_accepted(self):
        with self.assertRaises(ValueError):
            instrument(self.source, 512)

    def test_observe_disables_only_production_recovery_and_observes_before_rejecting(self):
        baseline = instrument(self.source, 16384)
        recovery = instrument(self.source, 16384, True)
        self.assertEqual(baseline.replace('kRecoverHostResync = false;', 'kRecoverHostResync = true;'), recovery)
        self.assertEqual(recovery.count('compare_exchange_strong(expected, generation + 1'), 1)
        self.assertEqual(recovery.count('ResyncPolicy resyncPolicy;'), 1)
        self.assertEqual(recovery.count('finishCapturedRead('), 1)
        self.assertLess(recovery.index('ingressTrace.begin('), recovery.index('resyncPolicy.reject('))
        self.assertIn('resyncPolicy.reject(generation, cycle ? cycle->mIOCycleCounter : 0) && frames', recovery)
        self.assertNotIn('observed && resyncPolicy', recovery) # Observer failure cannot disable production policy.
        self.assertLess(recovery.index('ring.rejectFrames(frames)'), recovery.index('ring.push('))
        self.assertLess(recovery.index('packet.generation = pcmGeneration.load'), recovery.index('ring.popLatest('))
        self.assertLess(recovery.index('ring.popLatest('), recovery.index('finishCapturedRead('))

    def test_variants_copy_identical_production_headers_and_record_digests(self):
        generator = pathlib.Path(__file__).with_name('instrument_audio_plugin.py')
        with tempfile.TemporaryDirectory() as temporary:
            output = pathlib.Path(temporary) / 'plugin-ingress.cpp'
            for arguments in (['--reject-resync'], []):
                metadata = json.loads(subprocess.check_output(
                    [sys.executable, str(generator), str(self.path), str(output), *arguments], text=True))
                for name, digest in metadata['production_header_sha256'].items():
                    source = (self.path.parent / name).read_bytes()
                    self.assertEqual((output.parent / name).read_bytes(), source)
                    self.assertEqual(digest, hashlib.sha256(source).hexdigest())
                self.assertEqual(metadata['production_ring_sha256'], metadata['instrumented_ring_sha256'])


if __name__ == '__main__':
    unittest.main()
