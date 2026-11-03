import unittest
import zlib
from unittest.mock import Mock, patch
from types import SimpleNamespace
from run_hidapi_windows import after_api, final_capture_summary, prime_and_clear, prime_observed


class RuntimeTests(unittest.TestCase):
    def test_rgb_precondition_requires_new_crc_valid_marked_output(self):
        marker = bytes.fromhex('a1b2c3')
        packet = bytearray(78); packet[0] = 0x31; packet[2] = 0x10
        packet[4] = 4; packet[47:50] = marker
        packet[-4:] = zlib.crc32(bytes([0xa2]) + packet[:-4]).to_bytes(4, 'little')
        row = dict(kind='output', monotonicNs=101, hex=packet.hex())
        self.assertTrue(prime_observed([row],100,marker))
        self.assertFalse(prime_observed([],100,marker))
        self.assertFalse(prime_observed([row],102,marker))
        self.assertFalse(prime_observed([row],100,bytes(3)))
        packet[-1] ^= 1
        with self.assertRaisesRegex(RuntimeError, 'CRC mismatch'):
            prime_observed([row | {'hex':packet.hex()}],100,marker)

    def test_repeated_clear_is_preceded_by_a_distinct_observed_rgb_change(self):
        record = dict(startedMonotonicNs=100)
        session = SimpleNamespace(ready=dict(hidrawPath='/dev/hidraw1', uhidEventsPath='owned-events'),
            command=Mock(return_value=(record,b'')), save=Mock())
        with patch('run_hidapi_windows.read_rows', return_value=[]), \
             patch('run_hidapi_windows.prime_observed', return_value=True), \
             patch('run_hidapi_windows.clear_sony_state') as clear:
            prime_and_clear(session, 'hidapi-south')
        report = bytes.fromhex(session.command.call_args.args[1][-1])
        self.assertEqual(report[:3],bytes([2,0,4]))
        self.assertEqual(report[45:],bytes.fromhex('a1b2c3'))
        self.assertTrue(record['preparationRgbObserved'])
        clear.assert_called_once_with(session,'hidapi-south-clear')
        with patch('run_hidapi_windows.read_rows', return_value=[]), \
             patch('run_hidapi_windows.time.monotonic', side_effect=[0,3]), \
             patch('run_hidapi_windows.clear_sony_state') as clear, self.assertRaises(RuntimeError):
            prime_and_clear(session, 'hidapi-south')
        clear.assert_not_called()

    def test_library_success_preserves_same_original_win32_failure(self):
        original = RuntimeError('Win32 returned 47, required 48')
        def primary(): raise original
        with patch('run_hidapi_windows.run_cases') as cases, self.assertRaises(RuntimeError) as caught:
            after_api(primary, Mock(), 'hidapi.exe', Mock())
        self.assertIs(caught.exception, original); cases.assert_called_once()

    def test_library_failure_does_not_replace_first_api_failure(self):
        original = RuntimeError('Win32 feature error')
        def primary(): raise original
        with patch('run_hidapi_windows.run_cases', side_effect=RuntimeError('HIDAPI failure')):
            with self.assertRaises(RuntimeError) as caught: after_api(primary, Mock(), 'hidapi.exe', Mock())
        self.assertIs(caught.exception, original)

    def test_disabled_observer_preserves_primary_return_and_no_extra_calls(self):
        with patch('run_hidapi_windows.run_cases') as cases:
            self.assertEqual(after_api(lambda: 7, Mock(), None, Mock()), 7)
        cases.assert_not_called()

    def test_failed_neutral_callback_cannot_mask_original_api_error(self):
        original = RuntimeError('original count failure'); reset = RuntimeError('core actor exited')
        def primary(): raise original
        session = SimpleNamespace(result={}, save=Mock())
        with patch('run_hidapi_windows.run_cases'), self.assertRaises(RuntimeError) as caught:
            after_api(primary, session, 'hidapi.exe', Mock(side_effect=reset))
        self.assertIs(caught.exception, original)
        self.assertIn('core actor exited', session.result['hidapiWindowsResetError'])
        with patch('run_hidapi_windows.run_cases'), self.assertRaises(RuntimeError) as caught:
            after_api(lambda: None, session, 'hidapi.exe', Mock(side_effect=reset))
        self.assertIs(caught.exception, reset)

    def test_final_raw_drain_and_cleanup_required_for_forwarding(self):
        observer = dict(result='passed', cases=[dict(passed=True,evidence=dict(outputReportForwarded=True)) for _ in range(2)])
        result = dict(hidapiWindows=observer, ready=dict(ownedGuestToken='guest'),
            accountProcessesAndFilesRemoved=True, cleanup=[dict(aclRestored=True)])
        stopped = dict(completed=True, cleanupConfirmed=True, evidenceOverflow=False,
            rawOutputObservation=dict(rejectedSnapshots=0,unwrittenSnapshots=0,error=None))
        self.assertTrue(final_capture_summary(result,stopped,'guest')['reportForwardingVerified'])
        for change in ({'rawOutputObservation':{}}, {'rawOutputObservation':dict(rejectedSnapshots=False,unwrittenSnapshots=0,error=None)},
                       {'rawOutputObservation':dict(rejectedSnapshots=0,unwrittenSnapshots=1,error=None)}, {'cleanupConfirmed':False}):
            self.assertFalse(final_capture_summary(result,stopped | change,'guest')['reportForwardingVerified'])
        self.assertFalse(final_capture_summary(result | {'cleanupErrors':['ACL']},stopped,'guest')['reportForwardingVerified'])
        self.assertFalse(final_capture_summary(result,stopped,'other')['reportForwardingVerified'])


if __name__ == '__main__': unittest.main()
