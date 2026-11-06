"""Offline evidence-gate tests; never compile or execute a native probe or Wine."""
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    'window_snapping_runner', ROOT / 'tests/native/window-snapping/run-container.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def report(suite, expected='candidate'):
    rows = []
    for case in runner.cases(suite):
        correct = expected == 'candidate' or not case['sticky']
        probe = {'failures': 0, 'samples': 250, 'enters': 1, 'exits': 1,
                 'capture_seen': True, 'snapped_samples': 20, 'passed': correct,
                 'injection_valid': True, 'exact_expected_geometry': correct}
        rows.append({'name': case['name'], 'exit_code': 0, 'probe': probe})
    return {'fixtures_unchanged': True, 'cases': rows}


class WindowSnappingEvidenceTests(unittest.TestCase):
    def test_matrix_keeps_bounded_child_keyboard_controls_separate(self):
        self.assertEqual(len(runner.cases('main')), 61)
        self.assertEqual(len(runner.cases('snap-hold')), 9)
        cases = runner.cases('supplemental')
        self.assertEqual(len(cases), 38)
        bounded = [c for c in cases if c['exe'] == 'child-keyboard-v4.exe']
        self.assertEqual([c['name'] for c in bounded], [
            'child-full-keyboard-border-nosnap', 'child-full-keyboard-border-cancel'])
        self.assertNotIn('child-full-keyboard-border-snap', [c['name'] for c in cases])
        self.assertEqual(sum(c['sticky'] for c in cases), 14)

    def test_candidate_geometry_failure_never_passes_as_valid_injection(self):
        data = report('main')
        self.assertTrue(runner.verify(data, 'main', 'candidate'))
        data['cases'][0]['probe']['passed'] = False
        self.assertFalse(runner.verify(data, 'main', 'candidate'))

    def test_invalid_delivery_nonzero_exit_and_mutated_fixture_are_refused(self):
        for change in ('capture', 'samples', 'exit', 'fixture'):
            with self.subTest(change=change):
                data = report('main')
                if change == 'capture':
                    data['cases'][0]['probe']['capture_seen'] = False
                elif change == 'samples':
                    data['cases'][0]['probe']['samples'] = 0
                elif change == 'exit':
                    data['cases'][0]['exit_code'] = 1
                else:
                    data['fixtures_unchanged'] = False
                self.assertFalse(runner.verify(data, 'main', 'candidate'))
        data = report('supplemental')
        data['cases'][0]['probe']['injection_valid'] = False
        self.assertFalse(runner.verify(data, 'supplemental', 'candidate'))

    def test_baseline_requires_the_exact_regression_set(self):
        data = report('main', 'baseline')
        self.assertTrue(runner.verify(data, 'main', 'baseline'))
        sticky = next(i for i, c in enumerate(runner.cases('main')) if c['sticky'])
        control = next(i for i, c in enumerate(runner.cases('main')) if not c['sticky'])
        data['cases'][sticky]['probe']['passed'] = True
        data['cases'][control]['probe']['passed'] = False
        # The failure count is unchanged, but the wrong behavior failed.
        self.assertFalse(runner.verify(data, 'main', 'baseline'))
        self.assertFalse(runner.verify(report('main'), 'main', 'baseline'))

    def test_snapping_must_still_engage_at_the_hold_endpoint(self):
        data = report('snap-hold')
        self.assertTrue(runner.verify(data, 'snap-hold', 'candidate'))
        data['cases'][0]['probe']['snapped_samples'] = 0
        self.assertFalse(runner.verify(data, 'snap-hold', 'candidate'))

    def test_missing_duplicate_or_reordered_results_and_unknown_mode_fail(self):
        for change in ('missing', 'duplicate', 'reordered'):
            with self.subTest(change=change):
                data = report('main')
                if change == 'missing':
                    data['cases'].pop()
                elif change == 'duplicate':
                    data['cases'][1] = data['cases'][0]
                else:
                    data['cases'][0], data['cases'][1] = data['cases'][1], data['cases'][0]
                self.assertFalse(runner.verify(data, 'main', 'candidate'))
        self.assertFalse(runner.verify(report('main'), 'main', 'unknown'))

    def test_network_guard_rejects_external_interfaces(self):
        with mock.patch.object(runner.Path, 'is_file', return_value=True), \
             mock.patch.object(runner.Path, 'iterdir', return_value=iter([
                 Path('/sys/class/net/lo'), Path('/sys/class/net/eth0')])), \
             mock.patch.object(runner.os, 'getuid', return_value=1000), \
             mock.patch.dict(runner.os.environ, {'QTK_SYNTHETIC_DATAONLY': '1'}):
            with self.assertRaisesRegex(RuntimeError, 'network none'):
                runner.container_guard()

    def test_preflight_refusal_occurs_before_native_process_or_output_creation(self):
        arguments = ['run-container.py', '--suite', 'main', '--expect', 'candidate', '--label', 'test']
        with mock.patch.object(sys, 'argv', arguments), \
             mock.patch.object(runner, 'container_guard', side_effect=RuntimeError('guard refusal')), \
             mock.patch.object(runner.subprocess, 'Popen') as popen, \
             mock.patch.object(runner.Path, 'mkdir') as mkdir:
            with self.assertRaisesRegex(RuntimeError, 'guard refusal'):
                runner.main()
            popen.assert_not_called()
            mkdir.assert_not_called()


if __name__ == '__main__':
    unittest.main()
