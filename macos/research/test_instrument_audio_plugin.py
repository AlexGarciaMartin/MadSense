import pathlib
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


if __name__ == '__main__':
    unittest.main()
