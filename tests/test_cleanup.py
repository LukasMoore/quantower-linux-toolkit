"""Owned-session teardown ordering; all processes and signals are mocked."""

import os
from pathlib import Path
import signal
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import supervisor
import diagnostics


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.session = supervisor.SessionProcesses(self.base / 'prefix', self.base / 'ledger.json')
        self.compositor = supervisor.ProcessIdentity(101, 1001, os.getuid())
        self.xwayland = supervisor.ProcessIdentity(102, 1002, os.getuid())
        wine = mock.patch.object(self.session, 'wine_processes', return_value=set())
        wine.start()
        self.addCleanup(wine.stop)

    def test_unreadable_recorded_process_blocks_observation_without_losing_identity(self):
        identity = supervisor.ProcessIdentity(999999999, 1001, os.getuid())
        self.session.token = 'a' * 32
        self.session.identities = {identity}
        with mock.patch.object(Path, 'iterdir', return_value=[Path('/proc/999999999')]), \
                mock.patch.object(supervisor, 'owned_identity', side_effect=PermissionError('protected process')):
            with self.assertRaisesRegex(RuntimeError, 'Cannot verify a recorded session process'):
                self.session.observe()
        self.assertEqual(self.session.identities, {identity})
        self.assertEqual(self.session.sent_signals, set())

    def test_wine_selection_requires_exact_dedicated_loader_paths(self):
        unrelated = supervisor.ProcessIdentity(103, 1003, os.getuid())
        runtime = self.base / 'runner-quantower/opt/wine-devel'
        with mock.patch.object(Path, 'readlink', side_effect=[
                runtime / 'lib/wine/x86_64-unix/wine-preloader',
                Path(str(runtime / 'bin/wineserver') + ' (deleted)'),
                Path('/unrelated/wine-preloader')]):
            self.assertEqual(supervisor.SessionProcesses.wine_processes(
                self.session, [self.compositor, self.xwayland, unrelated]),
                {self.compositor, self.xwayland})

    def test_stubborn_wine_helper_stops_before_compositor_escalation(self):
        helper = supervisor.ProcessIdentity(103, 1003, os.getuid())
        current = {self.compositor, self.xwayland, helper}
        signals = []

        def send(identity, signum):
            signals.append((identity, signum))
            if identity == helper and signum == signal.SIGKILL:
                # Once its last Wine descendant exits, the reaper and compositor
                # finish their own normal shutdown; Xwayland needs no supervisor signal.
                current.clear()

        with mock.patch.object(self.session, 'no_starter', return_value=True), \
                mock.patch.object(self.session, 'observe', side_effect=lambda: set(current)), \
                mock.patch.object(self.session, 'compositors', return_value={self.compositor}), \
                mock.patch.object(self.session, 'wine_processes',
                                  side_effect=lambda identities: identities.intersection({helper})), \
                mock.patch.object(self.session, 'signal_identity', side_effect=send):
            self.assertTrue(self.session.cleanup(term_seconds=0))
        self.assertEqual(signals, [(self.compositor, signal.SIGTERM),
                                   (helper, signal.SIGTERM), (helper, signal.SIGKILL)])

    def test_helper_role_is_retained_even_without_a_compositor(self):
        helper = supervisor.ProcessIdentity(103, 1003, os.getuid())
        current = {helper}

        def send(identity, signum):
            if signum == signal.SIGKILL:
                current.remove(identity)

        with mock.patch.object(self.session, 'no_starter', return_value=True), \
                mock.patch.object(self.session, 'observe', side_effect=lambda: set(current)), \
                mock.patch.object(self.session, 'compositors', return_value=set()), \
                mock.patch.object(self.session, 'wine_processes',
                                  side_effect=lambda identities: identities.intersection({helper})), \
                mock.patch.object(self.session, 'signal_identity', side_effect=send):
            self.assertTrue(self.session.cleanup(term_seconds=0))
        self.assertEqual(self.session.wine_helpers, {helper})

    def test_compositor_selection_requires_exact_dedicated_executable(self):
        unrelated = supervisor.ProcessIdentity(103, 1003, os.getuid())
        expected = self.base / 'gamescope/files/bin/gamescope-brokey'
        with mock.patch.object(Path, 'readlink', side_effect=[
                expected, Path(str(expected) + ' (deleted)'),
                Path('/unrelated/gamescope-brokey')]):
            self.assertEqual(self.session.compositors([self.compositor, self.xwayland, unrelated]),
                             {self.compositor, self.xwayland})

    def test_compositor_can_stop_children_before_survivor_signals(self):
        current = {self.compositor, self.xwayland}
        signals = []

        def send(identity, signum):
            signals.append((identity, signum))
            # gamescope owns shutdown of Xwayland after its compositor thread.
            current.clear()

        with mock.patch.object(self.session, 'no_starter', return_value=True), \
                mock.patch.object(self.session, 'observe', side_effect=lambda: set(current)), \
                mock.patch.object(self.session, 'compositors', return_value={self.compositor}), \
                mock.patch.object(self.session, 'signal_identity', side_effect=send):
            self.assertTrue(self.session.cleanup())
        self.assertEqual(signals, [(self.compositor, signal.SIGTERM)])

    def test_survivors_are_signalled_only_after_compositor_exits(self):
        current = {self.compositor, self.xwayland}
        signals = []

        def send(identity, signum):
            signals.append((identity, signum))
            current.remove(identity)

        with mock.patch.object(self.session, 'no_starter', return_value=True), \
                mock.patch.object(self.session, 'observe', side_effect=lambda: set(current)), \
                mock.patch.object(self.session, 'compositors', return_value={self.compositor}), \
                mock.patch.object(self.session, 'signal_identity', side_effect=send):
            self.assertTrue(self.session.cleanup())
        self.assertEqual(signals, [(self.compositor, signal.SIGTERM),
                                   (self.xwayland, signal.SIGTERM)])

    def test_compositor_escalates_before_any_display_child(self):
        current = {self.compositor, self.xwayland}
        signals = []

        def send(identity, signum):
            signals.append((identity, signum))
            if identity != self.compositor or signum == signal.SIGKILL:
                current.remove(identity)

        with mock.patch.object(self.session, 'no_starter', return_value=True), \
                mock.patch.object(self.session, 'observe', side_effect=lambda: set(current)), \
                mock.patch.object(self.session, 'compositors', return_value={self.compositor}), \
                mock.patch.object(self.session, 'signal_identity', side_effect=send):
            self.assertTrue(self.session.cleanup(compositor_seconds=0))
        self.assertEqual(signals, [(self.compositor, signal.SIGTERM),
                                   (self.compositor, signal.SIGKILL),
                                   (self.xwayland, signal.SIGTERM)])

    def test_unstoppable_compositor_leaves_its_display_children_alone(self):
        with mock.patch.object(self.session, 'no_starter', return_value=True), \
                mock.patch.object(self.session, 'observe', return_value={self.compositor, self.xwayland}), \
                mock.patch.object(self.session, 'compositors', return_value={self.compositor}), \
                mock.patch.object(self.session, 'signal_identity') as send:
            self.assertFalse(self.session.cleanup(compositor_seconds=0, kill_seconds=0))
        self.assertEqual(send.call_args_list, [mock.call(self.compositor, signal.SIGTERM),
                                              mock.call(self.compositor, signal.SIGKILL)])

    def test_active_starter_blocks_all_cleanup_signals(self):
        with mock.patch.object(self.session, 'no_starter', return_value=False), \
                mock.patch.object(self.session, 'signal_identity') as send, \
                mock.patch.object(self.session, 'observe') as observe:
            with self.assertRaises(RuntimeError):
                self.session.cleanup()
            send.assert_not_called()
            observe.assert_not_called()

    def test_starter_appearing_during_compositor_wait_blocks_later_signals(self):
        with mock.patch.object(self.session, 'no_starter', side_effect=[True, False]), \
                mock.patch.object(self.session, 'observe', return_value={self.compositor, self.xwayland}), \
                mock.patch.object(self.session, 'compositors', return_value={self.compositor}), \
                mock.patch.object(self.session, 'signal_identity') as send:
            with self.assertRaises(RuntimeError):
                self.session.cleanup()
        send.assert_called_once_with(self.compositor, signal.SIGTERM)

    def test_signal_still_refuses_a_reused_or_unowned_identity(self):
        self.session.token = '0' * 32
        with mock.patch.object(self.session, 'no_starter', return_value=True), \
                mock.patch.object(supervisor.os, 'pidfd_open', return_value=77), \
                mock.patch.object(supervisor.os, 'close'), \
                mock.patch.object(supervisor, 'owned_identity', return_value=None), \
                mock.patch.object(supervisor.signal, 'pidfd_send_signal') as send:
            self.assertFalse(self.session.signal_identity(self.compositor, signal.SIGTERM))
            send.assert_not_called()

    def test_absent_server_skips_kill_wait_commands(self):
        session = mock.Mock(prefix=self.base / 'prefix', sent_signals=set(), wine_helpers=set())
        session.no_starter.return_value = True
        session.cleanup.return_value = True
        log = mock.Mock()
        with mock.patch.object(supervisor, 'prefix_processes', return_value=set()), \
                mock.patch.object(supervisor.subprocess, 'run') as run, \
                mock.patch.object(supervisor.profile_manager, 'no_running_prefix'):
            self.assertTrue(supervisor.cleanup_session(session, self.base, {}, None, log))
            run.assert_not_called()
        log.assert_called_once_with('The dedicated Wine server has already exited.',
                                    event='wine_server_absent')

    def test_unknown_or_present_server_keeps_existing_dedicated_cleanup(self):
        for servers in (None, {201}):
            with self.subTest(servers=servers):
                session = mock.Mock(prefix=self.base / 'prefix', sent_signals=set(), wine_helpers=set())
                session.no_starter.return_value = True
                session.cleanup.return_value = True
                with mock.patch.object(supervisor, 'prefix_processes', return_value=servers), \
                        mock.patch.object(supervisor.subprocess, 'run') as run, \
                        mock.patch.object(supervisor.profile_manager, 'no_running_prefix'):
                    self.assertTrue(supervisor.cleanup_session(session, self.base, {}, None, mock.Mock()))
                self.assertEqual([call.args[0][-1] for call in run.call_args_list], ['-k', '-w'])

    def test_forced_wine_helper_cleanup_remains_visible_in_finished_summary(self):
        helper = supervisor.ProcessIdentity(103, 1003, os.getuid())
        session = mock.Mock(prefix=self.base / 'prefix',
                            sent_signals={(helper, signal.SIGKILL),
                                          (self.compositor, signal.SIGKILL)},
                            wine_helpers={helper})
        session.no_starter.return_value = True
        session.cleanup.return_value = True
        log = diagnostics.SessionLog(self.base)

        def event(message, **fields):
            log.event(fields.pop('event', 'supervisor'), message, **fields)

        with mock.patch.object(supervisor, 'prefix_processes', return_value=set()), \
                mock.patch.object(supervisor.profile_manager, 'no_running_prefix'):
            self.assertTrue(supervisor.cleanup_session(session, self.base, {}, None, event))
        log.finish(0)
        import json
        summary = json.loads((self.base / 'logs/latest-session.json').read_text())
        self.assertEqual(summary['status'], 'finished')
        self.assertTrue(summary['cleanup_required_force'])
        self.assertEqual(summary['cleanup_forced_wine_helpers'],
                         [dict(pid=103, role='wine_helper', signal=signal.SIGKILL)])
        events = [json.loads(line) for line in (self.base / 'logs/events.jsonl').read_text().splitlines()]
        forced = next(item for item in events if item['event'] == 'wine_helper_cleanup_escalated')
        self.assertEqual(forced['level'], 'WARNING')


if __name__ == '__main__':
    unittest.main()
