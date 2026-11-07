"""Failure-path checks using isolated prefixes; never launch the real trading app."""

import contextlib
import errno
import fcntl
import itertools
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import diagnostics
import supervisor


class LoggingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        previous = os.umask(0o077)
        self.addCleanup(os.umask, previous)
        platform = mock.patch.object(diagnostics.platform, 'platform', return_value='isolated test host')
        platform.start()
        self.addCleanup(platform.stop)

    def events(self):
        return [json.loads(line) for line in (self.base / 'logs/events.jsonl').read_text().splitlines()]

    def runtime(self):
        (self.base / 'prefix').mkdir()
        runner = self.base / 'runner-quantower/opt/wine-devel/bin'
        runner.mkdir(parents=True)
        for name in ('wine', 'wineserver'):
            (runner / name).touch()

    def test_rotation_handles_invalid_utf8_and_long_unterminated_output(self):
        log = diagnostics.SessionLog(self.base, text_bytes=16384, event_bytes=4096)
        payload = b'plain text\xff\x00\x1b[31mERROR\x1b[0m ' + b'x' * (300 * 1024) + b'\xffTAIL'
        log.output.write(payload)
        log.finish(1)
        files = list((self.base / 'logs').glob('launch.log*'))
        self.assertGreater(len(files), 1)
        self.assertLessEqual(len(files), diagnostics.BACKUPS + 1)
        for path in files:
            self.assertLessEqual(path.stat().st_size, 16384)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        text = '\n'.join(p.read_text() for p in files)
        self.assertIn('TAIL', text)
        self.assertNotIn('\x1b[', text)
        summary = json.loads((self.base / 'logs/latest-session.json').read_text())
        self.assertEqual(summary['exit_code'], 1)
        self.assertFalse(summary['output_reader_failed'])
        self.assertEqual(summary['status'], 'failed')

    def test_mirror_disk_full_does_not_stop_raw_capture(self):
        log = diagnostics.SessionLog(self.base)
        handler = log.handlers[0]
        writer_errors = []

        def write():
            try:
                log.output.write(b'error: simulated runtime output\n' * 10000)
            except BaseException as error:
                writer_errors.append(error)

        try:
            with mock.patch.object(handler, 'shouldRollover', return_value=False), \
                    mock.patch.object(handler.stream, 'write', side_effect=OSError(errno.ENOSPC, 'test disk full')):
                writer = threading.Thread(target=write, daemon=True)
                writer.start()
                writer.join(timeout=5)
                self.assertFalse(writer.is_alive(), 'runtime output blocked after a logging write failure')
                self.assertEqual(writer_errors, [])
                log.finish(0)
            self.assertGreater(handler.failures, 0)
            summary = json.loads((self.base / 'logs/latest-session.json').read_text())
            self.assertGreater(summary['logging_write_failures'], 0)
            self.assertFalse(summary['output_reader_failed'])
            self.assertEqual((log.directory / 'runtime.raw.log').read_bytes(),
                             b'error: simulated runtime output\n' * 10000)
        finally:
            if not log.output.closed:
                log.finish(1)

    def test_shutdown_wakes_reader_to_capture_final_appended_bytes(self):
        log = diagnostics.SessionLog(self.base)
        waiting = threading.Event()
        original_wait = log.stop_reader.wait

        def wait(timeout):
            waiting.set()
            return original_wait(timeout)

        with mock.patch.object(log.stop_reader, 'wait', side_effect=wait):
            self.assertTrue(waiting.wait(timeout=2), 'reader never reached EOF wait')
            log.output.write(b'final append after EOF\n')
            log.finish(0)
        self.assertIn('final append after EOF', (self.base / 'logs/launch.log').read_text())
        self.assertEqual(log.output_bytes, len(b'final append after EOF\n'))

    def test_inherited_writer_survives_supervisor_output_shutdown(self):
        log = diagnostics.SessionLog(self.base)
        with os.fdopen(os.dup(log.output.fileno()), 'wb', buffering=0) as inherited:
            inherited.write(b'before supervisor exit\n')
            log.finish(0)
            # A pipe would raise BrokenPipeError here after the only reader closes.
            inherited.write(b'after supervisor exit\n')
        self.assertEqual((log.directory / 'runtime.raw.log').read_bytes(),
                         b'before supervisor exit\nafter supervisor exit\n')
        self.assertEqual((log.directory / 'runtime.raw.log').stat().st_mode & 0o777, 0o600)

    def test_reader_failure_does_not_break_inherited_writer(self):
        log = diagnostics.SessionLog(self.base)
        with mock.patch.object(log, '_runtime_line', side_effect=RuntimeError('test reader failure')):
            log.output.write(b'initial output\n')
            log.thread.join(timeout=2)
            self.assertFalse(log.thread.is_alive())
            self.assertTrue(log.reader_failed)
            log.output.write(b'output after reader failure\n')
        log.finish(0)
        self.assertTrue((log.directory / 'runtime.raw.log').read_bytes().endswith(b'output after reader failure\n'))
        event = next(e for e in self.events() if e['event'] == 'output_reader_failed')
        self.assertIn('RuntimeError: test reader failure', event['exception'])

    def test_lock_prevents_session_files_or_rotation(self):
        folder = self.base / 'logs'
        folder.mkdir()
        (folder / 'launch.log').write_text('existing log\n')
        with (folder / 'launch.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with mock.patch.object(diagnostics, 'SessionLog') as session_log:
                self.assertEqual(diagnostics.run(self.base), 0)
                session_log.assert_not_called()
        self.assertEqual((folder / 'launch.log').read_text(), 'existing log\n')
        self.assertFalse((folder / 'sessions').exists())
        self.assertFalse((folder / 'events.jsonl').exists())

    def test_missing_runtime_is_recorded_without_launching(self):
        with mock.patch.object(supervisor.subprocess, 'Popen') as start:
            self.assertEqual(diagnostics.run(self.base), 1)
            start.assert_not_called()
        self.assertTrue(any(e['event'] == 'runtime_missing' and e['level'] == 'ERROR'
                            for e in self.events()))

    def test_unhandled_supervisor_exception_has_traceback_and_final_summary(self):
        fake = types.SimpleNamespace(main=mock.Mock(side_effect=ValueError('test failure detail')))
        with mock.patch.dict(sys.modules, {'supervisor': fake}):
            self.assertEqual(diagnostics.run(self.base), 1)
        event = next(e for e in self.events() if e['event'] == 'launcher_exception')
        self.assertIn('Traceback', event['exception'])
        self.assertIn('ValueError: test failure detail', event['exception'])
        self.assertEqual(json.loads((self.base / 'logs/latest-session.json').read_text())['status'], 'failed')

    def test_renderer_failure_during_cleanup_marks_the_session_failed(self):
        def teardown_failure(*, base, diagnostics):
            diagnostics.event('child_final_status', 'Renderer crashed during teardown.',
                              level='ERROR', child_final_exit_code=-signal.SIGSEGV,
                              child_exit_unexpected=True)
            return 0
        fake = types.SimpleNamespace(main=teardown_failure)
        with mock.patch.dict(sys.modules, {'supervisor': fake}):
            self.assertEqual(diagnostics.run(self.base), 1)
        summary = json.loads((self.base / 'logs/latest-session.json').read_text())
        self.assertEqual(summary['status'], 'failed')
        self.assertEqual(summary['child_final_exit_code'], -signal.SIGSEGV)

    def test_deferred_cleanup_cannot_finalize_as_success(self):
        def deferred(*, base, diagnostics):
            diagnostics.event('cleanup_finished', 'Uncertain process ownership.', cleanup_complete=False)
            return 0
        with mock.patch.dict(sys.modules, {'supervisor': types.SimpleNamespace(main=deferred)}):
            self.assertEqual(diagnostics.run(self.base), 1)
        summary = json.loads((self.base / 'logs/latest-session.json').read_text())
        self.assertEqual(summary['status'], 'failed')
        self.assertFalse(summary['cleanup_complete'])

    def test_active_starter_preserved_without_preparation_or_cleanup(self):
        self.runtime()
        with mock.patch.object(supervisor, 'prefix_processes', return_value={456}), \
                mock.patch.object(supervisor, 'cleanup_session') as cleanup, \
                mock.patch.object(supervisor.profile_manager, 'prepare') as prepare, \
                mock.patch.object(supervisor.subprocess, 'Popen') as start:
            self.assertEqual(diagnostics.run(self.base), 0)
            cleanup.assert_not_called()
            prepare.assert_not_called()
            start.assert_not_called()
        self.assertTrue(any(e['event'] == 'existing_app_preserved' for e in self.events()))

    def test_cleanup_refuses_active_starter(self):
        session = mock.Mock()
        session.no_starter.return_value = False
        with mock.patch.object(supervisor.subprocess, 'run') as run:
            self.assertFalse(supervisor.cleanup_session(session, self.base, {}, None, mock.Mock()))
            run.assert_not_called()
            session.cleanup.assert_not_called()

    def test_child_exit_codes_signals_and_normal_exit(self):
        self.runtime()
        for status in (0, 17, -signal.SIGSEGV, -signal.SIGTERM):
            with self.subTest(status=status):
                log = diagnostics.SessionLog(self.base)
                session = mock.Mock(token='test-token', identities=set(), sent_signals=set())
                child = mock.Mock(pid=123, poll=mock.Mock(side_effect=[None, status, status]))
                with contextlib.ExitStack() as stack:
                    stack.enter_context(mock.patch.object(supervisor, 'SessionProcesses', return_value=session))
                    stack.enter_context(mock.patch.object(supervisor, 'prefix_processes', side_effect=[set(), set(), {456}]))
                    stack.enter_context(mock.patch.object(supervisor.profile_manager, 'load', return_value='gpu'))
                    stack.enter_context(mock.patch.object(supervisor.profile_manager, 'prepare'))
                    stack.enter_context(mock.patch.object(supervisor, 'cleanup_session', return_value=True))
                    start = stack.enter_context(mock.patch.object(supervisor.subprocess, 'Popen', return_value=child))
                    stack.enter_context(mock.patch.object(supervisor.signal, 'signal'))
                    stack.enter_context(mock.patch.object(supervisor.time, 'sleep'))
                    code = supervisor.main(base=self.base, diagnostics=log)
                    log.finish(code)
                self.assertEqual(code, 0 if status == 0 else 1)
                self.assertEqual(start.call_args.kwargs['env']['WINEDEBUG'], diagnostics.DEFAULT_WINEDEBUG)
                events = [e for e in self.events() if e['session_id'] == log.session_id]
                event = next(e for e in events if e['event'] == 'child_exited')
                self.assertEqual(event['child_exit_code'], status)
                self.assertEqual(event['child_signal'], -status if status < 0 else None)
                self.assertTrue(event['starter_observed'])
                final = next(e for e in events if e['event'] == 'child_final_status')
                self.assertEqual(final['child_exit_unexpected'], status != 0)
                # Prior-session discovery, initial child scan, then one Starter
                # identity scan; ordinary monitor iterations add no full scan.
                self.assertEqual(session.observe.call_count, 3)

    def test_final_sigterm_is_expected_only_if_cleanup_actually_sent_it(self):
        self.runtime()
        for sent in (True, False):
            with self.subTest(cleanup_sent_term=sent), contextlib.ExitStack() as stack:
                session = mock.Mock(token='test-token', identities=set(), sent_signals=set())
                identity = supervisor.ProcessIdentity(123, 1000, os.getuid())
                child = mock.Mock(pid=123, poll=mock.Mock(side_effect=[None, None, None, -signal.SIGTERM]))

                def cleanup(*args, **kwargs):
                    if sent:
                        session.sent_signals.add((identity, signal.SIGTERM))
                    return True

                stack.enter_context(mock.patch.object(supervisor, 'SessionProcesses', return_value=session))
                stack.enter_context(mock.patch.object(supervisor, 'prefix_processes',
                                                      side_effect=[set(), set(), {456}, set()]))
                stack.enter_context(mock.patch.object(supervisor.profile_manager, 'load', return_value='gpu'))
                stack.enter_context(mock.patch.object(supervisor.profile_manager, 'prepare'))
                stack.enter_context(mock.patch.object(supervisor, 'cleanup_session', side_effect=cleanup))
                stack.enter_context(mock.patch.object(supervisor.subprocess, 'Popen', return_value=child))
                stack.enter_context(mock.patch.object(supervisor.signal, 'signal'))
                stack.enter_context(mock.patch.object(supervisor.time, 'monotonic', side_effect=itertools.count(100, 10)))
                stack.enter_context(mock.patch.object(supervisor.time, 'sleep'))
                code = diagnostics.run(self.base)
                self.assertEqual(code, 0 if sent else 1)
                summary = json.loads((self.base / 'logs/latest-session.json').read_text())
                self.assertEqual(summary['child_exit_unexpected'], not sent)
                events = [e for e in self.events() if e['session_id'] == summary['session_id']]
                final = next(e for e in events if e['event'] == 'child_final_status')
                self.assertEqual(final['cleanup_sent_child_sigterm'], sent)

    def test_signal_telemetry_records_only_successful_verified_pidfd_send(self):
        session = supervisor.SessionProcesses(self.base / 'prefix', self.base / 'ledger.json')
        identity = supervisor.ProcessIdentity(123, 1000, os.getuid())
        with mock.patch.object(session, 'no_starter', return_value=True), \
                mock.patch.object(supervisor.os, 'pidfd_open', return_value=77), \
                mock.patch.object(supervisor.os, 'close'), \
                mock.patch.object(supervisor, 'owned_identity', return_value=identity), \
                mock.patch.object(supervisor.signal, 'pidfd_send_signal') as send:
            send.side_effect = ProcessLookupError()
            self.assertFalse(session.signal_identity(identity, signal.SIGTERM))
            self.assertEqual(session.sent_signals, set())
            send.side_effect = None
            self.assertTrue(session.signal_identity(identity, signal.SIGTERM))
            self.assertEqual(session.sent_signals, {(identity, signal.SIGTERM)})

    def test_profile_exception_detail_is_preserved(self):
        self.runtime()
        with mock.patch.object(supervisor, 'prefix_processes', return_value=set()), \
                mock.patch.object(supervisor.profile_manager, 'load', side_effect=RuntimeError('bad profile detail')), \
                mock.patch.object(supervisor.subprocess, 'Popen') as start:
            self.assertEqual(diagnostics.run(self.base), 1)
            start.assert_not_called()
        self.assertTrue(any('RuntimeError: bad profile detail' in e.get('exception', '')
                            for e in self.events()))

    def test_summary_disk_error_does_not_stop_supervision(self):
        log = diagnostics.SessionLog(self.base)
        with mock.patch.object(diagnostics, 'atomic_json', side_effect=OSError(errno.ENOSPC, 'summary disk full')):
            log.heartbeat(phase='running', child_pid=123, starter_pids=[456])
        log.finish(0)
        summary = json.loads((self.base / 'logs/latest-session.json').read_text())
        self.assertEqual(summary['summary_write_failures'], 1)
        self.assertEqual(summary['exit_code'], 0)
        self.assertTrue(any(e['event'] == 'summary_write_failed' for e in self.events()))

    def test_nonobject_prior_summary_does_not_prevent_startup(self):
        folder = self.base / 'logs'
        folder.mkdir()
        for value in ([], 'invalid summary object', None):
            with self.subTest(value=value):
                (folder / 'latest-session.json').write_text(json.dumps(value))
                log = diagnostics.SessionLog(self.base)
                log.finish(0)
                events = [e for e in self.events() if e['session_id'] == log.session_id]
                event = next(e for e in events if e['event'] == 'previous_summary_unreadable')
                self.assertIn('ValueError', event['exception'])

    def test_finished_event_preserves_original_error_cause(self):
        log = diagnostics.SessionLog(self.base)
        log.event('original_failure', 'specific useful cause', level='ERROR')
        log.finish(1)
        summary = json.loads((self.base / 'logs/latest-session.json').read_text())
        self.assertEqual(summary['last_error']['event'], 'original_failure')
        self.assertEqual(summary['last_error']['message'], 'specific useful cause')

    def test_starter_observation_is_saved_before_next_heartbeat(self):
        log = diagnostics.SessionLog(self.base)
        log.heartbeat(phase='starting', starter_pids=[])
        log.event('starter_observed', 'Starter appeared.', phase='running', starter_pids=[456])
        for path in (self.base / 'logs/latest-session.json', log.directory / 'summary.json'):
            summary = json.loads(path.read_text())
            self.assertEqual(summary['phase'], 'running')
            self.assertEqual(summary['starter_pids'], [456])
        log.event('cleanup_finished', 'Cleanup deferred.', cleanup_complete=False)
        log.finish(1)
        summary = json.loads((self.base / 'logs/latest-session.json').read_text())
        self.assertEqual(summary['phase'], 'failed')
        self.assertEqual(summary['starter_pids'], [456])
        self.assertFalse(summary['cleanup_complete'])

    def test_verified_disappearance_and_successful_cleanup_clear_starter_state(self):
        log = diagnostics.SessionLog(self.base)
        log.event('starter_observed', 'Starter appeared.', phase='running', starter_pids=[456])
        log.event('starter_disappeared', 'Starter disappeared.', phase='stopping', starter_pids=[])
        summary = json.loads((self.base / 'logs/latest-session.json').read_text())
        self.assertEqual(summary['starter_pids'], [])
        self.assertEqual(summary['phase'], 'stopping')
        # An application PID left over from the latest monitor snapshot is also
        # cleared when cleanup explicitly verifies completion.
        log.event('starter_observed', 'Another Starter observation.', phase='running', starter_pids=[789])
        log.event('cleanup_finished', 'Cleanup completed.', cleanup_complete=True)
        log.finish(0)
        summary = json.loads((self.base / 'logs/latest-session.json').read_text())
        self.assertEqual(summary['phase'], 'finished')
        self.assertEqual(summary['status'], 'finished')
        self.assertEqual(summary['starter_pids'], [])

    def test_retention_prunes_generated_sessions_preserving_legacy_and_ownership(self):
        preserved = (
            'logs/session-processes.json', 'logs/launch.lock',
            'logs/gpu/old-gpu.log', 'logs/native-assert/old-assert.log',
            'logs/runtime-profile-originals/user.reg', 'logs/support/old-support.tar.gz',
            'logs/sessions/manual-backup/keep.txt',
            'prefix/drive_c/AMPQuantowerInstalled/Logs/Serilog/old.slog',
        )
        for name in preserved:
            path = self.base / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('preserve this evidence')
        old = []
        for index in range(6):
            directory = self.base / 'logs/sessions' / f'20261001T000000Z-{index:08x}'
            directory.mkdir()
            (directory / 'summary.json').write_text('{"status":"finished","cleanup_complete":true}')
            timestamp = time.time() - (40 * 86400 if index == 0 else index * 100)
            os.utime(directory, (timestamp, timestamp))
            old.append(directory)
        with mock.patch.object(diagnostics, 'SESSION_KEEP', 4):
            log = diagnostics.SessionLog(self.base)
            log.finish(0)
        self.assertFalse(old[0].exists(), 'expired generated session was retained')
        self.assertEqual(sum(path.exists() for path in old), 3)
        for name in preserved:
            self.assertEqual((self.base / name).read_text(), 'preserve this evidence')

    def test_retention_limits_previous_session_bytes(self):
        old = []
        for index in range(2):
            directory = self.base / 'logs/sessions' / f'20261001T000000Z-{index:08x}'
            directory.mkdir(parents=True)
            (directory / 'gpu.log').write_bytes(b'123456')
            (directory / 'summary.json').write_text('{"status":"finished","cleanup_complete":true}')
            os.utime(directory, (time.time() - index * 100, time.time() - index * 100))
            old.append(directory)
        one_session_bytes = 6 + (old[0] / 'summary.json').stat().st_size
        with mock.patch.object(diagnostics, 'SESSION_BYTES', one_session_bytes + 1):
            log = diagnostics.SessionLog(self.base)
            log.finish(0)
        self.assertTrue(old[0].exists())
        self.assertFalse(old[1].exists())

    def test_retention_preserves_unknown_unfinished_and_deferred_sessions(self):
        summaries = (None, '[]', 'not json', '{"status":"running"}',
                     '{"status":"finished","cleanup_complete":false}',
                     '{"status":"failed","cleanup_complete":false}')
        old = []
        for index, summary in enumerate(summaries):
            directory = self.base / 'logs/sessions' / f'20261001T000000Z-{index:08x}'
            directory.mkdir(parents=True)
            (directory / 'runtime.raw.log').write_bytes(b'may still have a writer')
            if summary is not None:
                (directory / 'summary.json').write_text(summary)
            stamp = time.time() - 40 * 86400
            os.utime(directory, (stamp, stamp))
            old.append(directory)
        with mock.patch.object(diagnostics, 'SESSION_KEEP', 1), \
                mock.patch.object(diagnostics, 'SESSION_BYTES', 1):
            log = diagnostics.SessionLog(self.base)
            log.finish(0)
        for directory in old:
            self.assertEqual((directory / 'runtime.raw.log').read_bytes(), b'may still have a writer')


if __name__ == '__main__':
    unittest.main()
