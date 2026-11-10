"""Isolated interrupted-history checks; never inspect or operate the real app."""
import contextlib
import copy
import json
import os
from pathlib import Path
import tempfile
import sys
import unittest
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import interrupted_session as tool


def failed_summary():
    return {'schema_version': 1, 'session_id': '20010102T030405Z-0123abcd',
            'started_at': '2001-01-02T03:04:05.100+00:00',
            'updated_at': '2001-01-02T03:10:00.200+00:00',
            'ended_at': '2001-01-02T03:10:00.100+00:00',
            'status': 'failed', 'phase': 'failed', 'exit_code': 143,
            'supervisor_pid': 123, 'child_pid': 456, 'window_mode': 'integrated',
            'starter_pids': [789], 'child_exited': False, 'handoff_pending': False,
            'cleanup_complete': False, 'child_exit_code': -15, 'child_final_exit_code': -15,
            'child_signal': 15, 'child_exit_unexpected': True, 'cleanup_sent_child_sigterm': False}


def running_summary():
    result = failed_summary()
    for key in ('ended_at', 'exit_code', 'cleanup_complete', 'child_exit_code', 'child_final_exit_code',
                'child_signal', 'child_exit_unexpected', 'cleanup_sent_child_sigterm'):
        result.pop(key)
    result.update(status='running', phase='running')
    return result


class EligibilityTests(unittest.TestCase):
    def test_external_term_and_unfinished_running_are_history_candidates(self):
        self.assertTrue(tool.eligible(failed_summary()))
        self.assertTrue(tool.eligible(running_summary()))

    def test_running_known_monitor_phases_and_optional_false_cleanup(self):
        for phase in ('startup', 'starting', 'running', 'stopping'):
            with self.subTest(phase=phase):
                self.assertTrue(tool.eligible(dict(running_summary(), phase=phase, cleanup_complete=False)))

    def test_missing_or_malformed_common_identity_refuses(self):
        changes = [('schema_version', True), ('schema_version', 2), ('session_id', '../bad'),
                   ('session_id', '20010102T030405Z-0123abcd\n'), ('window_mode', 'nested'),
                   ('started_at', '2001-01-02'), ('updated_at', '2001-01-01T01:00:00+00:00'),
                   ('supervisor_pid', True), ('child_pid', 0), ('child_pid', 2147483648),
                   ('starter_pids', [True]), ('starter_pids', [7, 7]), ('starter_pids', '7'),
                   ('child_exited', 0), ('handoff_pending', None), ('child_exit_code', True),
                   ('child_final_exit_code', 0), ('child_signal', True), ('child_signal', 0),
                   ('child_exit_unexpected', 1), ('cleanup_sent_child_sigterm', 0)]
        for key, value in changes:
            with self.subTest(key=key, value=value):
                self.assertFalse(tool.eligible(dict(failed_summary(), **{key: value})))
        for key in ('schema_version', 'session_id', 'window_mode', 'started_at', 'updated_at',
                    'supervisor_pid', 'child_pid', 'starter_pids', 'child_exited', 'handoff_pending'):
            with self.subTest(missing=key):
                summary = failed_summary()
                summary.pop(key)
                self.assertFalse(tool.eligible(summary))
        for value in (None, [], 'running', 1):
            self.assertFalse(tool.eligible(value))

    def test_finalized_failure_requires_actual_failed_incomplete_record(self):
        changes = [('status', 'finished'), ('phase', 'running'), ('exit_code', 0),
                   ('exit_code', True), ('cleanup_complete', True), ('cleanup_complete', None),
                   ('ended_at', '2001-01-02T03:10:00'),
                   ('ended_at', '2001-01-01T03:10:00+00:00'),
                   ('ended_at', '2001-01-03T03:10:00+00:00')]
        for key, value in changes:
            with self.subTest(key=key, value=value):
                self.assertFalse(tool.eligible(dict(failed_summary(), **{key: value})))

    def test_running_must_not_masquerade_as_finalized_or_unknown_state(self):
        for key, value in (('exit_code', 1), ('ended_at', None), ('phase', 'failed'),
                           ('cleanup_complete', True), ('cleanup_complete', 0)):
            with self.subTest(key=key, value=value):
                self.assertFalse(tool.eligible(dict(running_summary(), **{key: value})))


