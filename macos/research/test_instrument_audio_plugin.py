import pathlib
import unittest
from instrument_audio_plugin import instrument, instrument_ring


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

    def test_recovery_is_opt_in_and_observes_before_rejecting(self):
        baseline = instrument(self.source, 16384)
        recovery = instrument(self.source, 16384, True)
        self.assertNotIn('compare_exchange_strong(expected, generation + 1', baseline)
        self.assertIn('compare_exchange_strong(expected, generation + 1', recovery)
        self.assertLess(recovery.index('ingressTrace.begin('), recovery.index('resyncPolicy.reject('))
        self.assertLess(recovery.index('ring.rejectFrames(frames)'), recovery.index('ring.push('))
        self.assertLess(recovery.index('packet.generation = pcmGeneration.load'), recovery.index('ring.popLatest('))
        self.assertLess(recovery.index('ring.popLatest('), recovery.index('audio_probe::finishCapturedRead('))

    def test_ring_changes_are_counting_and_acquire_accessor(self):
        source = (self.path.parent / 'AudioRing.hpp').read_text(encoding='utf-8')
        self.assertEqual(instrument_ring(source, False), source)
        result = instrument_ring(source, True)
        addition = '''    void rejectFrames(uint32_t frames) noexcept {
        dropped_.fetch_add(frames, std::memory_order_release);
    }
'''
        self.assertEqual(result.replace(addition, '').replace('dropped_.load(std::memory_order_acquire)',
                                                             'dropped_.load(std::memory_order_relaxed)'), source)


if __name__ == '__main__':
    unittest.main()
