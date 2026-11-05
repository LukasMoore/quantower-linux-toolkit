#!/usr/bin/env python3
"""Keep the dedicated Quantower Wine desktop and its app in one session."""

import argparse
import contextlib
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import secrets
import signal
import subprocess
import time

import profile_manager
import application_runtime
import runtime_config


def process_is_named(name, arguments, names):
    executable = (arguments[0].decode(errors='replace').replace('\\', '/').rsplit('/', 1)[-1].lower()
                  if arguments else '')
    return name.lower() in names or executable in names


def prefix_processes(prefix, names=('starter.exe',)):
    """Return matching exact-prefix PIDs, or None if ownership is uncertain."""
    found = set()
    expected = os.fsencode(str(prefix))
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():
            continue
        try:
            if proc.stat().st_uid != os.getuid():
                continue
            name = (proc / 'comm').read_text().strip().lower()
            arguments = (proc / 'cmdline').read_bytes().split(b'\0')
            if not process_is_named(name, arguments, names):
                continue
            environment = (proc / 'environ').read_bytes().split(b'\0')
            if b'WINEPREFIX=' + expected in environment:
                found.add(int(proc.name))
        except (FileNotFoundError, ProcessLookupError):
            continue
        except PermissionError:
            return None
        except OSError:
            return None
    return found


SESSION_VARIABLE = 'QUANTOWER_LAUNCH_SESSION'


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    start_ticks: int
    uid: int


def owned_identity(pid, prefix, token):
    """Read only an exact UID/prefix/session identity, checking PID reuse twice."""
    proc = Path('/proc') / str(pid)
    try:
        uid = proc.stat().st_uid
        if uid != os.getuid():
            return None
        before = (proc / 'stat').read_text()
        fields = before[before.rfind(')') + 2:].split()
        if fields[0] in ('Z', 'X'):
            return None
        started = int(fields[19])
        env = (proc / 'environ').read_bytes().split(b'\0')
        if (os.fsencode('WINEPREFIX=' + str(prefix)) not in env or
                os.fsencode(SESSION_VARIABLE + '=' + token) not in env):
            return None
        after = (proc / 'stat').read_text()
        fields = after[after.rfind(')') + 2:].split()
        if int(fields[19]) != started or fields[0] in ('Z', 'X') or proc.stat().st_uid != uid:
            return None
        return ProcessIdentity(int(pid), started, uid)
    except (FileNotFoundError, ProcessLookupError):
        return None


