"""Keep lookup floods readable without losing raw output or real failures."""

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import diagnostics


FUNCTIONS = ('NtCreateFile', 'NtQueryFullAttributesFile', 'NtQueryAttributesFile')
STATUSES = ('c0000034', 'c000003a')


def probe(function='NtQueryFullAttributesFile', status='c0000034', name='optional.dll',
          prefix='0012:0034:', channel='file', level='warn'):
    return (f'{prefix}{level}:{channel}:{function} L"C:\\synthetic\\{name}" '
            f'not found ({status})\n').encode()


class LookupLoggingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        previous = os.umask(0o077)
        self.addCleanup(os.umask, previous)
        platform = mock.patch.object(diagnostics.platform, 'platform',
                                     return_value='isolated test host')
        platform.start()
        self.addCleanup(platform.stop)

    def log(self):
        log = diagnostics.SessionLog(self.base)

        def close():
            if not log.output.closed:
                log.finish(1)

        self.addCleanup(close)
        return log

    def events(self):
        return [json.loads(line) for line in
                (self.base / 'logs/events.jsonl').read_text().splitlines()]

    def reports(self):
        return [event for event in self.events() if event['event'] == 'file_lookup_probes']

    def summary(self):
        return json.loads((self.base / 'logs/latest-session.json').read_text())

    def mirrored(self):
        return (self.base / 'logs/launch.log').read_text()

    def test_all_supported_lookup_signatures_are_counted_and_sampled_once(self):
        log = self.log()
        payload = b''
        names = []
        for function in FUNCTIONS:
            for status in STATUSES:
                name = f'error-{function}-{status}.dll'
                names.append(name)
                line = probe(function, status, name, prefix='123.456:0012:0034:')
                payload += line * 2
        log.output.write(payload)
        log.finish(0)

        summary = self.summary()
        self.assertEqual(summary['runtime_line_counts'], {'INFO': 12, 'WARNING': 0, 'ERROR': 0})
        self.assertEqual(summary['runtime_file_lookup_probes'],
                         {function: {status: 2 for status in STATUSES}
                          for function in FUNCTIONS})
        for name in names:
            self.assertEqual(self.mirrored().count(name), 1, name)
        self.assertEqual((log.directory / 'runtime.raw.log').read_bytes(), payload)
        self.assertEqual(sum(event['probe_count'] for event in self.reports()), 12)

    def test_other_file_warnings_and_errors_are_never_grouped(self):
        log = self.log()
        payload = b''.join((
            probe(status='c0000022', name='access-error.dll'),
            probe(function='CreateFileW', name='different-function.dll'),
            probe(channel='module', name='different-channel.dll'),
            probe(level='err', name='real-error.dll'),
            probe(name='extra-suffix.dll').rstrip(b'\n') + b' unexpected suffix\n',
            b'0012:warn:file:fd_set_file_info FS cache is out of space, expect performance problems\n',
            b'0012:warn:file:NtCreateFile cannot retrieve filesystem type\n',
            b'0012:warn:module:LdrGetProcedureAddress GetErrorMessageW not found\n',
        ))
        log.output.write(payload)
        log.finish(0)

        summary = self.summary()
        self.assertEqual(summary['runtime_line_counts'], {'INFO': 0, 'WARNING': 7, 'ERROR': 1})
        self.assertEqual(sum(sum(statuses.values()) for statuses in
                             summary['runtime_file_lookup_probes'].values()), 0)
        self.assertEqual(self.reports(), [])
        for line in payload.decode().splitlines():
            self.assertIn(line, self.mirrored())
        self.assertEqual((log.directory / 'runtime.raw.log').read_bytes(), payload)

    def test_fatal_markers_and_first_chance_exception_evidence_remain_visible(self):
        log = self.log()
        failures = (
            'Fatal error.',
            'Description: The process was terminated due to an unhandled exception.',
            'System.Environment.FailFast: deliberate synthetic failure',
            "_XAllocID: Assertion 'ret != inval_id' failed.",
            'Segmentation fault (core dumped)',
            '0012:warn:vulkan:create_device VK_ERROR_DEVICE_LOST',
            'bare access violation in synthetic worker',
        )
        first_chance = ('0012:warn:seh:dispatch_exception '
                        'EXCEPTION_ACCESS_VIOLATION exception (code=c0000005) raised')
        payload = ('\n'.join(failures + (first_chance,)) + '\n').encode()
        log.output.write(payload)
        log.finish(1)

        self.assertEqual(self.summary()['runtime_line_counts'],
                         {'INFO': 0, 'WARNING': 1, 'ERROR': len(failures)})
        for failure in failures:
            self.assertTrue(any(' ERROR ' in line and failure in line
                                for line in self.mirrored().splitlines()), failure)
        self.assertIn(first_chance, self.mirrored())
        self.assertEqual((log.directory / 'runtime.raw.log').read_bytes(), payload)

    def test_large_lookup_flood_preserves_raw_bytes_and_bounds_mirrored_work(self):
        log = self.log()
        lookups = probe(name='repeated-optional-error.dll') * 10000
        warning = b'0012:warn:file:fd_set_file_info failed to set an extended attribute\n'
        failure = b'0012:err:file:NtCreateFile actual filesystem error (c0000022)\n'
        payload = lookups + warning + failure
        log.output.write(payload)
        log.finish(1)

        summary = self.summary()
        self.assertFalse(summary['output_reader_failed'])
        self.assertEqual(summary['output_bytes'], len(payload))
        self.assertEqual(summary['runtime_line_counts'],
                         {'INFO': 10000, 'WARNING': 1, 'ERROR': 1})
        self.assertEqual(summary['runtime_file_lookup_probes']['NtQueryFullAttributesFile']['c0000034'],
                         10000)
        self.assertEqual((log.directory / 'runtime.raw.log').read_bytes(), payload)
        self.assertEqual(self.mirrored().count('repeated-optional-error.dll'), 1)
        self.assertLess(len(self.mirrored().encode()), 10000)
        self.assertIn(warning.decode().strip(), self.mirrored())
        self.assertIn(failure.decode().strip(), self.mirrored())
        self.assertEqual(sum(event['probe_count'] for event in self.reports()), 10000)

    def test_periodic_and_final_reports_conserve_counts_without_duplicate_reports(self):
        log = self.log()
        # No real process or wall-clock wait is needed to cross the report deadline.
        log.file_lookup_next_report = float('inf')
        for _ in range(3):
            log._runtime_line(probe())
        log._flush_file_lookup_probes()
        self.assertEqual(self.reports(), [])

        log.file_lookup_next_report = 0
        log._flush_file_lookup_probes()
        self.assertEqual([event['probe_count'] for event in self.reports()], [3])
        for _ in range(2):
            log._runtime_line(probe())
        log._flush_file_lookup_probes()
        self.assertEqual([event['probe_count'] for event in self.reports()], [3])

        log._flush_file_lookup_probes(force=True)
        log._flush_file_lookup_probes(force=True)
        log.finish(0)
        reports = self.reports()
        self.assertEqual([event['probe_count'] for event in reports], [3, 2])
        self.assertEqual([event['total_probe_count'] for event in reports], [3, 5])
        self.assertEqual(sum(event['probe_count'] for event in reports), 5)
        self.assertEqual(self.summary()['runtime_file_lookup_probes']['NtQueryFullAttributesFile']['c0000034'], 5)

    def test_heartbeat_exposes_lookup_counts_before_shutdown(self):
        log = self.log()
        for _ in range(4):
            log._runtime_line(probe(function='NtCreateFile', status='c000003a'))
        log.heartbeat(phase='running')
        summary = self.summary()
        self.assertEqual(summary['status'], 'running')
        self.assertEqual(summary['runtime_file_lookup_probes']['NtCreateFile']['c000003a'], 4)
        self.assertEqual(summary['runtime_line_counts'], {'INFO': 4, 'WARNING': 0, 'ERROR': 0})
        log._flush_file_lookup_probes(force=True)
        log.finish(0)


if __name__ == '__main__':
    unittest.main()
