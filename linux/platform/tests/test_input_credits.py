import io
import json
import os
from pathlib import Path
import stat
import struct
import sys
import unittest
from contextlib import ExitStack, redirect_stdout
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import broker
import broker_core as core

LEASE = "0123456789abcdef0123456789abcdef"


class InputCreditTests(unittest.TestCase):
    def test_window_counts_consumption_not_socket_delivery(self):
        now = [100]
        credits = core.InputCredits(LEASE, lambda: now[0])
        self.assertEqual([credits.reserve() for _ in range(8)], list(range(1, 9)))
        self.assertFalse(credits.can_read())
        with self.assertRaises(core.BrokerError): credits.reserve()
        self.assertEqual(credits.issued, 8)
        now[0] += 3_000_000
        credits.acknowledge(LEASE, 3)
        self.assertEqual([credits.reserve() for _ in range(3)], [9, 10, 11])
        self.assertFalse(credits.can_read())
        credits.acknowledge(LEASE, 3)  # Replayed duplicate cannot grant credit twice.
        self.assertFalse(credits.can_read())
        self.assertEqual(credits.metrics(), dict(creditBlockedCount=2, creditBlockedNs=3_000_000,
                                                creditMaxBlockedNs=3_000_000))

    def test_invalid_acknowledgements_never_change_credit(self):
        credits = core.InputCredits(LEASE)
        for _ in range(8): credits.reserve()
        credits.acknowledge(LEASE, 3)
        for identity, through in [("f"*32, 4), (LEASE, 2), (LEASE, 9), (LEASE, True), (LEASE, 0),
                                  (LEASE, -1), (LEASE, 4.0), (LEASE, "4")]:
            with self.subTest(identity=identity, through=through):
                with self.assertRaises(core.BrokerError): credits.acknowledge(identity, through)
                self.assertEqual((credits.issued, credits.acknowledged), (8, 3))

    def test_timeout_is_operational_and_does_not_claim_kernel_loss_count(self):
        now = [0]
        credits = core.InputCredits(LEASE, lambda: now[0])
        for _ in range(8): credits.reserve()
        now[0] = core.INPUT_CREDIT_TIMEOUT_NS - 1
        credits.check_timeout()
        now[0] += 1
        with self.assertRaisesRegex(core.BrokerError, "kernel input backlog is unverified"):
            credits.check_timeout()
        self.assertEqual(credits.acknowledged, 0)

    def test_sequence_cannot_wrap(self):
        credits = core.InputCredits(LEASE)
        credits.issued = credits.acknowledged = core.MAX_INPUT_SEQUENCE
        with self.assertRaises(core.BrokerError): credits.reserve()
        self.assertEqual(credits.issued, core.MAX_INPUT_SEQUENCE)

    def test_strict_wire_schema_and_controlled_legacy_acquisition(self):
        legacy = dict(id=1, op="acquire", path="/dev/hidraw0")
        self.assertEqual(core.request(json.dumps(legacy).encode()), legacy)
        for version in [True, 0, 2, 1.0, "1", None]:
            with self.subTest(version=version):
                with self.assertRaises(core.BrokerError):
                    core.request(json.dumps(legacy | dict(inputFlowVersion=version)).encode())
        self.assertEqual(core.request(json.dumps(legacy | dict(inputFlowVersion=1)).encode())["inputFlowVersion"], 1)
        ack = dict(id=0, op="input_ack", leaseId=LEASE, through=8)
        self.assertEqual(core.request(json.dumps(ack).encode()), ack)
        for changed in [dict(id=1), dict(id=True), dict(through=True), dict(through=0), dict(through=8.0),
                        dict(through=2**63), dict(leaseId="../lease"), dict(extra=0)]:
            with self.subTest(changed=changed):
                with self.assertRaises(core.BrokerError): core.request(json.dumps(ack | changed).encode())


