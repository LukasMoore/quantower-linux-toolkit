#!/usr/bin/env python3
"""Bounded local diagnostics for the dedicated Quantower launcher (stdlib only)."""
from __future__ import annotations

import argparse
import contextlib
from datetime import datetime, timezone
import faulthandler
import fcntl
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import platform
import re
import secrets
import shutil
import signal
import sys
import threading
import time
import traceback

TEXT_BYTES = 10 * 1024 * 1024
EVENT_BYTES = 2 * 1024 * 1024
BACKUPS = 4
SESSION_KEEP = 20
SESSION_DAYS = 30
SESSION_BYTES = 100 * 1024 * 1024
LINE_BYTES = 8192
DEFAULT_WINEDEBUG = '-all,err+all,warn+all'
ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')
WINE_MESSAGE = re.compile(
    r'^\s*(?:\d+\.\d+:)?(?:[0-9a-fA-F]+:)*'
    r'(?P<level>err|warn|fixme|trace):(?P<channel>[^:\s]+):(?P<function>[^\s]+)(?:\s+|$)')
FILE_LOOKUP_MISS = re.compile(r'L"[^\r\n]*" not found \((c0000034|c000003a)\)')
FILE_LOOKUP_FUNCTIONS = ('NtCreateFile', 'NtQueryFullAttributesFile', 'NtQueryAttributesFile')
FILE_LOOKUP_STATUSES = ('c0000034', 'c000003a')
FILE_LOOKUP_REPORT_SECONDS = 60
FATAL_MESSAGE = re.compile(
    r'\bfatal error\b|\bassertion\b[^\r\n]*\bfailed\b|\bunhandled exception\b|'
    r'\bfailfast\b|\bVK_ERROR_DEVICE_LOST\b|\bsegmentation fault\b', re.IGNORECASE)


def file_lookup_counters():
    # Fixed keys keep memory bounded even when every lookup uses a new path.
    return {function: dict.fromkeys(FILE_LOOKUP_STATUSES, 0) for function in FILE_LOOKUP_FUNCTIONS}


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


def atomic_json(path, value):
    temporary = path.with_name('.' + path.name + '.tmp')
    try:
        with temporary.open('w', encoding='utf-8') as stream:
            os.chmod(temporary, 0o600)
            json.dump(value, stream, ensure_ascii=True, indent=2)
            stream.write('\n')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class PrivateRotatingHandler(RotatingFileHandler):
    """A full disk must not kill the reader and block the trading process."""
    def __init__(self, *args, **kwargs):
        self.failures = 0
        super().__init__(*args, **kwargs)

    def _open(self):
        stream = super()._open()
        os.chmod(self.baseFilename, 0o600)
        return stream

    def handleError(self, record):
        self.failures += 1
        if self.failures == 1:
            try:
                os.write(2, b'Quantower logging failed; output may be lost. Check free disk space/permissions.\n')
            except OSError:
                pass


class UTCFormatter(logging.Formatter):
    converter = time.gmtime


class PythonOutput:
    def __init__(self, diagnostics, level):
        self.diagnostics, self.level = diagnostics, level

    def write(self, message):
        for line in str(message).splitlines():
            if line:
                self.diagnostics.text(line, self.level, source='python')
        return len(message)

    def flush(self):
        pass


