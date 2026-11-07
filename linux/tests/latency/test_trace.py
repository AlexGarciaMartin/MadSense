import copy
import csv
import io
import json
import unittest

from analyze import FIELDS as SAMPLE_FIELDS
from stage_trace import overlays, replace_once
from trace_analysis import EVENTS, EVENT_CAPACITY, EVENT_FIELDS, FIELDS, STAGES, ORIGINS, analyze
from trace_schedule import schedule


def stream(fields, rows):
    value = io.StringIO()
    writer = csv.DictWriter(value, fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    value.seek(0)
    return value


class AttributionTests(unittest.TestCase):
    def setUp(self):
        self.nonce = 0x8123
        self.rows = []
        self.samples = []
        # Six actual writes: one absent at each boundary and one delivered.
        # Other scheduled slots were skipped by the finite generator.
        for sequence in range(1000):
            base = 1_000_000_000 + sequence * 10_000_000
            self.samples.append(dict(zip(SAMPLE_FIELDS,
                [sequence, base, base + 1 if sequence < 6 else 0,
                 base + 2 if sequence < 6 else 0, base + 30 if sequence == 5 else 0,
                 0, 0, int(sequence >= 6)])))
            for stage in range(1, (min(sequence, 4) if sequence < 6 else 0) + 1):
                self.rows.append(dict(slot=len(self.rows), ticks=base + 3 + stage,
                    stage=stage, origin=1 if stage >= 3 else 0,
                    generation=sequence + 1 if stage >= 3 else 0,
                    tag=self.nonce << 16 | sequence, sequence8=sequence % 256))
        self.meta = dict(schema=1, kind="instrumented-research-copy", productionBinary=False,
            writersFinished=True, dumpedAfterBridgeDispose=True, overflow=0, badSequence=0,
            timestampFrequency=1_000_000_000, stages=STAGES, origins=ORIGINS, nonce=self.nonce,
            recorded=len(self.rows), capacity=131072, attempted=len(self.rows))
        self.meta["clockValidation"] = dict(nativeClockId=1, bracketedSamples=32,
                                            minimumBracketNs=20, maximumBracketNs=1000)
        self.probe = dict(schema=1, samplesSaved=True, rate=100, nominalSamples=1000,
            attempted=6, written=6, deadlineSkipped=994, matched=1, fatalErrno=0,
            malformedMatches=0, inputReads=1, unmatched=0, reordered=0, nonce=self.nonce)

    def run_analysis(self):
        return analyze(stream(FIELDS, self.rows), self.meta, stream(SAMPLE_FIELDS, self.samples), self.probe)

    def test_each_absence_is_attributed_once_without_claiming_its_cause(self):
        result = self.run_analysis()
        self.assertEqual(result["missingByLastBoundary"], {
            "before-parser": [0], "parser-to-slot": [1], "slot-to-interrupt": [2],
            "send-not-confirmed": [3], "after-send-return": [4]})
        self.assertEqual(result["missing"], 5)
        self.assertFalse(result["schedulingCauseEstablished"])
        self.assertFalse(result["physicalLatencyValidated"])

    def test_reader_may_receive_before_send_returns(self):
        self.rows[-1]["ticks"] = self.samples[5]["observed_ns"] + 1
        self.assertEqual(self.run_analysis()["observed"], 1)

    def test_complete_empty_recording_preserves_loss_before_parser(self):
        self.rows = []
        self.meta.update(recorded=0, attempted=0)
        self.samples[5]["observed_ns"] = 0
        self.probe.update(matched=0, inputReads=0)
        value = self.run_analysis()
        self.assertEqual(value["missingByLastBoundary"]["before-parser"], list(range(6)))
        self.assertEqual(value["observed"], 0)

    def test_no_writes_is_not_an_invalid_recording_or_a_latency_sample(self):
        self.rows = []
        self.meta.update(recorded=0, attempted=0)
        for row in self.samples:
            row.update(inject_before_ns=0, inject_after_ns=0, observed_ns=0, deadline_skipped=1)
        self.probe.update(attempted=0, written=0, matched=0, inputReads=0, deadlineSkipped=1000)
        value = self.run_analysis()
        self.assertEqual((value["written"], value["observed"], value["missing"]), (0, 0, 0))

    def test_nonce_overflow_clock_or_incomplete_publication_cannot_attribute_loss(self):
        original = self.meta.copy()
        for field, bad in (("nonce", self.nonce + 1), ("overflow", 1), ("badSequence", 1),
                           ("timestampFrequency", 10000000), ("recorded", len(self.rows) + 1),
                           ("writersFinished", False), ("productionBinary", True)):
            self.meta = original | {field: bad}
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.run_analysis()

    def test_impossible_stage_chain_and_early_trace_are_rejected(self):
        original = copy.deepcopy(self.rows)
        self.rows[0]["stage"] = 2
        with self.assertRaises(ValueError):
            self.run_analysis()
        self.rows = original
        self.rows[0]["ticks"] = 1
        with self.assertRaises(ValueError):
            self.run_analysis()

    def test_trace_of_unwritten_or_unrecorded_delivery_is_rejected(self):
        self.rows[-1]["tag"] = self.nonce << 16 | 999
        self.rows[-1]["sequence8"] = 999 % 256
        with self.assertRaises(ValueError):
            self.run_analysis()
        self.setUp()
        self.rows = self.rows[:-2]
        self.meta.update(recorded=len(self.rows), attempted=len(self.rows))
        with self.assertRaises(ValueError):
            self.run_analysis()

    def test_schedule_repeats_controls_and_trace_in_opposite_orders(self):
        cases = schedule()
        self.assertEqual(len({name for name, _, _ in cases}), 8)
        self.assertEqual([flag for _, rate, flag in cases if rate == 100], [False, True, True, False])
        self.assertEqual([flag for _, rate, flag in cases if rate == 1000], [True, False, False, True])


class EventAttributionTests(AttributionTests):
    """Sequence 2 is lost after raw-slot publication; events describe that window."""
    def setUp(self):
        super().setUp()
        self.slot2 = 1_000_000_000 + 2 * 10_000_000 + 5
        self.slot3 = 1_000_000_000 + 3 * 10_000_000 + 5
        self.events = []
        self.meta.update(events=EVENTS, eventCapacity=EVENT_CAPACITY, eventsRecorded=0, eventsAttempted=0, eventOverflow=0)

    def add(self, kind, ticks, generation=0, extra=0, origin=0):
        self.events.append(dict(slot=len(self.events), ticks=ticks, kind=kind, origin=origin,
                                generation=generation, extra=extra))
        self.meta.update(eventsRecorded=len(self.events), eventsAttempted=len(self.events))

    def run_analysis(self):
        # The inherited attribution cases must hold unchanged with event evidence present.
        return analyze(stream(FIELDS, self.rows), self.meta, stream(SAMPLE_FIELDS, self.samples), self.probe,
                       stream(EVENT_FIELDS, self.events))

    run_events = run_analysis

    def test_gate_visit_without_pending_urb_is_described_not_explained(self):
        self.add(6, self.slot2 + 10, generation=3, extra=1)
        self.add(7, self.slot2 + 20, generation=3)
        self.add(8, self.slot2 + 30, generation=3, extra=2, origin=2)
        self.add(10, self.slot2 + 31, generation=3, extra=2, origin=2)
        self.add(11, self.slot2 + 40, generation=3)
        self.add(5, self.slot3 + 1, generation=3, extra=2)
        result = self.run_events()
        attribution = result["slotToInterruptAttribution"]
        self.assertEqual(attribution["labels"]["gate-entry-without-pending-urb"], 1)
        [detail] = attribution["sequences"]
        self.assertEqual(detail["sequence"], 2)
        self.assertEqual(json.loads(json.dumps(attribution)), attribution)
        self.assertEqual(detail["replacedBySequence"], 3)
        self.assertEqual(detail["windowNs"], self.slot3 - self.slot2)
        self.assertEqual(detail["workersQueued"], 1)
        self.assertEqual(detail["gateEntries"], [dict(origin=2, afterSlotNs=30, generationsBehind=1)])
        self.assertEqual(detail["events"]["urbEnqueued"], 0)
        self.assertEqual(result["eventCounts"]["urbEnqueued"], 1)
        self.assertFalse(result["schedulingCauseEstablished"])

    def test_replacement_before_any_gate_visit_is_separate_from_urb_absence(self):
        self.add(6, self.slot2 + 10, generation=3, extra=0)
        self.add(8, self.slot3 + 50, generation=4, extra=2, origin=1)
        result = self.run_events()
        self.assertEqual(result["slotToInterruptAttribution"]["labels"]["slot-replaced-before-gate-entry"], 1)
        self.assertEqual(result["slotToInterruptAttribution"]["sequences"][0]["workersQueued"], 0)

    def test_event_evidence_and_recording_must_match(self):
        with self.assertRaises(ValueError):
            analyze(stream(FIELDS, self.rows), self.meta, stream(SAMPLE_FIELDS, self.samples), self.probe)
        self.add(8, self.slot2 + 30, generation=3, extra=2, origin=2)
        for change in (dict(eventOverflow=1), dict(eventsAttempted=5), dict(eventCapacity=1),
                       dict(events=dict(EVENTS, extra=13))):
            with self.subTest(change=change), self.assertRaises(ValueError):
                analyze(stream(FIELDS, self.rows), dict(self.meta, **change), stream(SAMPLE_FIELDS, self.samples),
                        self.probe, stream(EVENT_FIELDS, self.events))
        bad = copy.deepcopy(self.events); bad[0]["origin"] = 0
        with self.assertRaises(ValueError):
            analyze(stream(FIELDS, self.rows), self.meta, stream(SAMPLE_FIELDS, self.samples), self.probe,
                    stream(EVENT_FIELDS, bad))


class OverlayTests(unittest.TestCase):
    def test_missing_or_duplicate_hook_fails_instead_of_instrumenting_unknown_code(self):
        for text in ("other source", "anchor anchor"):
            with self.assertRaises(ValueError):
                replace_once(text, "anchor", "hook")

    def test_current_sources_have_exact_unique_hooks(self):
        from stage_trace import ROOT
        for relative, edits in overlays().items():
            text = (ROOT / relative).read_text(encoding="utf-8")
            for old, new in edits:
                text = replace_once(text, old, new)


if __name__ == "__main__":
    unittest.main()