class SessionProcesses:
    """Track inherited session identities even after descendants are reparented."""

    def __init__(self, prefix, ledger):
        self.prefix, self.ledger = Path(prefix), Path(ledger)
        self.token = None
        self.identities = set()
        self.sent_signals = set()
        self.wine_helpers = set()

    def load(self):
        if not self.ledger.exists():
            return
        info = self.ledger.lstat()
        if self.ledger.is_symlink() or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise RuntimeError('The session ownership record is unsafe.')
        record = json.loads(self.ledger.read_text())
        if (record.get('version') != 1 or record.get('uid') != os.getuid() or
                record.get('prefix') != str(self.prefix) or
                not re.fullmatch(r'[0-9a-f]{32}', record.get('token', ''))):
            raise RuntimeError('The session ownership record differs.')
        self.token = record['token']
        self.identities = {ProcessIdentity(int(p['pid']), int(p['start_ticks']), int(p['uid']))
                           for p in record['processes']}

    def save(self):
        record = dict(version=1, uid=os.getuid(), prefix=str(self.prefix),
                      token=self.token,
                      processes=[dict(pid=p.pid, start_ticks=p.start_ticks, uid=p.uid)
                                 for p in sorted(self.identities, key=lambda p: p.pid)])
        profile_manager.atomic_bytes(self.ledger, (json.dumps(record) + '\n').encode())

    def begin(self):
        self.token, self.identities = secrets.token_hex(16), set()
        self.sent_signals.clear()
        self.wine_helpers.clear()
        self.save()

    def remember_child(self, pid):
        """Remember our Popen child before an exec can make its environment unreadable."""
        proc = Path('/proc') / str(pid)
        try:
            if proc.stat().st_uid != os.getuid():
                raise RuntimeError('The launcher child ownership differs.')
            raw = (proc / 'stat').read_text()
            fields = raw[raw.rfind(')') + 2:].split()
            if fields[0] not in ('Z', 'X'):
                self.identities.add(ProcessIdentity(pid, int(fields[19]), os.getuid()))
                self.save()
        except (FileNotFoundError, ProcessLookupError):
            pass

    def observe(self):
        if self.token is None:
            return set()
        found = set()
        known = {p.pid for p in self.identities}
        for proc in Path('/proc').iterdir():
            if not proc.name.isdigit() or int(proc.name) == os.getpid():
                continue
            try:
                identity = owned_identity(int(proc.name), self.prefix, self.token)
                if identity:
                    found.add(identity)
            except (PermissionError, OSError):
                if int(proc.name) in known:
                    raise RuntimeError('Cannot verify a recorded session process.')
        if found != self.identities:
            self.identities = found
            self.save()
        return found

    def no_starter(self):
        # Any active or unverifiable Starter blocks all cleanup, including its ancestors.
        return prefix_processes(self.prefix) == set()

    def signal_identity(self, identity, signum):
        """A pidfd prevents the final check/signal race from hitting a recycled PID."""
        if not self.no_starter():
            raise RuntimeError('Quantower is still active; session cleanup deferred.')
        if not hasattr(os, 'pidfd_open') or not hasattr(signal, 'pidfd_send_signal'):
            raise RuntimeError('Safe process-handle cleanup is unavailable.')
        try:
            descriptor = os.pidfd_open(identity.pid, 0)
        except ProcessLookupError:
            return False
        try:
            if owned_identity(identity.pid, self.prefix, self.token) != identity:
                return False
            signal.pidfd_send_signal(descriptor, signum)
            self.sent_signals.add((identity, signum))
            return True
        except ProcessLookupError:
            return False
        finally:
            os.close(descriptor)

    def compositors(self, identities):
        """Select only the dedicated executable from already verified identities."""
        expected = str(self.prefix.parent / 'gamescope/files/bin/gamescope-brokey')
        found = set()
        for identity in identities:
            try:
                executable = str((Path('/proc') / str(identity.pid) / 'exe').readlink())
            except (FileNotFoundError, ProcessLookupError):
                continue
            if executable.removesuffix(' (deleted)') == expected:
                found.add(identity)
        return found

    def wine_processes(self, identities):
        """Select known dedicated Wine loaders/server, never a display process."""
        runtime = self.prefix.parent / 'runner-quantower/opt/wine-devel'
        expected = {str(runtime / 'bin/wine'), str(runtime / 'bin/wineserver')}
        for architecture in ('i386-unix', 'x86_64-unix'):
            for name in ('wine', 'wine-preloader'):
                expected.add(str(runtime / 'lib/wine' / architecture / name))
        found = set()
        for identity in identities:
            try:
                executable = str((Path('/proc') / str(identity.pid) / 'exe').readlink())
            except (FileNotFoundError, ProcessLookupError):
                continue
            if executable.removesuffix(' (deleted)') in expected:
                found.add(identity)
        return found

    def wait_for_exit(self, identities, seconds):
        deadline = time.monotonic() + seconds
        while True:
            if not self.no_starter():
                raise RuntimeError('Quantower became active; session cleanup deferred.')
            current = self.observe()
            if not (current if identities is None else current.intersection(identities)):
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.05)

    def cleanup(self, term_seconds=2, kill_seconds=1, compositor_seconds=30):
        if not self.no_starter():
            raise RuntimeError('Quantower is still active; session cleanup deferred.')

        # Official gamescope main.cpp handles SIGTERM with ShutdownGamescope(),
        # joins the X11 compositor thread, then stops/waits its own children.
        # Terminating its Xwayland first instead invokes its fatal X11 I/O handler.
        # https://github.com/ValveSoftware/gamescope/blob/3.16.25/src/main.cpp#L492-L505
        compositors = self.compositors(self.observe())
        if compositors:
            for identity in self.observe().intersection(compositors):
                self.signal_identity(identity, signal.SIGTERM)

            # gamescopereaper waits for every descendant, including Wine helpers
            # that can survive wineserver exit and ignore SIGTERM. Stop only these
            # exact owned Wine executables while Xwayland remains under gamescope's
            # control; otherwise gamescope blocks in WaitForAllChildren indefinitely.
            for signum, grace in ((signal.SIGTERM, term_seconds),
                                  (signal.SIGKILL, kill_seconds)):
                wine = self.wine_processes(self.observe())
                self.wine_helpers.update(wine)
                for identity in wine:
                    self.signal_identity(identity, signum)
                if self.wait_for_exit(wine, grace):
                    break

            if not self.wait_for_exit(compositors, compositor_seconds):
                for identity in self.observe().intersection(compositors):
                    self.signal_identity(identity, signal.SIGKILL)
                if not self.wait_for_exit(compositors, kill_seconds):
                    # Do not tear down a live compositor's display out from under it.
                    return False

        for signum, grace in ((signal.SIGTERM, term_seconds), (signal.SIGKILL, kill_seconds)):
            survivors = self.observe()
            self.wine_helpers.update(self.wine_processes(survivors))
            for identity in survivors:
                self.signal_identity(identity, signum)
            if self.wait_for_exit(None, grace):
                return True
        return not self.observe()