class CreditServiceTests(unittest.TestCase):
    def run_service(self, ending):
        listener, endpoint, lease, foreign = MagicMock(), MagicMock(), MagicMock(), MagicMock()
        listener.accept.return_value = (endpoint, None)
        endpoint.getsockopt.return_value = struct.pack("3i", 7, 1000, 1000)
        lease.pid, lease.fd, lease.pidfd, lease.active, lease.lease_id = 7, 100, 101, True, LEASE
        lease.acquire.return_value = {"isolationVerified": True}
        lease.read_feature.return_value = b"\x05\x42"
        acquire = dict(id=1, op="acquire", path="/dev/hidraw0", inputFlowVersion=1)
        requests = [json.dumps(acquire).encode()+b"\n",
                    b'{"id":2,"op":"get_feature","reportId":5,"size":2}\n']
        if ending == "ack":
            requests += [json.dumps(dict(id=0, op="input_ack", leaseId=LEASE, through=3)).encode()+b"\n", b""]
        elif ending == "invalid":
            requests += [json.dumps(dict(id=0, op="input_ack", leaseId="f"*32, through=3)).encode()+b"\n"]
        elif ending in ("eof", "foreign"): requests += [b""]
        if ending == "foreign":
            listener.accept.side_effect = [(endpoint, None), (foreign, None)]
            foreign.getsockopt.return_value = struct.pack("3i", 8, 1002, 1002)
            foreign.recv.return_value = json.dumps(dict(id=0, op="input_ack", leaseId=LEASE, through=3)).encode()+b"\n"
        endpoint.recv.side_effect = requests
        packets, pending = [], bytearray()
        def send(data):
            pending.extend(data)
            while b"\n" in pending:
                line, _, rest = pending.partition(b"\n"); pending[:] = rest
                packets.append(json.loads(line))
            return len(data)
        endpoint.send.side_effect = send
        runtime, path = MagicMock(), MagicMock()
        runtime.stat.return_value = type("Stat", (), dict(st_uid=0, st_mode=stat.S_IFDIR | 0o755))()
        path.exists.return_value = False
        loops, now, handlers = [0], [0.0], {}
        def select_once(readable, writable, _errors, timeout):
            loops[0] += 1; now[0] += .01
            n = loops[0]
            if n == 1: return [listener], [], []
            if n == 2: return [endpoint], writable, []
            if 3 <= n <= 10:
                self.assertIn(lease.fd, readable)
                return [lease.fd], writable, []
            if n == 11:
                self.assertNotIn(lease.fd, readable, "Full credits stop physical reads after exactly eight")
                self.assertIn(lease.pidfd, readable)
                now[0] += .6  # Isolation checks still run while credits are exhausted.
                return [endpoint, listener] if ending == "foreign" else [endpoint], writable, []
            if n == 12:
                self.assertNotIn(lease.fd, readable, "Sending all eight packets did not return consumer credits")
                self.assertTrue(any(p.get("id") == 2 and p.get("ok") for p in packets) or writable)
                if ending == "timeout":
                    now[0] += 5.1
                    return [], writable, []
                if ending == "death":
                    lease.verify.side_effect = core.BrokerError("owner_lost", "Owner exited while credit-blocked")
                    return [lease.pidfd], writable, []
                if ending == "foreign": return [foreign], writable, []
                return [endpoint], writable, []
            if n == 13 and ending == "ack":
                self.assertIn(lease.fd, readable)
                return [lease.fd], writable, []
            if n == 14 and ending == "ack": return [endpoint], writable, []
            if n == 13 and ending == "foreign":
                self.assertNotIn(lease.fd, readable, "Foreign ACK must not return any owner credit")
                self.assertIn(endpoint, readable)
                self.assertNotIn(foreign, readable)
                return [endpoint], writable, []
            handlers[broker.signal.SIGTERM](None, None)
            return [], writable, []
        physical_read = MagicMock(return_value=b"\x31\x42")
        with ExitStack() as stack:
            for item in (
                patch.object(broker, "RUN", runtime), patch.object(broker, "SOCKET", path),
                patch.object(broker, "JOURNAL", path), patch.object(broker, "RULE", path),
                patch.object(broker, "recover_imports", return_value=set()), patch.object(broker, "restore_journal"),
                patch.object(broker, "notify"), patch.object(broker, "preflight"), patch.object(broker, "Lease", return_value=lease),
                patch.object(os, "geteuid", return_value=0, create=True), patch.object(os, "chmod"),
                patch.object(os, "pidfd_open", return_value=101, create=True), patch.object(os, "close"),
                patch.object(os, "read", physical_read),
                patch.object(broker, "process_start", return_value=1),
                patch.object(broker.socket, "AF_UNIX", 1, create=True), patch.object(broker.socket, "SOCK_CLOEXEC", 0, create=True),
                patch.object(broker.socket, "SO_PEERCRED", 17, create=True), patch.object(broker.socket, "socket", return_value=listener),
                patch.object(broker.select, "select", side_effect=select_once),
                patch.object(broker.signal, "signal", side_effect=lambda kind, handler: handlers.update({kind: handler})),
                patch.object(broker.time, "monotonic", side_effect=lambda: now[0]),
                patch.object(core.time, "monotonic_ns", side_effect=lambda: int(now[0]*1_000_000_000)),
            ): stack.enter_context(item)
            output = io.StringIO()
            with redirect_stdout(output): broker.serve()
            read_count = physical_read.call_count
        self.assertEqual(read_count, 9 if ending == "ack" else 8)
        self.assertGreaterEqual(lease.verify.call_count, 2)
        lease.close.assert_called_once()
        self.assertEqual(sum(p.get("event") == "input" for p in packets), read_count)
        self.assertTrue(next(p for p in packets if p.get("id") == 2)["ok"], "Feature RPC completes with the input window full")
        self.assertFalse(any(p.get("id") == 0 for p in packets), "Credit notifications never create RPC replies")
        return [json.loads(line) for line in output.getvalue().splitlines()]

    def test_full_window_retains_rpc_isolation_and_resumes_only_on_consumption(self):
        rows = self.run_service("ack")
        final = next(row for row in rows if row["event"] == "input-flow-ended")
        self.assertEqual((final["issued"], final["acknowledged"]), (9, 3))
        self.assertFalse(final["kernelBacklogMeasured"])

    def test_eof_releases_a_full_window(self): self.run_service("eof")

    def test_owner_death_is_observed_while_physical_reads_are_paused(self):
        rows = self.run_service("death")
        self.assertIn("Owner exited", next(row for row in rows if row["event"] == "lease-ended")["reason"])

    def test_wrong_lease_ack_closes_without_granting(self):
        rows = self.run_service("invalid")
        self.assertIn("Invalid input credit", next(row for row in rows if row["event"] == "lease-ended")["reason"])

    def test_another_peer_cannot_acknowledge_the_exact_current_lease(self):
        rows = self.run_service("foreign")
        final = next(row for row in rows if row["event"] == "input-flow-ended")
        self.assertEqual((final["issued"], final["acknowledged"]), (8, 0))
        self.assertFalse(any(row["event"] == "lease-ended" for row in rows), "Rejecting the foreign peer must not end the owner lease")

    def test_unresponsive_consumer_reaches_bounded_failure_with_cleanup(self):
        rows = self.run_service("timeout")
        self.assertIn("five seconds", next(row for row in rows if row["event"] == "lease-ended")["reason"])


if __name__ == "__main__": unittest.main()
