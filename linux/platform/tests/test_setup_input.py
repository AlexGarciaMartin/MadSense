"""Acquisition history must be drained before ACTIVE, without relaxing active queues."""
import errno
import io
import json
import socket
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import broker


def empty():
    return BlockingIOError(errno.EAGAIN, "No queued report")


class SetupInputTests(unittest.TestCase):
    def lease(self):
        lease = broker.Lease.__new__(broker.Lease)
        lease.fd, lease.pid, lease.active = 123, 7, False
        lease.alive, lease.start_audit = MagicMock(), MagicMock()
        return lease

    def test_empty_nonblocking_stream_needs_no_discard(self):
        with patch.object(broker.os, "read", side_effect=empty()) as read:
            self.assertEqual(broker.drain_setup_input(123), 0)
        read.assert_called_once_with(123, broker.MAX_REPORT)

    def test_backlog_is_counted_and_active_is_published_only_after_eagain(self):
        lease = self.lease()
        reports = iter([b"first", b"second", b"third", empty()])
        def read(fd, size):
            self.assertFalse(lease.active)
            lease.start_audit.assert_not_called()
            value = next(reports)
            if isinstance(value, Exception):
                raise value
            return value
        output = io.StringIO()
        with patch.object(broker.os, "read", side_effect=read) as reader, redirect_stdout(output):
            self.assertEqual(lease.finish_input_setup(), 3)
        self.assertEqual(reader.call_count, 4)
        self.assertTrue(lease.active)
        lease.alive.assert_called_once_with()
        lease.start_audit.assert_called_once_with()
        self.assertEqual(json.loads(output.getvalue()),
                         {"event": "lease-acquired", "ownerPid": 7, "setupReportsDrained": 3})

    def test_report_bound_fails_instead_of_activating_a_continuously_readable_stream(self):
        lease = self.lease()
        output = io.StringIO()
        with patch.object(broker.os, "read", return_value=b"report") as reader, \
             patch.object(broker.time, "monotonic", return_value=1), \
             redirect_stdout(output), self.assertRaises(broker.BrokerError) as error:
            lease.finish_input_setup()
        self.assertEqual(error.exception.code, "input_setup_failed")
        self.assertEqual(reader.call_count, 128)
        self.assertFalse(lease.active)
        lease.alive.assert_not_called()
        lease.start_audit.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())["setupReportsDrained"], 128)

    def test_time_bound_stops_before_another_read_and_does_not_activate(self):
        lease = self.lease()
        output = io.StringIO()
        with patch.object(broker.os, "read", return_value=b"report") as reader, \
             patch.object(broker.time, "monotonic", side_effect=[1, 1.001, 1.051]), \
             redirect_stdout(output), self.assertRaises(broker.BrokerError) as error:
            lease.finish_input_setup()
        self.assertEqual(error.exception.code, "input_setup_failed")
        self.assertEqual(reader.call_count, 1)
        self.assertFalse(lease.active)
        lease.start_audit.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())["setupReportsDrained"], 1)

    def test_eagain_observed_after_deadline_is_not_success(self):
        lease = self.lease()
        with patch.object(broker.os, "read", side_effect=empty()), \
             patch.object(broker.time, "monotonic", side_effect=[1, 1.001, 1.051]), \
             redirect_stdout(io.StringIO()), self.assertRaises(broker.BrokerError) as error:
            lease.finish_input_setup()
        self.assertEqual(error.exception.code, "input_setup_failed")
        self.assertFalse(lease.active)
        lease.start_audit.assert_not_called()

    def test_eof_preserves_observed_discard_count_and_does_not_activate(self):
        lease = self.lease()
        output = io.StringIO()
        with patch.object(broker.os, "read", side_effect=[b"old", b""]), \
             redirect_stdout(output), self.assertRaises(broker.BrokerError) as error:
            lease.finish_input_setup()
        self.assertEqual(error.exception.code, "isolation_lost")
        self.assertFalse(lease.active)
        lease.start_audit.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())["setupReportsDrained"], 1)

    def test_device_error_is_not_treated_as_an_empty_stream(self):
        lease = self.lease()
        with patch.object(broker.os, "read", side_effect=OSError(errno.ENODEV, "Gone")), \
             redirect_stdout(io.StringIO()), self.assertRaises(OSError) as error:
            lease.finish_input_setup()
        self.assertEqual(error.exception.errno, errno.ENODEV)
        self.assertFalse(lease.active)
        lease.start_audit.assert_not_called()

    def test_other_blocking_error_is_not_eagain(self):
        with patch.object(broker.os, "read", side_effect=BlockingIOError(errno.EBUSY, "Busy")), \
             redirect_stdout(io.StringIO()), self.assertRaises(broker.BrokerError) as error:
            broker.drain_setup_input(123)
        self.assertEqual(error.exception.code, "input_setup_failed")

    def test_owner_loss_after_drain_prevents_active(self):
        lease = self.lease()
        lease.alive.side_effect = broker.BrokerError("owner_lost", "Owner closed")
        output = io.StringIO()
        with patch.object(broker.os, "read", side_effect=[b"old", empty()]), \
             redirect_stdout(output), self.assertRaises(broker.BrokerError) as error:
            lease.finish_input_setup()
        self.assertEqual(error.exception.code, "owner_lost")
        self.assertFalse(lease.active)
        lease.start_audit.assert_not_called()
        self.assertNotIn("lease-acquired", output.getvalue())
        self.assertEqual(json.loads(output.getvalue())["setupReportsDrained"], 1)

    @unittest.skipUnless(sys.platform == "linux", "os.read on Unix datagram descriptor requires Linux")
    def test_real_nonblocking_packet_fd_drains_history_but_keeps_later_input(self):
        receiver, sender = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
        try:
            receiver.setblocking(False)
            for report in (b"one", b"two", b"three"):
                sender.send(report)
            self.assertEqual(broker.drain_setup_input(receiver.fileno()), 3)
            sender.send(b"active")
            self.assertEqual(broker.os.read(receiver.fileno(), broker.MAX_REPORT), b"active")
        finally:
            receiver.close()
            sender.close()


if __name__ == "__main__":
    unittest.main()
