"""Synthetic desktop-adapter policy tests; never open a GUI or launch Wine."""
import contextlib
import copy
from pathlib import Path
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import session_restart as policy


def failed_summary():
    return {
        'schema_version': 1,
        'session_id': '20000101T000000Z-12345678',
        'status': 'failed',
        'phase': 'failed',
        'exit_code': 1,
        'cleanup_complete': True,
        'ended_at': '2000-01-01T00:01:00+00:00',
        'updated_at': '2000-01-01T00:01:00+00:00',
        'starter_pids': [],
        'handoff_pending': False,
        'child_exited': True,
    }


class PolicyTestCase(unittest.TestCase):
    def setUp(self):
        execute = mock.patch.object(policy.subprocess, 'run',
                                    return_value=types.SimpleNamespace(returncode=1))
        self.execute = execute.start()
        self.addCleanup(execute.stop)
        spawn = mock.patch.object(policy.subprocess, 'Popen',
                                  side_effect=AssertionError('No application process'))
        self.spawn = spawn.start()
        self.addCleanup(spawn.stop)

    def tearDown(self):
        self.spawn.assert_not_called()


class ClassificationTests(PolicyTestCase):
    def test_clean_finish_and_finalized_failure_have_distinct_states(self):
        failed = failed_summary()
        original = copy.deepcopy(failed)
        self.assertEqual(policy.stopped_session_state(failed), 'inactive-failed')
        self.assertEqual(failed, original)
        clean = dict(failed, status='finished', phase='finished', exit_code=0)
        self.assertEqual(policy.stopped_session_state(clean), 'inactive')
        self.execute.assert_not_called()

    def test_normal_finish_keeps_legacy_minimal_contract(self):
        self.assertEqual(policy.stopped_session_state({
            'schema_version': 1, 'status': 'finished', 'exit_code': 0,
            'cleanup_complete': True,
        }), 'inactive')

    def test_invalid_result_types_and_unfinished_sessions_refuse(self):
        changes = [
            ('schema_version', True), ('schema_version', '1'), ('schema_version', 2),
            ('status', 'running'), ('status', 'unknown'), ('phase', 'monitoring'),
            ('exit_code', True), ('exit_code', '1'), ('exit_code', 0),
            ('cleanup_complete', False), ('cleanup_complete', 1),
            ('starter_pids', [123]), ('starter_pids', None),
            ('handoff_pending', True), ('handoff_pending', 0),
            ('child_exited', False), ('child_exited', 1),
        ]
        for field, value in changes:
            with self.subTest(field=field, value=value):
                with self.assertRaises(RuntimeError):
                    policy.stopped_session_state(dict(failed_summary(), **{field: value}))
        self.execute.assert_not_called()

    def test_missing_fields_and_nonobject_records_refuse(self):
        for field in failed_summary():
            if field == 'updated_at':
                continue
            summary = failed_summary()
            del summary[field]
            with self.subTest(missing=field), self.assertRaises(RuntimeError):
                policy.stopped_session_state(summary)
        for value in (None, [], 'failed', True, 1):
            with self.subTest(summary=value), self.assertRaises(RuntimeError):
                policy.stopped_session_state(value)

    def test_failure_needs_generated_identifier_and_timezone_aware_end(self):
        changes = [
            ('session_id', ''), ('session_id', 'unverified-session'),
            ('session_id', '../20000101T000000Z-12345678'),
            ('session_id', '20000101T000000Z-12345678\n'),
            ('ended_at', ''), ('ended_at', None), ('ended_at', 'invalid'),
            ('ended_at', '2000-01-01'), ('ended_at', '2000-01-01T00:01:00'),
        ]
        for field, value in changes:
            with self.subTest(field=field, value=value), self.assertRaises(RuntimeError):
                policy.stopped_session_state(dict(failed_summary(), **{field: value}))