class SessionLog:
    def __init__(self, base, *, text_bytes=TEXT_BYTES, event_bytes=EVENT_BYTES):
        self.base = Path(base)
        self.logs = self.base / 'logs'
        self.logs.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.logs, 0o700)
        self.session_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + secrets.token_hex(4)
        self.directory = self.logs / 'sessions' / self.session_id
        self.directory.mkdir(mode=0o700, parents=True)
        os.chmod(self.directory.parent, 0o700)
        for name in ('gpu', 'native-assert'):
            (self.directory / name).mkdir(mode=0o700)
        self.started = time.monotonic()
        self.record = dict(schema_version=1, session_id=self.session_id, started_at=utc_now(),
                           supervisor_pid=os.getpid(), status='running', phase='startup')
        self.handlers = []
        self.loggers = []
        for name, size, formatter in (
            ('launch.log', text_bytes, UTCFormatter('%(asctime)s.%(msecs)03dZ %(levelname)s %(message)s', '%Y-%m-%dT%H:%M:%S')),
            ('events.jsonl', event_bytes, logging.Formatter('%(message)s')),
        ):
            handler = PrivateRotatingHandler(self.logs / name, maxBytes=size, backupCount=BACKUPS,
                                            encoding='utf-8', errors='backslashreplace')
            handler.setFormatter(formatter)
            logger = logging.Logger(self.session_id + name, level=logging.DEBUG)
            logger.propagate = False
            logger.addHandler(handler)
            self.handlers.append(handler)
            self.loggers.append(logger)
        # Children inherit an append-only regular file, never a pipe to this supervisor.
        # If supervision dies or cleanup defers to an active Starter, future writes must
        # not get SIGPIPE/EPIPE. The reader mirrors this evidence into the rolling log.
        raw = self.directory / 'runtime.raw.log'
        self.output = raw.open('ab', buffering=0)
        os.chmod(raw, 0o600)
        self.reader = raw.open('rb', buffering=0)
        self.stop_reader = threading.Event()
        self.drain_deadline = None
        self.drain_truncated = False
        self.reader_failed = False
        self.output_bytes = 0
        self.last_output_at = None
        self.runtime_levels = dict(INFO=0, WARNING=0, ERROR=0)
        self.file_lookup_counts = file_lookup_counters()
        self.file_lookup_reported_counts = file_lookup_counters()
        self.file_lookup_next_report = time.monotonic() + FILE_LOOKUP_REPORT_SECONDS
        self.summary_failures = 0
        self.thread = threading.Thread(target=self._pump, name='quantower-output', daemon=True)
        self.thread.start()
        self.fault_output = (self.directory / 'python-fault.log').open('ab', buffering=0)
        faulthandler.enable(file=self.fault_output, all_threads=True)
        # An explicit diagnostic signal writes Python stacks; it never signals Wine.
        faulthandler.register(signal.SIGUSR1, file=self.fault_output, all_threads=True)
        self.next_heartbeat = 0
        try:
            previous = json.loads((self.logs / 'latest-session.json').read_text())
            if not isinstance(previous, dict):
                raise ValueError('Prior session summary must be a JSON object.')
            if previous.get('status') == 'running':
                self.event('previous_session_unclean', 'Previous supervisor did not record a final result.',
                           level='WARNING', previous_session_id=previous.get('session_id'))
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            self.event('previous_summary_unreadable', 'Cannot read prior session summary.', level='WARNING', exception=True)
        self.event('session_started', 'Launcher diagnostics started.',
                   platform=platform.platform(), python=platform.python_version(),
                   wine_debug=os.environ.get('QUANTOWER_WINEDEBUG', DEFAULT_WINEDEBUG))
        self._save()
        try:
            self._prune_sessions()
        except OSError:
            self.event('retention_failed', 'Cannot prune old diagnostic sessions.', level='WARNING', exception=True)

    def text(self, message, level='INFO', source='supervisor'):
        clean = ANSI.sub('', str(message)).replace('\r', r'\r').replace('\x00', r'\0')
        # Bound even an accidental large Python message. Runtime bytes are capped by the pump.
        for line in clean.splitlines() or ['']:
            for offset in range(0, max(len(line), 1), LINE_BYTES):
                self.loggers[0].log(getattr(logging, level),
                                    f'[{self.session_id}] [{source}] {line[offset:offset + LINE_BYTES]}')

    def event(self, event, message, *, level='INFO', exception=False, **fields):
        if level == 'ERROR' and (event != 'session_finished' or 'last_error' not in self.record):
            self.record['last_error'] = dict(event=event, message=message, timestamp=utc_now())
        durable_fields = ('profile', 'child_pid', 'child_exit_code', 'child_signal', 'cleanup_complete',
                      'x11_runtime', 'application_version', 'child_final_exit_code', 'child_exit_unexpected',
                      'cleanup_required_force', 'cleanup_forced_wine_helpers', 'phase', 'starter_pids',
                      'requested_window_mode', 'window_mode', 'display_backend', 'effective_wine_dpi',
                      'child_role', 'child_exited', 'handoff_pending', 'cleanup_sent_child_sigterm')
        for field in durable_fields:
            if field in fields:
                self.record[field] = fields[field]
        if fields.get('cleanup_complete') is True:
            self.record['starter_pids'] = []
        detail = traceback.format_exc() if exception else None
        self.text(message, level)
        if detail:
            self.text(detail, level)
        record = dict(timestamp=utc_now(), session_id=self.session_id, level=level,
                      event=event, message=message, **fields)
        if detail:
            record['exception'] = detail
        self.loggers[1].log(getattr(logging, level), json.dumps(record, ensure_ascii=True))
        # Persist lifecycle observations immediately; the next heartbeat can be 60s
        # away. Heartbeat and finish already save after their remaining updates.
        if event != 'heartbeat' and any(field in fields for field in durable_fields):
            self._save()

    def _pump(self):
        pending = b''
        last_data = time.monotonic()
        try:
            while True:
                data = self.reader.read(65536)
                if not data:
                    if pending and time.monotonic() - last_data >= 1:
                        self._runtime_line(pending)
                        pending = b''
                    self._flush_file_lookup_probes()
                    if self.stop_reader.is_set():
                        break
                    self.stop_reader.wait(0.1)
                    continue
                self.output_bytes += len(data)
                self.last_output_at = utc_now()
                last_data = time.monotonic()
                pending += data
                while pending:
                    newline = pending.find(b'\n', 0, LINE_BYTES)
                    if newline < 0 and len(pending) < LINE_BYTES:
                        break
                    length = newline + 1 if newline >= 0 else LINE_BYTES
                    self._runtime_line(pending[:length])
                    pending = pending[length:]
                self._flush_file_lookup_probes()
                if self.drain_deadline is not None and time.monotonic() >= self.drain_deadline:
                    self.drain_truncated = True
                    break
            if pending:
                self._runtime_line(pending)
        except Exception:
            self.reader_failed = True
            self.event('output_reader_failed', 'Runtime output reader failed.', level='ERROR', exception=True)
        finally:
            self._flush_file_lookup_probes(force=True)
            self.reader.close()

    def _runtime_line(self, data):
        text = data.decode('utf-8', errors='replace').rstrip('\n')
        clean = ANSI.sub('', text)
        message = WINE_MESSAGE.match(clean)
        # These statuses describe a failed candidate lookup, not a failed load.
        # Keep all original lines in runtime.raw.log and show one sample for each
        # function/status. Access denied and all other file warnings stay visible.
        if (message and message['level'] == 'warn' and message['channel'] == 'file'
                and message['function'] in self.file_lookup_counts):
            miss = FILE_LOOKUP_MISS.fullmatch(clean[message.end():])
            if miss:
                counts = self.file_lookup_counts[message['function']]
                counts[miss[1]] += 1
                self.runtime_levels['INFO'] += 1
                if counts[miss[1]] == 1:
                    self.text('File lookup probe (repeats counted; full output in runtime.raw.log): ' + text,
                              'INFO', source='runtime')
                return
        if FATAL_MESSAGE.search(clean):
            level = 'ERROR'
        elif message:
            # Wine's class is more precise than words inside a DLL/file name.
            level = {'err': 'ERROR', 'warn': 'WARNING'}.get(message['level'], 'INFO')
        else:
            lowered = clean.lower()
            level = ('ERROR' if any(word in lowered for word in (
                'err:', 'error', 'fatal', 'assertion failed', 'access violation'))
                     else 'WARNING' if 'warn' in lowered else 'INFO')
        self.runtime_levels[level] += 1
        self.text(text, level, source='runtime')

    def _file_lookup_snapshot(self):
        return {function: dict(counts) for function, counts in self.file_lookup_counts.items()}

    def _flush_file_lookup_probes(self, *, force=False):
        # Only the reader updates/report these counters, including its final drain.
        now = time.monotonic()
        if not force and now < self.file_lookup_next_report:
            return
        self.file_lookup_next_report = now + FILE_LOOKUP_REPORT_SECONDS
        counts = self._file_lookup_snapshot()
        delta = {function: {status: count - self.file_lookup_reported_counts[function][status]
                            for status, count in values.items()}
                 for function, values in counts.items()}
        probe_count = sum(sum(values.values()) for values in delta.values())
        if probe_count:
            self.event('file_lookup_probes',
                       f'Grouped {probe_count} file/path-not-found lookup probes since the previous report; '
                       'all original lines remain in runtime.raw.log.',
                       probe_count=probe_count, probe_counts=delta,
                       total_probe_count=sum(sum(values.values()) for values in counts.values()))
            self.file_lookup_reported_counts = counts

    def _save(self):
        self.record['updated_at'] = utc_now()
        self.record['logging_write_failures'] = sum(h.failures for h in self.handlers)
        self.record['summary_write_failures'] = self.summary_failures
        try:
            atomic_json(self.directory / 'summary.json', self.record)
            atomic_json(self.logs / 'latest-session.json', self.record)
        except OSError:
            # Logging failures must not turn into trading-session failures.
            self.summary_failures += 1
            if self.summary_failures == 1:
                self.event('summary_write_failed', 'Cannot update diagnostic summary; supervision continues.',
                           level='ERROR', exception=True)

    def heartbeat(self, **fields):
        if time.monotonic() < self.next_heartbeat:
            return
        self.next_heartbeat = time.monotonic() + 60
        self.record.update(fields)
        self.record['uptime_seconds'] = round(time.monotonic() - self.started, 1)
        self.record.update(output_bytes=self.output_bytes, last_output_at=self.last_output_at,
                           output_reader_failed=self.reader_failed, runtime_line_counts=dict(self.runtime_levels),
                           runtime_file_lookup_probes=self._file_lookup_snapshot())
        self.event('heartbeat', 'Supervisor is monitoring the session.', **fields,
                   uptime_seconds=self.record['uptime_seconds'], output_bytes=self.output_bytes,
                   last_output_at=self.last_output_at, output_reader_failed=self.reader_failed)
        self._save()

    def _prune_sessions(self):
        # Never touch legacy logs/runtime backups or Quantower's own vendor logs.
        now = time.time()
        entries = []
        for directory in self.directory.parent.iterdir():
            if directory == self.directory or directory.is_symlink() or not directory.is_dir():
                continue
            if not re.fullmatch(r'\d{8}T\d{6}Z-[0-9a-f]{8}', directory.name):
                continue
            # An orphaned app can still hold its raw output file after supervision
            # ended. Preserve unfinished/deferred/unknown sessions for manual review.
            try:
                summary = json.loads((directory / 'summary.json').read_text())
                if not isinstance(summary, dict) or summary.get('status') not in ('finished', 'failed'):
                    continue
                if summary.get('cleanup_complete') is False:
                    continue
                if summary.get('child_pid') is not None and summary.get('cleanup_complete') is not True:
                    continue
            except (OSError, ValueError):
                continue
            size = sum(p.stat().st_size for p in directory.rglob('*') if p.is_file() and not p.is_symlink())
            entries.append((directory, directory.stat().st_mtime, size))
        entries.sort(key=lambda item: item[1], reverse=True)
        retained_size = 0
        for index, (directory, modified, size) in enumerate(entries):
            retained_size += size
            if index >= SESSION_KEEP - 1 or now - modified > SESSION_DAYS * 86400 or retained_size > SESSION_BYTES:
                shutil.rmtree(directory)
                self.event('session_pruned', 'Removed expired session diagnostics.', removed_session_id=directory.name)

    def finish(self, code):
        self.output.close()
        self.drain_deadline = time.monotonic() + 2
        self.stop_reader.set()
        self.thread.join(timeout=3)
        if self.thread.is_alive() or self.drain_truncated:
            self.event('output_drain_timeout', 'Stopped mirroring runtime output at the shutdown deadline; raw output is retained.', level='WARNING')
        final_phase = 'finished' if code == 0 else 'failed'
        self.record.update(status=final_phase, phase=final_phase, exit_code=code,
                           ended_at=utc_now(), duration_seconds=round(time.monotonic() - self.started, 2),
                           output_reader_failed=self.reader_failed,
                           output_bytes=self.output_bytes, last_output_at=self.last_output_at,
                           runtime_line_counts=dict(self.runtime_levels),
                           runtime_file_lookup_probes=self._file_lookup_snapshot(),
                           logging_write_failures=sum(h.failures for h in self.handlers))
        if self.record.get('cleanup_complete') is True:
            self.record['starter_pids'] = []
        self.event('session_finished', 'Launcher session finished.', level='INFO' if code == 0 else 'ERROR',
                   exit_code=code, duration_seconds=self.record['duration_seconds'])
        self._save()
        faulthandler.unregister(signal.SIGUSR1)
        faulthandler.disable()
        self.fault_output.close()
        for logger, handler in zip(self.loggers, self.handlers):
            logger.removeHandler(handler)
            handler.close()