def cleanup_session(session, runner, env, output, log, server_timeout=15):
    """Stop the dedicated server, then only verified survivors from this launch."""
    if not session.no_starter():
        log('Quantower is still active; leaving its dedicated session running.')
        return False
    try:
        session.observe()
        servers = prefix_processes(session.prefix, ('wineserver', 'wineserver64'))
        if servers == set():
            # Wine server/main.c exits !kill_lock_owner(); no current lock owner
            # therefore makes -k return 1 even after a normal server shutdown.
            log('The dedicated Wine server has already exited.', event='wine_server_absent')
        else:
            for option in ('-k', '-w'):
                if not session.no_starter():
                    log('Quantower became active; session cleanup deferred.')
                    return False
                subprocess.run([str(runner / 'wineserver'), option], env=env,
                               stdout=output, stderr=output, timeout=server_timeout, check=True)
    except (OSError, subprocess.SubprocessError):
        log('Wine server cleanup did not finish; checking recorded session processes.', level='WARNING', exception=True)
    except RuntimeError:
        log('Cannot verify recorded session ownership; cleanup deferred.', level='WARNING', exception=True)
        return False
    try:
        if not session.cleanup():
            log('Recorded session processes did not stop within the cleanup deadline.')
            return False
        # Unknown exact-prefix processes block edits/relaunch rather than being killed.
        profile_manager.no_running_prefix(session.prefix)
        return True
    except (OSError, RuntimeError):
        log('Cannot establish an inactive dedicated prefix; leaving uncertain processes alone.', level='WARNING', exception=True)
        return False
    finally:
        forced = sorted({identity.pid for identity, signum in session.sent_signals
                         if signum == signal.SIGKILL and identity in session.wine_helpers})
        if forced:
            log('Lingering Wine helper processes required SIGKILL during cleanup.',
                event='wine_helper_cleanup_escalated', level='WARNING',
                cleanup_required_force=True,
                cleanup_forced_wine_helpers=[dict(pid=pid, role='wine_helper', signal=signal.SIGKILL)
                                             for pid in forced])


def windows(log=None, environment=None):
    """Return (desktop exists, visible app, reliable observation)."""
    try:
        env = dict(os.environ if environment is None else environment, LC_ALL='C')
        tree = subprocess.run(['xwininfo', '-root', '-tree'], capture_output=True,
                              text=True, errors='replace', env=env,
                              timeout=5, check=True).stdout
        if 'Root window id:' not in tree:
            return False, False, False
        desktop, visible = False, False
        pattern = r'^\s*(0x[0-9a-f]+)\s+"([^"]*)":\s+\("([^"]*)"\s+"([^"]*)"\)'
        for line in tree.splitlines():
            match = re.match(pattern, line, re.IGNORECASE)
            if not match:
                continue
            window, title, name, klass = match.groups()
            classes = {name.lower(), klass.lower()}
            is_desktop = ('explorer.exe' in classes and
                          title.lower() == 'amp-quantower - wine desktop')
            if not is_desktop and 'starter.exe' not in classes:
                continue
            desktop |= is_desktop
            info = subprocess.run(['xwininfo', '-id', window], capture_output=True,
                                  text=True, errors='replace', env=env,
                                  timeout=5, check=True).stdout
            state = re.search(r'Map State:\s*(\w+)', info)
            if not state:
                return desktop, visible, False
            visible |= not is_desktop and state.group(1) == 'IsViewable'
        return desktop, visible, True
    except (OSError, subprocess.SubprocessError):
        if log:
            log('Window observation failed.', level='WARNING', exception=True, event='window_observation_failed')
        return False, False, False


