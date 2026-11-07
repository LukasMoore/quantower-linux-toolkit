"""Integrated lifecycle regressions. All process discovery and signals are fake."""
import contextlib
import json
import os
from pathlib import Path
import signal
import tempfile
import unittest
from unittest import mock

import diagnostics
import supervisor
import runtime_config


class IntegratedOwnershipTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.session = supervisor.SessionProcesses(self.base / 'prefix', self.base / 'ledger.json')
        self.session.begin('integrated')
        self.identity = supervisor.ProcessIdentity(999999999, 1001, os.getuid())

    def test_ledger_records_and_restores_prior_actual_mode(self):
        other = supervisor.SessionProcesses(self.session.prefix, self.session.ledger)
        other.load()
        self.assertEqual(other.window_mode, 'integrated')
        other.begin('nested')
        self.assertEqual(json.loads(other.ledger.read_text())['window_mode'], 'nested')

    def test_legacy_and_invalid_modes(self):
        record = json.loads(self.session.ledger.read_text())
        del record['window_mode']
        self.session.ledger.write_text(json.dumps(record))
        self.session.load()
        self.assertEqual(self.session.window_mode, 'nested')
        for mode in ('other', None, 1, {}, ['integrated']):
            with self.subTest(mode=mode):
                record['window_mode'] = mode
                self.session.ledger.write_text(json.dumps(record))
                with self.assertRaisesRegex(RuntimeError, 'window mode'):
                    self.session.load()

    def test_same_birth_credential_loss_retains_ledger_and_defers(self):
        self.session.identities = {self.identity}
        self.session.save()
        before = self.session.ledger.read_bytes()
        with mock.patch.object(supervisor, 'live_identity', return_value=self.identity), \
                mock.patch.object(supervisor, 'owned_identity', return_value=None):
            with self.assertRaisesRegex(RuntimeError, 'credentials lost'):
                self.session.observe()
        self.assertEqual(self.session.identities, {self.identity})
        self.assertEqual(self.session.ledger.read_bytes(), before)

    def test_protected_same_birth_metadata_retains_identity(self):
        self.session.identities = {self.identity}
        for failure in (PermissionError('denied'), OSError('unreadable')):
            with self.subTest(failure=failure), \
                    mock.patch.object(supervisor, 'live_identity', side_effect=failure):
                with self.assertRaisesRegex(RuntimeError, 'Cannot verify'):
                    self.session.observe()
                self.assertEqual(self.session.identities, {self.identity})

    def test_same_birth_uid_change_is_uncertainty_not_retirement(self):
        self.session.identities = {self.identity}
        changed = supervisor.ProcessIdentity(self.identity.pid, self.identity.start_ticks, self.identity.uid + 1)
        with mock.patch.object(supervisor, 'live_identity', return_value=changed):
            with self.assertRaisesRegex(RuntimeError, 'UID changed'):
                self.session.observe()
        self.assertEqual(self.session.identities, {self.identity})

    def test_verified_death_or_reuse_retires_old_birth_without_adoption(self):
        for current in (None, supervisor.ProcessIdentity(self.identity.pid, 2002, os.getuid())):
            with self.subTest(current=current):
                self.session.identities = {self.identity}
                with mock.patch.object(supervisor, 'live_identity', return_value=current), \
                        mock.patch.object(supervisor.Path, 'iterdir', return_value=[Path('/proc/999999999')]), \
                        mock.patch.object(supervisor, 'owned_identity', return_value=current) as owned:
                    self.assertEqual(self.session.observe(), set())
                    owned.assert_not_called()

    def test_unknown_role_is_recorded_then_blocks_before_wineserver(self):
        with mock.patch.object(supervisor.Path, 'iterdir', return_value=[Path('/proc/999999999')]), \
                mock.patch.object(supervisor, 'owned_identity', return_value=self.identity):
            self.assertEqual(self.session.observe(), {self.identity})
        for executable in ('/usr/bin/Xwayland', '/usr/bin/Xorg', '/usr/bin/gnome-shell',
                           '/usr/libexec/xdg-desktop-portal', '/usr/bin/pipewire', '/usr/bin/firefox',
                           str(self.base / 'gamescope/files/bin/gamescope-brokey'), '/other/bin/wine'):
            with self.subTest(executable=executable), \
                    mock.patch.object(self.session, 'no_starter', return_value=True), \
                    mock.patch.object(self.session, 'observe', return_value={self.identity}), \
                    mock.patch.object(supervisor.Path, 'readlink', return_value=Path(executable)), \
                    mock.patch.object(supervisor.subprocess, 'run') as run, \
                    mock.patch.object(supervisor.signal, 'pidfd_send_signal') as send:
                self.assertFalse(supervisor.cleanup_session(self.session, self.base, {}, None, mock.Mock()))
                run.assert_not_called()
                send.assert_not_called()
        self.assertEqual(self.session.identities, {self.identity})

    def test_wait_rechecks_roles_without_signals(self):
        with mock.patch.object(self.session, 'no_starter', return_value=True), \
                mock.patch.object(self.session, 'observe', return_value={self.identity}), \
                mock.patch.object(self.session, 'wine_processes', return_value=set()), \
                mock.patch.object(supervisor.signal, 'pidfd_send_signal') as send:
            with self.assertRaisesRegex(RuntimeError, 'Non-Wine'):
                self.session.wait_for_exit({self.identity}, 0)
            send.assert_not_called()

    def test_role_change_after_pidfd_open_refuses_signal(self):
        with mock.patch.object(self.session, 'no_starter', return_value=True), \
                mock.patch.object(supervisor.os, 'pidfd_open', return_value=77) as opened, \
                mock.patch.object(supervisor.os, 'close') as closed, \
                mock.patch.object(supervisor, 'owned_identity', return_value=self.identity), \
                mock.patch.object(self.session, 'wine_processes', return_value=set()), \
                mock.patch.object(supervisor.signal, 'pidfd_send_signal') as send:
            with self.assertRaisesRegex(RuntimeError, 'Non-Wine'):
                self.session.signal_identity(self.identity, signal.SIGTERM)
            opened.assert_called_once_with(self.identity.pid, 0)
            closed.assert_called_once_with(77)
            send.assert_not_called()
            self.assertEqual(self.session.sent_signals, set())

    def test_missing_pidfd_api_defers_without_unsafe_signal_fallback(self):
        with mock.patch.object(self.session, 'no_starter', return_value=True), \
                mock.patch.object(supervisor, 'os', spec=['close']) as unavailable, \
                mock.patch.object(supervisor.signal, 'pidfd_send_signal') as send:
            with self.assertRaisesRegex(RuntimeError, 'process-handle cleanup is unavailable'):
                self.session.signal_identity(self.identity, signal.SIGTERM)
            send.assert_not_called()
        self.assertEqual(self.session.sent_signals, set())

    def test_starter_appearing_after_pidfd_open_refuses_signal(self):
        with mock.patch.object(self.session, 'no_starter', side_effect=[True, False]), \
                mock.patch.object(supervisor.os, 'pidfd_open', return_value=77), \
                mock.patch.object(supervisor.os, 'close'), \
                mock.patch.object(supervisor, 'owned_identity', return_value=self.identity), \
                mock.patch.object(self.session, 'observe', return_value={self.identity}), \
                mock.patch.object(self.session, 'wine_processes', return_value={self.identity}), \
                mock.patch.object(supervisor.signal, 'pidfd_send_signal') as send:
            with self.assertRaisesRegex(RuntimeError, 'became active'):
                self.session.signal_identity(self.identity, signal.SIGTERM)
            send.assert_not_called()

    def test_new_nonwine_role_between_server_commands_blocks_wait(self):
        with mock.patch.object(self.session, 'no_starter', return_value=True), \
                mock.patch.object(self.session, 'observe', side_effect=[set(), set(), {self.identity}]), \
                mock.patch.object(self.session, 'verify_wine_server_ownership'), \
                mock.patch.object(self.session, 'wine_processes', side_effect=lambda identities: set()), \
                mock.patch.object(supervisor, 'prefix_processes', return_value={101}), \
                mock.patch.object(supervisor.subprocess, 'run') as run:
            self.assertFalse(supervisor.cleanup_session(self.session, self.base, {}, None, mock.Mock()))
            self.assertEqual([call.args[0][-1] for call in run.call_args_list], ['-k'])

    def test_unknown_server_identity_blocks_before_first_scoped_command(self):
        with mock.patch.object(self.session, 'no_starter', return_value=True), \
                mock.patch.object(self.session, 'observe', return_value=set()), \
                mock.patch.object(supervisor, 'prefix_processes', return_value={self.identity.pid}), \
                mock.patch.object(supervisor.subprocess, 'run') as run:
            self.assertFalse(supervisor.cleanup_session(self.session, self.base, {}, None, mock.Mock()))
            run.assert_not_called()

    def test_new_unknown_role_during_pidfd_open_blocks_even_known_target(self):
        foreign = supervisor.ProcessIdentity(999999998, 1002, os.getuid())
        with mock.patch.object(self.session, 'no_starter', return_value=True), \
                mock.patch.object(supervisor.os, 'pidfd_open', return_value=77), \
                mock.patch.object(supervisor.os, 'close'), \
                mock.patch.object(supervisor, 'owned_identity', return_value=self.identity), \
                mock.patch.object(self.session, 'observe', return_value={self.identity, foreign}), \
                mock.patch.object(self.session, 'wine_processes', return_value={self.identity}), \
                mock.patch.object(supervisor.signal, 'pidfd_send_signal') as send:
            with self.assertRaisesRegex(RuntimeError, 'Non-Wine'):
                self.session.signal_identity(self.identity, signal.SIGTERM)
            send.assert_not_called()

    def test_integrated_cleanup_does_not_consult_compositor_selector(self):
        with mock.patch.object(self.session, 'no_starter', return_value=True), \
                mock.patch.object(self.session, 'observe', return_value=set()), \
                mock.patch.object(self.session, 'compositors') as compositor:
            self.assertTrue(self.session.cleanup())
            compositor.assert_not_called()

    def test_authentication_of_starter_requires_owned_wine_role(self):
        for identity, roles in ((None, set()), (self.identity, set()),
                                (self.identity, {self.identity})):
            with self.subTest(identity=identity, roles=roles), \
                    mock.patch.object(supervisor, 'prefix_processes', return_value={self.identity.pid}), \
                    mock.patch.object(supervisor, 'owned_identity', return_value=identity), \
                    mock.patch.object(self.session, 'wine_processes', return_value=roles):
                self.assertEqual(supervisor.authenticated_starters(self.session),
                                 {self.identity.pid} if roles and identity else None)