def run(base):
    """Acquire the existing ownership lock BEFORE rotating or writing session files."""
    os.umask(0o077)
    diagnostics = None
    code = 1
    try:
        base = Path(base).resolve()
        logs = base / 'logs'
        logs.mkdir(mode=0o700, parents=True, exist_ok=True)
        with (logs / 'launch.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                print('Quantower launcher already owns the session; leaving it running.', file=sys.stderr)
                return 0
            diagnostics = SessionLog(base)
            try:
                with contextlib.redirect_stdout(PythonOutput(diagnostics, 'INFO')), contextlib.redirect_stderr(PythonOutput(diagnostics, 'ERROR')):
                    import supervisor
                    code = supervisor.main(base=base, diagnostics=diagnostics)
                    if code == 0 and (diagnostics.record.get('child_exit_unexpected') or
                                      diagnostics.record.get('cleanup_complete') is False):
                        code = 1
            except KeyboardInterrupt:
                code = 130
                diagnostics.event('launcher_interrupted', 'Launcher interrupted.', level='WARNING')
            except Exception:
                diagnostics.event('launcher_exception', 'Unhandled launcher failure.', level='ERROR', exception=True)
            finally:
                diagnostics.finish(code)
        return code
    except Exception:
        # Includes failures to initialize/write logs: keep a visible terminal fallback.
        traceback.print_exc(file=sys.stderr)
        return 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['run'])
    parser.add_argument('--base', type=Path, default=Path(__file__).resolve().parent)
    arguments = parser.parse_args()
    raise SystemExit(run(arguments.base))