class ConfirmationTests(PolicyTestCase):
    def setUp(self):
        super().setUp()
        self.summary = failed_summary()
        self.events = []
        self.held = False

        @contextlib.contextmanager
        def inactive_lock():
            self.assertFalse(self.held)
            self.held = True
            self.events.append('locked')
            try:
                yield
            finally:
                self.held = False
                self.events.append('unlocked')

        def read_summary():
            self.assertTrue(self.held)
            self.events.append('read')
            return copy.deepcopy(self.summary)

        def verify_deployment():
            self.assertTrue(self.held)
            self.events.append('verified')

        def ask():
            self.assertFalse(self.held)
            self.events.append('asked')
            return 0

        self.lock = mock.Mock(side_effect=inactive_lock)
        self.read = mock.Mock(side_effect=read_summary)
        self.verify = mock.Mock(side_effect=verify_deployment)
        self.ask = mock.Mock(side_effect=ask)

    def confirm(self):
        return policy.confirm_failed_session_start(
            inactive_lock=self.lock, read_summary=self.read,
            verify_deployment=self.verify, ask=self.ask)

    def test_accept_checks_inactivity_twice_with_prompt_outside_lock(self):
        self.assertTrue(self.confirm())
        self.assertEqual(self.events, [
            'locked', 'read', 'verified', 'unlocked', 'asked',
            'locked', 'read', 'unlocked',
        ])
        self.verify.assert_called_once_with()
        self.ask.assert_called_once_with()
        self.execute.assert_not_called()

    def test_cancel_does_not_recheck_or_change_summary(self):
        original = copy.deepcopy(self.summary)
        self.ask.side_effect = lambda: 1
        self.assertFalse(self.confirm())
        self.assertEqual(self.summary, original)
        self.lock.assert_called_once_with()
        self.read.assert_called_once_with()
        self.verify.assert_called_once_with()
        self.execute.assert_not_called()

    def test_dialog_error_signal_or_invalid_return_type_refuses(self):
        for code in (2, -15, None, '0', True, False):
            with self.subTest(returncode=code):
                self.ask.side_effect = lambda code=code: code
                with self.assertRaises(RuntimeError):
                    self.confirm()
        self.execute.assert_not_called()

    def test_dialog_execution_error_propagates_without_second_read(self):
        self.ask.side_effect = OSError('Synthetic dialog failure')
        with self.assertRaises(OSError):
            self.confirm()
        self.read.assert_called_once_with()
        self.lock.assert_called_once_with()

    def test_busy_or_unknown_inactivity_prevents_prompt(self):
        for error in (BlockingIOError('Busy'), RuntimeError('Ownership unknown')):
            with self.subTest(error=type(error).__name__):
                self.lock.side_effect = error
                with self.assertRaises(type(error)):
                    self.confirm()
        self.read.assert_not_called()
        self.verify.assert_not_called()
        self.ask.assert_not_called()

    def test_deployment_or_summary_read_failure_prevents_prompt(self):
        self.verify.side_effect = RuntimeError('Synthetic hash mismatch')
        with self.assertRaisesRegex(RuntimeError, 'hash mismatch'):
            self.confirm()
        self.ask.assert_not_called()
        self.verify.reset_mock()
        self.read.side_effect = RuntimeError('Unsafe summary metadata')
        with self.assertRaisesRegex(RuntimeError, 'Unsafe summary'):
            self.confirm()
        self.verify.assert_not_called()
        self.ask.assert_not_called()

    def test_normal_or_unfinished_session_does_not_get_failure_prompt(self):
        for summary in (dict(failed_summary(), status='finished', exit_code=0),
                        dict(failed_summary(), cleanup_complete=False),
                        dict(failed_summary(), child_exited=False)):
            with self.subTest(summary=summary):
                self.summary = summary
                with self.assertRaises(RuntimeError):
                    self.confirm()
        self.verify.assert_not_called()
        self.ask.assert_not_called()

    def test_changed_session_or_summary_invalidates_accepted_choice(self):
        for changed in (dict(failed_summary(), session_id='20000101T000100Z-87654321'),
                        dict(failed_summary(), updated_at='2000-01-01T00:02:00+00:00'),
                        dict(failed_summary(), status='running', phase='startup')):
            with self.subTest(changed=changed):
                self.read.side_effect = [failed_summary(), changed]
                with self.assertRaisesRegex(RuntimeError, 'changed'):
                    self.confirm()

    def test_busy_after_accept_refuses_choice(self):
        self.lock.side_effect = [contextlib.nullcontext(), BlockingIOError('New session busy')]
        self.read.side_effect = None
        self.read.return_value = failed_summary()
        self.verify.side_effect = None
        self.ask.side_effect = None
        self.ask.return_value = 0
        with self.assertRaises(BlockingIOError):
            self.confirm()
        self.read.assert_called_once_with()
        self.ask.assert_called_once_with()

    def test_all_callbacks_are_explicit_without_an_implicit_gui(self):
        with self.assertRaises(TypeError):
            policy.confirm_failed_session_start(
                inactive_lock=self.lock, read_summary=self.read,
                verify_deployment=self.verify)
        self.execute.assert_not_called()


class DialogTests(PolicyTestCase):
    def test_default_cancel_and_explicit_labels_use_no_shell(self):
        for code in (0, 1, 2, -15):
            with self.subTest(returncode=code):
                self.execute.return_value = types.SimpleNamespace(returncode=code)
                self.assertEqual(policy.ask_start_new_session(), code)
        command = self.execute.call_args.args[0]
        self.assertEqual(command[0], '/usr/bin/zenity')
        for option in ('--question', '--no-markup', '--default-cancel',
                       '--ok-label=Start a new session', '--cancel-label=Cancel'):
            self.assertIn(option, command)
        options = self.execute.call_args.kwargs
        self.assertEqual(options['stdin'], policy.subprocess.DEVNULL)
        self.assertIs(options['check'], False)
        self.assertFalse(options.get('shell', False))

    def test_missing_zenity_propagates_without_fallback_or_retry(self):
        self.execute.side_effect = FileNotFoundError('Synthetic missing dialog')
        with self.assertRaises(FileNotFoundError):
            policy.ask_start_new_session()
        self.execute.assert_called_once()


if __name__ == '__main__':
    unittest.main(verbosity=2)