def main(*, base, diagnostics):
    base = Path(base).resolve()
    prefix = base / 'prefix'
    runner = base / 'runner-quantower/opt/wine-devel/bin'
    logs = base / 'logs'
    logs.mkdir(parents=True, exist_ok=True)
    # The diagnostics entry point owns launch.lock, rolling logs and the output pipe.
    with contextlib.nullcontext(diagnostics.output) as output:
        def log(message, *, level='INFO', exception=False, event='supervisor', **fields):
            diagnostics.event(event, message, level=level, exception=exception, **fields)

        env = os.environ.copy()
        from diagnostics import DEFAULT_WINEDEBUG
        env.update(WINEPREFIX=str(prefix), WINEARCH='win64',
                   WINEDEBUG=env.get('QUANTOWER_WINEDEBUG', DEFAULT_WINEDEBUG),
                   WINEDLLOVERRIDES='winemenubuilder.exe=d',
                   DXVK_LOG_PATH=str(diagnostics.directory / 'gpu'),
                   QUANTOWER_CRASH_TRACE_DIR=str(diagnostics.directory / 'native-assert'))
        env.pop('DOTNET_ROOT', None)
        env.pop('DOTNET_ROOT_X64', None)
        for variable in ('WINEDLLPATH', 'WINELOADER', 'WINESERVER',
                         'DOTNET_STARTUP_HOOKS', 'CONNECTION_OBSERVER_PROBE'):
            env.pop(variable, None)
        # Deliberate, one-launch diagnostics only; normal launches clear inherited hooks.
        diagnostic_hooks = env.get('QUANTOWER_DIAGNOSTIC_STARTUP_HOOKS')
        if diagnostic_hooks:
            env['DOTNET_STARTUP_HOOKS'] = diagnostic_hooks
            log('Explicit one-launch managed diagnostics enabled.', event='managed_diagnostics_enabled')
        if not prefix.is_dir() or not all((runner / name).is_file()
                                         for name in ('wine', 'wineserver')):
            log('Cannot start: the dedicated Wine installation is incomplete.', level='ERROR', event='runtime_missing')
            return 1
        log(f'Selected Wine runtime: {runner}')
        session = SessionProcesses(prefix, logs / 'session-processes.json')

        def cleanup():
            return cleanup_session(session, runner, env, output, log)

        current = prefix_processes(prefix)
        if current is None:
            log('Cannot verify an inactive desktop; leaving existing processes alone.', level='ERROR')
            return 1
        if current:
            log('An existing Quantower process is active; leaving it running.', event='existing_app_preserved', starter_pids=sorted(current))
            return 0
        try:
            application = application_runtime.load(base)
        except (OSError, ValueError, TypeError):
            log('Cannot resolve the selected AMP application version.', level='ERROR', exception=True)
            return 1
        env['QUANTOWER_APP_VERSION'] = application.version
        log('Selected AMP application version.', event='application_selected',
            application_version=application.version)
        try:
            mode = profile_manager.load(base)
            launch_config = runtime_config.load(base)
            x11_runtime = runtime_config.selected_x11(base, env)
        except (OSError, ValueError, RuntimeError) as error:
            log(f'Cannot start dedicated runtime: {error}', level='ERROR', exception=True)
            return 1
        env['QUANTOWER_X11_RUNTIME'] = x11_runtime
        runtime_config.apply_wine_library_environment(base, env, x11_runtime)
        log('Selected X11 client runtime.', event='x11_runtime_selected', x11_runtime=x11_runtime,
            library_directory=env.get('LD_LIBRARY_PATH'),
            note='Manifest-pinned private Xlib selected.' if x11_runtime == 'private' else 'System Xlib selected.')
        try:
            session.load()
            session.observe()
        except (OSError, ValueError, RuntimeError, KeyError, TypeError):
            log('Cannot verify the prior dedicated session ownership record.', level='ERROR', exception=True)
            return 1
        # Gamescope windows belong to its private X server. Exact-prefix process
        # ownership is authoritative; host xwininfo cannot establish their state.
        desktop = False
        if mode == 'compatible':
            desktop, visible, reliable = windows(log, environment=env)
            if not reliable:
                log('Cannot verify the compatible desktop state.', level='ERROR')
                return 1
            if visible:
                log('An existing Wine application window is visible; leaving it running.')
                return 0
        servers = prefix_processes(prefix, ('wineserver', 'wineserver64'))
        if servers is None:
            log('Cannot verify Wine server ownership; leaving existing processes alone.', level='ERROR')
            return 1
        if current or desktop or servers or session.identities:
            log('Recovering an inactive session without a usable Quantower desktop.')
            if not cleanup() or prefix_processes(prefix) != set():
                return 1
        try:
            profile_manager.prepare(base, mode)
        except (OSError, ValueError, RuntimeError) as error:
            log(f'Cannot apply rendering profile: {error}', level='ERROR', exception=True)
            return 1
        log(f'Applied rendering profile: {mode}', event='profile_applied', profile=mode)
        session.begin()
        env[SESSION_VARIABLE] = session.token

        stopped = 0

        def stop(signum, _frame):
            nonlocal stopped
            stopped = signum

        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(signum, stop)
        owned = False
        child = None
        observed_exit_status = None
        try:
            command = ([str(base / 'render-launch.sh')] if mode == 'gpu' else
                       [str(runner / 'wine'), 'explorer.exe',
                        launch_config.wine_desktop,
                        application.windows_executable])
            if mode == 'compatible':
                runtime_config.apply_trace_environment(base, env)
            child = subprocess.Popen(
                command,
                cwd=application.directory,
                env=env, stdout=output, stderr=output, close_fds=True)
            log('Started the dedicated Wine desktop.', event='child_started', child_pid=child.pid, profile=mode)
            owned = True
            session.remember_child(child.pid)
            session.observe()
            deadline, appeared, gone = time.monotonic() + 90, False, None
            while not stopped:
                status = child.poll()
                if status is not None:
                    observed_exit_status = status
                    message = ('Wine desktop exited; ending its dedicated session.' if appeared else
                               'Wine desktop exited before Quantower Starter was observed.')
                    log(message, event='child_exited',
                        level='INFO' if appeared and status == 0 else 'ERROR', child_exit_code=status,
                        child_signal=-status if status < 0 else None, starter_observed=appeared)
                    return 0 if appeared and status == 0 else 1
                current = prefix_processes(prefix)
                if current:
                    if not appeared:
                        log('Quantower Starter appeared.', event='starter_observed',
                            phase='running', starter_pids=sorted(current))
                        # Capture the real Starter identity once it has appeared;
                        # the initial child scan can precede its process creation.
                        session.observe()
                    appeared, gone = True, None
                elif current == set() and appeared:
                    gone = gone or time.monotonic()
                    if time.monotonic() - gone >= 3:
                        log('Quantower Starter disappeared; application exit status is unavailable.',
                            level='WARNING', event='starter_disappeared',
                            phase='stopping', starter_pids=[])
                        return 0
                elif not appeared and time.monotonic() >= deadline:
                    log('Startup failed: Starter did not appear within 90 seconds.', level='ERROR', event='startup_timeout')
                    return 1
                diagnostics.heartbeat(phase='running' if appeared else 'starting', child_pid=child.pid,
                                      starter_pids=sorted(current) if current is not None else None)
                time.sleep(0.5)
            log('Launcher stopped; ending its dedicated session.', level='WARNING', event='launcher_signal', signal=stopped)
            return 128 + stopped
        except (OSError, subprocess.SubprocessError, RuntimeError):
            log('Dedicated Wine desktop supervision failed.', level='ERROR', exception=True)
            return 1
        finally:
            if owned:
                before_cleanup = (observed_exit_status if observed_exit_status is not None
                                  else child.poll())
                cleaned = cleanup()
                log('Dedicated session cleanup finished.', event='cleanup_finished',
                    level='INFO' if cleaned else 'WARNING', cleanup_complete=cleaned)
                # poll() only reaps an already-exited direct child; all signaling
                # uses the guarded session identities above, never a bare PID.
                # A compositor can fail during teardown after Starter disappears.
                # Record that result as well as exits observed in the monitor loop.
                final_status = child.poll()
                if final_status is not None:
                    cleanup_sent_term = (before_cleanup is None and
                                         any(identity.pid == child.pid and signum == signal.SIGTERM
                                             for identity, signum in session.sent_signals))
                    expected_term = final_status == -signal.SIGTERM and cleanup_sent_term
                    unexpected = final_status != 0 and not expected_term
                    log('Dedicated renderer final process status.', event='child_final_status',
                        level='ERROR' if unexpected else 'INFO', child_exit_code=final_status,
                        child_final_exit_code=final_status,
                        child_signal=-final_status if final_status < 0 else None,
                        cleanup_sent_child_sigterm=cleanup_sent_term,
                        child_exit_unexpected=unexpected)
                else:
                    log('Renderer status remains unavailable after deferred cleanup.',
                        event='child_final_status_unavailable', level='WARNING')


if __name__ == '__main__':
    from diagnostics import run
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, default=Path(__file__).resolve().parent)
    raise SystemExit(run(parser.parse_args().base))