class GuardedFilesTests(unittest.TestCase):
    def setUp(self):
        uid_patch = mock.patch.object(tool.os, 'getuid', return_value=1001)
        uid_patch.start()
        self.addCleanup(uid_patch.stop)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.app = self.root / 'app'
        self.latest = self.app / 'logs/latest-session.json'
        self.summary = failed_summary()
        self.history = self.app / 'logs/sessions' / self.summary['session_id'] / 'summary.json'
        self.ledger_path = self.app / 'logs/session-processes.json'
        self.ledger = {'version': 1, 'uid': os.getuid(), 'prefix': str(self.app / 'prefix'),
                       'window_mode': 'integrated', 'token': 'a' * 32,
                       'processes': [{'pid': 456, 'uid': os.getuid(), 'start_ticks': 1000},
                                     {'pid': 789, 'uid': os.getuid(), 'start_ticks': 2000}]}
        self.write(self.latest, self.summary)
        self.write(self.history, self.summary)
        self.write(self.ledger_path, self.ledger)
        self.held = False
        self.entries = 0
        self.lock = mock.Mock(side_effect=self.inactive_lock)
        self.read_json = mock.Mock(side_effect=self.safe_json)
        self.deployment = {'runtime_manifest_sha256': 'b' * 64, 'transaction_sha256': 'c' * 64}
        self.verify = mock.Mock(side_effect=self.verify_deployment)
        self.backends = dict(app=self.app, inactive_lock=self.lock, read_json=self.read_json,
                             verify_deployment=self.verify)
        for name in ('kill', 'pidfd_open'):
            patcher = mock.patch.object(tool.os, name, side_effect=AssertionError('No real process action'))
            patcher.start()
            self.addCleanup(patcher.stop)

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, sort_keys=True, indent=2) + '\n')
        path.chmod(0o600)

    @contextlib.contextmanager
    def inactive_lock(self):
        self.assertFalse(self.held)
        self.held = True
        self.entries += 1
        try:
            yield
        finally:
            self.held = False

    def safe_json(self, path):
        self.assertTrue(self.held)
        self.assertTrue(path.is_relative_to(self.root))
        if path.is_symlink():
            raise RuntimeError('Redirected test proof')
        return json.loads(path.read_bytes())

    def verify_deployment(self):
        self.assertTrue(self.held)
        return copy.deepcopy(self.deployment)

    def test_recognition_checks_current_full_lock_and_deployment_without_action_or_write(self):
        before = {path: path.read_bytes() for path in (self.latest, self.history, self.ledger_path)}
        paths_before = set(self.root.rglob('*'))
        self.assertEqual(tool.recognize(**self.backends), 'inactive-interrupted')
        self.assertEqual(self.entries, 1)
        self.verify.assert_called_once()
        self.assertEqual(before, {path: path.read_bytes() for path in before})
        self.assertEqual(set(self.root.rglob('*')), paths_before)

    def test_explicit_start_validation_repeats_two_full_locks_and_deployment_without_actions(self):
        self.assertTrue(tool.validate_start(**self.backends))
        self.assertEqual(self.entries, 2)
        self.assertEqual(self.verify.call_count, 2)

    def test_running_unfinished_record_uses_same_fresh_guarded_route(self):
        self.write(self.latest, running_summary())
        self.write(self.history, running_summary())
        self.assertTrue(tool.validate_start(**self.backends))
        self.assertEqual(self.entries, 2)

    def test_busy_or_unknown_ownership_refuses_before_reading_history_or_verification(self):
        for error in (BlockingIOError('Busy'), RuntimeError('Unknown prefix ownership'),
                      RuntimeError('Shared runtime active')):
            with self.subTest(error=error):
                self.lock.side_effect = error
                with self.assertRaises(type(error)):
                    tool.validate_start(**self.backends)
        self.read_json.assert_not_called()
        self.verify.assert_not_called()

    def test_root_and_noncanonical_application_refuse_before_reading_history(self):
        with mock.patch.object(tool.os, 'getuid', return_value=0):
            with self.assertRaises(RuntimeError):
                tool.validate_start(**self.backends)
        alias = self.root / 'app-alias'
        alias.symlink_to(self.app, target_is_directory=True)
        for app in (Path('relative-installation'), alias, self.app / '..' / 'app'):
            with self.subTest(app=app), self.assertRaises(RuntimeError):
                tool.validate_start(**dict(self.backends, app=app))
        self.read_json.assert_not_called()
        self.verify.assert_not_called()

    def test_second_full_lock_becoming_busy_refuses(self):
        self.lock.side_effect = [self.inactive_lock(), BlockingIOError('New session started')]
        with self.assertRaises(BlockingIOError):
            tool.validate_start(**self.backends)
        self.verify.assert_called_once()

    def test_mismatched_latest_and_history_content_or_bytes_refuses(self):
        self.history.write_text(json.dumps(self.summary))
        with self.assertRaises(RuntimeError):
            tool.validate_start(**self.backends)
        self.write(self.history, dict(self.summary, child_exited=True))
        with self.assertRaises(RuntimeError):
            tool.validate_start(**self.backends)
        self.verify.assert_not_called()

    def test_missing_history_refuses(self):
        self.history.unlink()
        with self.assertRaises(FileNotFoundError):
            tool.validate_start(**self.backends)

    def test_unrecorded_child_or_starter_refuses(self):
        for removed in (456, 789):
            with self.subTest(removed=removed):
                ledger = copy.deepcopy(self.ledger)
                ledger['processes'] = [row for row in ledger['processes'] if row['pid'] != removed]
                self.write(self.ledger_path, ledger)
                with self.assertRaises(RuntimeError):
                    tool.validate_start(**self.backends)

    def test_invalid_ledger_identity_and_private_binding_refuses(self):
        changes = [('version', True), ('uid', True), ('uid', os.getuid() + 1),
                   ('prefix', '/other/prefix'), ('window_mode', 'nested'), ('token', 'bad'),
                   ('processes', []), ('processes', 'bad')]
        for key, value in changes:
            with self.subTest(key=key, value=value):
                self.write(self.ledger_path, dict(self.ledger, **{key: value}))
                with self.assertRaises(RuntimeError):
                    tool.validate_start(**self.backends)
        for key, value in (('pid', True), ('pid', 0), ('uid', True), ('uid', os.getuid() + 1),
                           ('start_ticks', True), ('start_ticks', -1)):
            with self.subTest(identity_field=key, value=value):
                ledger = copy.deepcopy(self.ledger)
                ledger['processes'][0][key] = value
                self.write(self.ledger_path, ledger)
                with self.assertRaises(RuntimeError):
                    tool.validate_start(**self.backends)
        ledger = copy.deepcopy(self.ledger)
        ledger['processes'].append(copy.deepcopy(ledger['processes'][0]))
        self.write(self.ledger_path, ledger)
        with self.assertRaises(RuntimeError):
            tool.validate_start(**self.backends)

    def test_history_ledger_or_deployment_change_between_locks_refuses(self):
        for kind in ('history', 'ledger', 'deployment'):
            with self.subTest(kind=kind):
                self.write(self.latest, self.summary)
                self.write(self.history, self.summary)
                self.write(self.ledger_path, self.ledger)
                self.deployment = {'runtime_manifest_sha256': 'b' * 64}
                count = 0

                @contextlib.contextmanager
                def changing_lock():
                    nonlocal count
                    count += 1
                    if count == 2:
                        if kind == 'history':
                            changed = dict(self.summary, updated_at='2001-01-02T03:10:01+00:00')
                            self.write(self.latest, changed)
                            self.write(self.history, changed)
                        elif kind == 'ledger':
                            self.write(self.ledger_path, dict(self.ledger, token='d' * 32))
                        else:
                            self.deployment = {'runtime_manifest_sha256': 'e' * 64}
                    with self.inactive_lock():
                        yield

                self.lock.side_effect = changing_lock
                with self.assertRaisesRegex(RuntimeError, 'changed'):
                    tool.validate_start(**self.backends)

    def test_snapshot_changing_during_deployment_verification_refuses(self):
        def changing_verify():
            value = self.verify_deployment()
            self.write(self.ledger_path, dict(self.ledger, token='f' * 32))
            return value

        self.verify.side_effect = changing_verify
        with self.assertRaises(RuntimeError):
            tool.validate_start(**self.backends)
        self.assertEqual(self.entries, 1)

    def test_bytes_race_between_safe_json_and_read_is_refused(self):
        original = self.read_json.side_effect
        changed = False

        def racing_read(path):
            nonlocal changed
            value = original(path)
            if path == self.latest and not changed:
                changed = True
                self.write(path, dict(self.summary, child_exited=True))
            return value

        self.read_json.side_effect = racing_read
        with self.assertRaises(RuntimeError):
            tool.validate_start(**self.backends)
        self.verify.assert_not_called()

    def test_deployment_refusal_and_invalid_result_never_authorize_start(self):
        self.verify.side_effect = RuntimeError('Runtime changed')
        with self.assertRaisesRegex(RuntimeError, 'Runtime changed'):
            tool.validate_start(**self.backends)
        for result in ({}, None, {'bad_number': float('nan')}):
            with self.subTest(result=result):
                self.verify.side_effect = None
                self.verify.return_value = result
                with self.assertRaises((RuntimeError, ValueError)):
                    tool.validate_start(**self.backends)

    def test_redirected_summary_and_application_are_refused(self):
        other = self.root / 'other.json'
        self.write(other, self.summary)
        self.latest.unlink()
        self.latest.symlink_to(other)
        with self.assertRaises(RuntimeError):
            tool.validate_start(**self.backends)
        link = self.root / 'root-link'
        link.symlink_to(self.app, target_is_directory=True)
        with self.assertRaises(RuntimeError):
            tool.validate_start(**dict(self.backends, app=link))


if __name__ == '__main__':
    unittest.main(verbosity=2)
