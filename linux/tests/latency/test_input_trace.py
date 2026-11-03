import copy
import tempfile
import unittest
from pathlib import Path

from analyze import FIELDS as SAMPLE_FIELDS
from input_trace_analysis import FIELDS, STAGES, analyze
from stage_trace import ROOT, overlays, recorder_paths, replace_once
from test_trace import stream
from trace_schedule import schedule
from trace_variant import kind, require_control


class InputTailTests(unittest.TestCase):
    def fixture(self, written=6, rate=100):
        self.nonce = 0x8123
        self.samples, self.rows = [], []
        for sequence in range(rate * 10):
            base = 1_000_000_000 + sequence * (1_000_000_000 // rate)
            self.samples.append(dict(zip(SAMPLE_FIELDS, [sequence, base,
                base + 1 if sequence < written else 0, base + 2 if sequence < written else 0,
                base + 20 if sequence < written else 0, 0, 0, int(sequence >= written)])))
            if sequence >= written:
                continue
            # A consumer can remove a published report before the producer's
            # post-enqueue hook. This ordering must remain admissible.
            for stage, delta in ((1, 5), (4, 7), (2, 9)):
                self.rows.append(dict(slot=len(self.rows), ticks=base + delta, stage=stage,
                    tag=self.nonce << 16 | sequence, sequence8=sequence % 256,
                    broker_read_ns=base + 3, received_ns=base + 4, queue_count=0,
                    last_read_started_ns=base + 2, thread=1 if stage != 4 else 2))
        self.probe = dict(schema=1, samplesSaved=True, rate=rate, nominalSamples=rate * 10,
            attempted=written, written=written, deadlineSkipped=rate * 10 - written,
            matched=written, fatalErrno=0, malformedMatches=0, inputReads=written,
            unmatched=0, reordered=0, nonce=self.nonce)
        total = len(self.rows)
        self.rows = self.rows[-4096:]
        self.meta = dict(schema=1, kind="client-input-tail", productionBinary=False,
            writersFinished=True, dumpedAfterBridgeDispose=True, holes=0, collisions=0,
            badSequence=0, timestampFrequency=1_000_000_000, stages=STAGES, nonce=self.nonce,
            capacity=4096, totalAttempts=total, retained=len(self.rows), overwritten=max(0, total - 4096),
            tailStart=max(0, total - 4096), tailEnd=total, gcPauseAttribution=False,
            clockValidation=dict(nativeClockId=1, bracketedSamples=32, minimumBracketNs=20, maximumBracketNs=1000),
            gcBefore=dict(collectionCounts=[0, 0, 0], totalAllocatedBytes=100, lastCollectionIndex=0),
            gcAfter=dict(collectionCounts=[1, 0, 0], totalAllocatedBytes=1000, lastCollectionIndex=1))

    def setUp(self):
        self.fixture()

    def run_analysis(self):
        return analyze(stream(FIELDS, self.rows), self.meta, stream(SAMPLE_FIELDS, self.samples), self.probe)

    def test_dequeue_before_enqueue_hook_is_not_a_lost_input(self):
        result = self.run_analysis()
        self.assertEqual(result["completeRetainedInputs"], 6)
        self.assertEqual(result["stageEvents"], dict(accept=6, enqueue=6, enqueueFailed=0, dequeue=6))
        self.assertFalse(result["schedulingCauseEstablished"])
        self.assertFalse(result["gcPauseAttribution"])
        self.assertEqual(result["gcCollectionDeltas"], [1, 0, 0])

    def test_wrapped_tail_may_start_after_a_predecessor_was_overwritten(self):
        self.fixture(2000, 1000)
        result = self.run_analysis()
        self.assertEqual(result["retainedEvents"], 4096)
        self.assertEqual(result["overwrittenEvents"], 1904)
        self.assertFalse(result["fullHistoryRetained"])
        self.assertFalse(result["overwrittenHistoryAbsenceInferred"])

    def test_failed_enqueue_count_snapshot_need_not_still_be_eight(self):
        self.rows = self.rows[:-2]
        row = self.rows[-1].copy()
        row.update(stage=3, ticks=row["ticks"] + 1, slot=len(self.rows), queue_count=7)
        self.rows.append(row)
        self.meta.update(totalAttempts=len(self.rows), tailEnd=len(self.rows), retained=len(self.rows))
        self.samples[5]["observed_ns"] = 0
        self.probe.update(matched=5, inputReads=5)
        result = self.run_analysis()
        self.assertEqual(result["enqueueFailures"][0]["queueCountSnapshot"], 7)
        self.assertEqual(result["enqueueFailures"][0]["sequence"], 5)

    def test_hole_collision_or_unfinished_writer_invalidates_capture(self):
        original = self.meta.copy()
        for field, value in (("holes", 1), ("collisions", 1), ("writersFinished", False),
                             ("overwritten", 1), ("totalAttempts", 999), ("badSequence", 1)):
            self.meta = original | {field: value}
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.run_analysis()
        self.meta = original
        self.rows[2]["slot"] += 1
        with self.assertRaises(ValueError):
            self.run_analysis()

    def test_trace_cannot_join_other_nonce_unwritten_input_or_changed_source_time(self):
        original = copy.deepcopy(self.rows)
        for values in (dict(tag=(self.nonce + 1) << 16), dict(tag=self.nonce << 16 | 999, sequence8=999 % 256),
                       dict(broker_read_ns=self.rows[0]["broker_read_ns"] - 1)):
            self.rows = copy.deepcopy(original)
            self.rows[1].update(values)
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.run_analysis()

    def test_boolean_metadata_is_not_an_integer_counter(self):
        original = self.meta.copy()
        for field in ("holes", "collisions", "badSequence", "tailStart", "tailEnd", "retained", "overwritten"):
            self.meta = original | {field: False}
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.run_analysis()

    def test_missing_accept_without_overwrite_and_duplicate_stage_fail(self):
        self.rows[0]["stage"] = 2
        with self.assertRaises(ValueError):
            self.run_analysis()
        self.fixture()
        self.rows[0]["stage"] = 3
        with self.assertRaises(ValueError):
            self.run_analysis()

    def test_empty_recording_is_not_absence_attribution(self):
        self.rows = []
        self.meta.update(totalAttempts=0, tailStart=0, tailEnd=0, retained=0)
        with self.assertRaises(ValueError):
            self.run_analysis()  # Complete history cannot miss already observed input.
        for sample in self.samples:
            sample["observed_ns"] = 0
        self.probe.update(matched=0, inputReads=0)
        result = self.run_analysis()
        self.assertEqual(result["retainedEvents"], 0)
        self.assertFalse(result["overwrittenHistoryAbsenceInferred"])

    def test_reordered_client_reports_are_not_sorted_into_plausible_evidence(self):
        self.rows[0:3], self.rows[3:6] = self.rows[3:6], self.rows[0:3]
        for slot, row in enumerate(self.rows):
            row["slot"] = slot
        with self.assertRaises(ValueError):
            self.run_analysis()


class InputOverlayTests(unittest.TestCase):
    def test_client_variant_has_only_client_and_actor_hooks(self):
        edits = overlays("input")
        self.assertEqual(set(edits), {"linux/platform/LinuxPhysicalSession.cs", "linux/tests/app-integration/Program.cs"})
        for relative, replacements in edits.items():
            source = (ROOT / relative).read_text(encoding="utf-8")
            for old, new in replacements:
                source = replace_once(source, old, new)
        self.assertEqual(recorder_paths("input")[1], "linux/platform/ResearchInputTrace.cs")
        self.assertNotEqual(recorder_paths("input"), recorder_paths())

    def test_input_schedule_is_1000hz_abba_and_legacy_is_unchanged(self):
        rows = schedule("input")
        self.assertEqual([flag for _, _, flag in rows], [False, True, True, False])
        self.assertEqual({rate for _, rate, _ in rows}, {1000})
        self.assertEqual(len({name for name, _, _ in rows}), 4)
        self.assertEqual(len(schedule()), 8)
        with self.assertRaises(ValueError):
            kind({"traceKind": "unknown"})

    def test_control_cannot_contain_either_trace_kind(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            require_control(root)
            for filename in ("input-trace.csv", "input-trace.json", "report-trace.csv", "report-trace.json"):
                path = root / filename
                path.write_text("stale")
                with self.subTest(filename=filename), self.assertRaises(ValueError):
                    require_control(root)
                path.unlink()


if __name__ == "__main__":
    unittest.main()
