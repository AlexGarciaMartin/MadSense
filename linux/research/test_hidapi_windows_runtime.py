import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace
from run_hidapi_windows import after_api, final_capture_summary


class RuntimeTests(unittest.TestCase):
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