class SyntheticProcTests(unittest.TestCase):
    """Exercise real identity/role parsing with an original disposable proc tree."""
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.proc = self.base / 'proc'
        self.entry = self.proc / '999999999'
        self.entry.mkdir(parents=True)
        self.prefix = self.base / 'prefix'
        self.session = supervisor.SessionProcesses(self.prefix, self.base / 'ledger.json')
        self.session.begin('integrated')
        self.identity = supervisor.ProcessIdentity(999999999, 1001, os.getuid())
        (self.entry / 'stat').write_text('999999999 (winedevice.exe) ' +
                                        ' '.join(['S'] + ['0'] * 18 + ['1001']))
        (self.entry / 'comm').write_text('winedevice.exe\n')
        (self.entry / 'cmdline').write_bytes(b'C:\\windows\\system32\\winedevice.exe\0')
        (self.entry / 'environ').write_bytes(os.fsencode('WINEPREFIX=' + str(self.prefix)) + b'\0' +
                                            os.fsencode(supervisor.SESSION_VARIABLE + '=' + self.session.token) + b'\0')
        (self.entry / 'exe').symlink_to(self.base / 'runner-quantower/opt/wine-devel/lib/wine/x86_64-unix/wine')
        def routed_path(*args):
            if args and str(args[0]) == '/proc':
                return self.proc.joinpath(*args[1:])
            return Path(*args)
        patch = mock.patch.object(supervisor, 'Path', side_effect=routed_path)
        patch.start()
        self.addCleanup(patch.stop)

    def test_actual_identity_and_wine_role_accept_only_exact_prefix_token_and_birth(self):
        self.assertEqual(supervisor.owned_identity(self.identity.pid, self.prefix, self.session.token), self.identity)
        self.assertEqual(supervisor.live_identity(self.identity.pid), self.identity)
        self.assertIsNone(supervisor.owned_identity(self.identity.pid, self.base / 'other-prefix', self.session.token))
        self.assertIsNone(supervisor.owned_identity(self.identity.pid, self.prefix, 'b' * 32))
        self.assertEqual(self.session.observe(), {self.identity})
        self.session.verify_cleanup_roles(self.session.identities)

    def test_real_env_loss_preserves_same_birth_ledger(self):
        self.session.observe()
        before = self.session.ledger.read_bytes()
        (self.entry / 'environ').write_bytes(b'WINEPREFIX=/different\0')
        with self.assertRaisesRegex(RuntimeError, 'credentials lost'):
            self.session.observe()
        self.assertEqual(self.session.ledger.read_bytes(), before)
        self.assertEqual(self.session.identities, {self.identity})

    def test_actual_same_pid_exec_to_host_role_is_not_signalled(self):
        self.session.observe()
        def opened(pid, flags):
            (self.entry / 'exe').unlink()
            (self.entry / 'exe').symlink_to('/usr/bin/gnome-shell')
            return 77
        with mock.patch.object(supervisor.os, 'pidfd_open', side_effect=opened), \
                mock.patch.object(supervisor.os, 'close'), \
                mock.patch.object(supervisor.signal, 'pidfd_send_signal') as send:
            with self.assertRaisesRegex(RuntimeError, 'Non-Wine'):
                self.session.signal_identity(self.identity, signal.SIGTERM)
            send.assert_not_called()
        self.assertEqual(self.session.identities, {self.identity})

    def test_actual_starter_presence_blocks_even_wine_ancestor_cleanup(self):
        (self.entry / 'comm').write_text('Starter.exe\n')
        (self.entry / 'cmdline').write_bytes(b'C:\\app\\Starter.exe\0')
        self.assertEqual(supervisor.authenticated_starters(self.session), {self.identity.pid})
        with mock.patch.object(supervisor.os, 'pidfd_open') as opened:
            with self.assertRaisesRegex(RuntimeError, 'still active'):
                self.session.signal_identity(self.identity, signal.SIGTERM)
            opened.assert_not_called()

    def test_real_server_prefix_token_and_executable_gate_scoped_commands(self):
        (self.entry / 'comm').write_text('wineserver\n')
        (self.entry / 'cmdline').write_bytes(b'/dedicated/wineserver\0')
        (self.entry / 'exe').unlink()
        (self.entry / 'exe').symlink_to(self.base / 'runner-quantower/opt/wine-devel/bin/wineserver')
        with mock.patch.object(self.session, 'cleanup', return_value=True), \
                mock.patch.object(supervisor.profile_manager, 'no_running_prefix'), \
                mock.patch.object(supervisor.subprocess, 'run') as run:
            self.assertTrue(supervisor.cleanup_session(self.session, self.base, {}, None, mock.Mock()))
            self.assertEqual([call.args[0][-1] for call in run.call_args_list], ['-k', '-w'])
        # Same prefix with another launch token must not receive a scoped stop.
        self.session.identities.clear()
        (self.entry / 'environ').write_bytes(os.fsencode('WINEPREFIX=' + str(self.prefix)) + b'\0' +
                                            os.fsencode(supervisor.SESSION_VARIABLE + '=' + 'b' * 32) + b'\0')
        with mock.patch.object(supervisor.subprocess, 'run') as run:
            self.assertFalse(supervisor.cleanup_session(self.session, self.base, {}, None, mock.Mock()))
            run.assert_not_called()


