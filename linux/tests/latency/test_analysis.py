import copy
import csv
import io
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from analyze import FIELDS, summarize


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        # A finite 100 Hz capture with one known 300 ns path and a 100 ns
        # injection interval. The remaining slots expired without writes.
        self.rows = [dict(zip(FIELDS, [0, 1000, 1010, 1110, 1310, 0, 0, 0]))]
        self.rows += [dict(zip(FIELDS, [i, 1000 + i * 10000000, 0, 0, 0, 0, 0, 1])) for i in range(1, 1000)]
        self.probe = dict(schema=1, samplesSaved=True, rate=100, nominalSamples=1000, attempted=1,
                          written=1, deadlineSkipped=999, matched=1, fatalErrno=0, malformedMatches=0,
                          inputReads=1, unmatched=0, reordered=0)

    def analyze(self):
        stream = io.StringIO()
        writer = csv.DictWriter(stream, FIELDS)
        writer.writeheader(); writer.writerows(self.rows)
        stream.seek(0)
        return summarize(stream, self.probe)

    def test_injection_interval_and_generator_loss_are_not_transport_loss(self):
        result = self.analyze()
        self.assertEqual(result["result"], "generator-limit")
        self.assertEqual(result["elapsedFromInjectionLowerNs"]["p99"], 200)
        self.assertEqual(result["elapsedFromInjectionUpperNs"]["p99"], 300)
        self.assertEqual(result["producerDeadlineSkipped"], 999)
        self.assertEqual(result["missingWrittenSequences"], [])
        self.assertFalse(result["cleanupConfirmed"])

    def test_observation_can_precede_injection_return(self):
        self.rows[0]["observed_ns"] = 1060
        result = self.analyze()
        self.assertEqual(result["elapsedFromInjectionLowerNs"]["min"], 0)
        self.assertEqual(result["elapsedFromInjectionUpperNs"]["max"], 50)

    def test_missing_written_report_is_preserved(self):
        self.rows[1].update(scheduled_ns=10001000, inject_before_ns=10001001,
                            inject_after_ns=10001002, deadline_skipped=0)
        self.probe.update(attempted=2, written=2, deadlineSkipped=998)
        result = self.analyze()
        self.assertEqual(result["result"], "transport-limit")
        self.assertEqual(result["missingWrittenSequences"], [1])

    def test_zero_observations_is_not_a_latency_measurement(self):
        self.rows[0]["observed_ns"] = 0
        self.probe.update(matched=0, inputReads=0)
        result = self.analyze()
        self.assertEqual(result["result"], "no-samples")
        self.assertIsNone(result["elapsedFromInjectionUpperNs"])

    def test_duplicate_reads_do_not_inflate_latency_population(self):
        self.rows[0]["duplicates"] = 7
        self.probe["inputReads"] += 7
        result = self.analyze()
        self.assertEqual(result["observed"], 1)
        self.assertEqual(result["duplicateReads"], 7)

    def test_failed_transport_keeps_unattempted_slots(self):
        self.probe.update(fatalErrno=19, deadlineSkipped=0)
        for row in self.rows[1:]:
            row.update(scheduled_ns=0, deadline_skipped=0)
        result = self.analyze()
        self.assertEqual(result["unattempted"], 999)
        self.assertEqual(result["result"], "transport-limit")

    def test_rejects_contradictory_counters_or_timestamps(self):
        original_rows, original_probe = copy.deepcopy(self.rows), self.probe.copy()
        changes = [lambda: self.probe.update(inputReads=2), lambda: self.probe.update(matched=2),
                   lambda: self.rows[0].update(observed_ns=1001), lambda: self.rows[1].update(observed_ns=10),
                   lambda: self.rows[1].update(sequence=0), lambda: self.rows[0].update(write_errno=5),
                   lambda: self.probe.update(malformedMatches=1, inputReads=2)]
        for index, change in enumerate(changes):
            with self.subTest(index=index):
                self.rows, self.probe = copy.deepcopy(original_rows), original_probe.copy()
                change()
                with self.assertRaises(ValueError):
                    self.analyze()


@unittest.skipUnless(sys.platform == "linux" and shutil.which("cc"), "Native Linux C compiler required")
class NativeBuildTests(unittest.TestCase):
    def test_compile_and_refuse_unscoped_invocation(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "probe"
            subprocess.run(["cc", "-std=c11", "-O2", "-pthread", "-Wall", "-Wextra", "-Werror",
                            str(Path(__file__).with_name("latency_probe.c")), "-o", executable], check=True)
            result = subprocess.run([executable], capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, b"")


if __name__ == "__main__":
    unittest.main()