class IntegratedMonitorTests(unittest.TestCase):
    def run_monitor(self, starters, statuses, *, startup=10, stopped=None):
        clock = [0]
        def sleep(seconds):
            clock[0] += seconds
        child = mock.Mock(pid=101, poll=mock.Mock(side_effect=statuses))
        session, log, telemetry = mock.Mock(), mock.Mock(), mock.Mock()
        with mock.patch.object(supervisor, 'authenticated_starters', side_effect=starters), \
                mock.patch.object(supervisor.time, 'monotonic', side_effect=lambda: clock[0]), \
                mock.patch.object(supervisor.time, 'sleep', side_effect=sleep):
            result = supervisor.monitor_integrated(child, session, telemetry, log,
                                                   stopped or (lambda: 0), startup_seconds=startup)
        return result, session, log, telemetry, clock[0]

    def test_running_child_or_child_is_starter_then_normal_disappearance(self):
        for statuses in ([None] * 9, [None, None] + [0] * 7):
            result, session, log, telemetry, elapsed = self.run_monitor(
                [{101}, {101}] + [set()] * 7, statuses)
            self.assertEqual(result[0], 0)
            self.assertEqual(session.observe.call_count, 1)
            session.signal_identity.assert_not_called()
            self.assertGreaterEqual(elapsed, 4)

    def test_zero_bootstrap_handoff_waits_for_delayed_starter(self):
        result, session, log, telemetry, elapsed = self.run_monitor(
            [set(), set(), {202}] + [set()] * 7, [0] * 10)
        self.assertEqual(result, (0, 0))
        exited = [call for call in log.call_args_list if call.kwargs.get('event') == 'child_exited']
        self.assertEqual(len(exited), 1)
        self.assertTrue(exited[0].kwargs['handoff_pending'])
        self.assertEqual(session.observe.call_count, 1)
        session.signal_identity.assert_not_called()

    def test_nonzero_bootstrap_with_live_starter_stays_supervised_and_failed(self):
        result, session, log, telemetry, elapsed = self.run_monitor(
            [{202}] * 3 + [set()] * 7, [17] * 10)
        self.assertEqual(result, (1, 17))
        self.assertGreaterEqual(elapsed, 4)
        self.assertEqual(telemetry.heartbeat.call_args_list[1].kwargs['starter_pids'], [202])
        session.signal_identity.assert_not_called()

    def test_no_starter_deadline_even_when_child_exited_zero(self):
        result, session, log, telemetry, elapsed = self.run_monitor(
            [set()] * 5, [0] * 5, startup=2)
        self.assertEqual(result, (1, 0))
        self.assertEqual(elapsed, 2)
        session.signal_identity.assert_not_called()
        self.assertTrue(any(call.kwargs.get('event') == 'startup_timeout' for call in log.call_args_list))

    def test_unknown_discovery_resets_continuous_disappearance_interval(self):
        starters = [{202}, set(), set(), None] + [set()] * 7
        result, session, log, telemetry, elapsed = self.run_monitor(starters, [0] * len(starters))
        self.assertEqual(result, (0, 0))
        self.assertEqual(elapsed, 5)
        self.assertIsNone(telemetry.heartbeat.call_args_list[3].kwargs['starter_pids'])

    def test_live_starter_cancels_disappearance_grace(self):
        starters = [{202}, set(), set(), {202}] + [set()] * 7
        result, session, log, telemetry, elapsed = self.run_monitor(starters, [0] * len(starters))
        self.assertEqual(result, (0, 0))
        self.assertEqual(elapsed, 5)

    def test_uncertain_startup_times_out_without_success_or_signals(self):
        result, session, log, telemetry, elapsed = self.run_monitor(
            [None] * 5, [None] * 5, startup=2)
        self.assertEqual(result, (1, None))
        session.observe.assert_not_called()
        session.signal_identity.assert_not_called()

    def test_signal_returns_without_signalling_live_app(self):
        result, session, log, telemetry, elapsed = self.run_monitor([], [], stopped=lambda: signal.SIGINT)
        self.assertEqual(result, (130, None))
        session.signal_identity.assert_not_called()

    def test_mode_dpi_backend_and_handoff_status_are_durable(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            log = diagnostics.SessionLog(base)
            log.event('window_mode_selected', 'Integrated mode selected.', window_mode='integrated',
                      requested_window_mode='integrated', display_backend='host-x11', effective_wine_dpi=192,
                      child_role='wine', child_exited=True, handoff_pending=True)
            summary = json.loads((base / 'logs/latest-session.json').read_text())
            for field, value in dict(window_mode='integrated', requested_window_mode='integrated',
                                     display_backend='host-x11', effective_wine_dpi=192, child_role='wine',
                                     child_exited=True, handoff_pending=True).items():
                self.assertEqual(summary[field], value)
            log.finish(0)


class IntegratedLaunchSequenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        (self.base / 'prefix').mkdir()
        runner = self.base / 'runner-quantower/opt/wine-devel/bin'
        runner.mkdir(parents=True)
        for name in ('wine', 'wineserver'):
            (runner / name).touch()

    def test_unsupported_combo_does_not_prepare_recover_or_spawn(self):
        log = diagnostics.SessionLog(self.base)
        with mock.patch.object(supervisor, 'prefix_processes', return_value=set()), \
                mock.patch.object(supervisor.profile_manager, 'load', return_value='compatible'), \
                mock.patch.object(supervisor.runtime_config, 'load',
                                  return_value=runtime_config.RuntimeConfig(window_mode='integrated')), \
                mock.patch.object(supervisor.profile_manager, 'prepare') as prepare, \
                mock.patch.object(supervisor, 'cleanup_session') as cleanup, \
                mock.patch.object(supervisor.subprocess, 'Popen') as popen:
            self.assertEqual(supervisor.main(base=self.base, diagnostics=log), 1)
            prepare.assert_not_called()
            cleanup.assert_not_called()
            popen.assert_not_called()
        log.finish(1)

    def test_connection_preflight_failure_precedes_profile_transaction(self):
        log = diagnostics.SessionLog(self.base)
        session = mock.Mock(window_mode='nested', identities=set())
        with mock.patch.object(supervisor, 'SessionProcesses', return_value=session), \
                mock.patch.object(supervisor, 'prefix_processes', return_value=set()), \
                mock.patch.object(supervisor.profile_manager, 'load', return_value='gpu'), \
                mock.patch.object(supervisor.runtime_config, 'load',
                                  return_value=runtime_config.RuntimeConfig(window_mode='integrated')), \
                mock.patch.object(supervisor.profile_manager, 'no_running_prefix') as inactive, \
                mock.patch.object(supervisor.runtime_config, 'integrated_preflight',
                                  side_effect=RuntimeError('Authentication denied')) as preflight, \
                mock.patch.object(supervisor.profile_manager, 'prepare') as prepare, \
                mock.patch.object(supervisor.subprocess, 'Popen') as popen:
            self.assertEqual(supervisor.main(base=self.base, diagnostics=log), 1)
            inactive.assert_called_once_with(self.base / 'prefix')
            preflight.assert_called_once()
            prepare.assert_not_called()
            session.begin.assert_not_called()
            popen.assert_not_called()
        log.finish(1)

    def test_prior_mode_is_used_for_recovery_before_new_mode_is_saved(self):
        for prior, requested in (('integrated', 'nested'), ('nested', 'integrated')):
            with self.subTest(prior=prior, requested=requested):
                log = diagnostics.SessionLog(self.base)
                session = mock.Mock(window_mode=prior, token='a' * 32,
                                    identities={supervisor.ProcessIdentity(99, 100, os.getuid())},
                                    sent_signals=set())
                order = []
                def cleanup(*args, **kwargs):
                    order.append(('cleanup', session.window_mode))
                    session.identities.clear()
                    return True
                def begin(window_mode):
                    order.append(('begin', window_mode))
                    session.window_mode = window_mode
                session.begin.side_effect = begin
                child = mock.Mock(pid=101, poll=mock.Mock(return_value=0))
                plan = mock.Mock(arguments=['/dedicated/wine', 'C:\\selected\\Starter.exe'], environment={})
                with contextlib.ExitStack() as stack:
                    stack.enter_context(mock.patch.object(supervisor, 'SessionProcesses', return_value=session))
                    stack.enter_context(mock.patch.object(supervisor, 'prefix_processes', return_value=set()))
                    stack.enter_context(mock.patch.object(supervisor.profile_manager, 'load', return_value='gpu'))
                    stack.enter_context(mock.patch.object(supervisor.runtime_config, 'load',
                                        return_value=runtime_config.RuntimeConfig(window_mode=requested)))
                    stack.enter_context(mock.patch.object(supervisor.profile_manager, 'no_running_prefix'))
                    stack.enter_context(mock.patch.object(supervisor.profile_manager, 'prepare'))
                    stack.enter_context(mock.patch.object(supervisor.runtime_config, 'integrated_preflight'))
                    stack.enter_context(mock.patch.object(supervisor.runtime_config, 'build_integrated_launch',
                                                         return_value=plan))
                    stack.enter_context(mock.patch.object(supervisor, 'monitor_integrated', return_value=(0, 0)))
                    stack.enter_context(mock.patch.object(supervisor, 'cleanup_session', side_effect=cleanup))
                    stack.enter_context(mock.patch.object(supervisor.subprocess, 'Popen', return_value=child))
                    stack.enter_context(mock.patch.object(supervisor.signal, 'signal'))
                    supervisor.main(base=self.base, diagnostics=log)
                self.assertEqual(order[:2], [('cleanup', prior), ('begin', requested)])
                log.finish(0)

    def test_failed_prior_recovery_never_begins_new_mode(self):
        log = diagnostics.SessionLog(self.base)
        session = mock.Mock(window_mode='integrated', identities={object()})
        with mock.patch.object(supervisor, 'SessionProcesses', return_value=session), \
                mock.patch.object(supervisor, 'prefix_processes', return_value=set()), \
                mock.patch.object(supervisor.profile_manager, 'load', return_value='gpu'), \
                mock.patch.object(supervisor, 'cleanup_session', return_value=False), \
                mock.patch.object(supervisor.profile_manager, 'prepare') as prepare:
            self.assertEqual(supervisor.main(base=self.base, diagnostics=log), 1)
            session.begin.assert_not_called()
            prepare.assert_not_called()
            self.assertEqual(session.window_mode, 'integrated')
        log.finish(1)


if __name__ == '__main__':
    unittest.main()
