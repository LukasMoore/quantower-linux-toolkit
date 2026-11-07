"""Offline original-tooltip evidence/refusal gates; no Wine or desktop access."""
import copy
import hashlib
import importlib.util
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests/native/integrated-windows/tooltip-coordinates'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runner = load('tooltip_runner', FIXTURES / 'run-container.py')
builder = load('tooltip_builder', FIXTURES / 'build.py')
POINTS = ((200, 200), (400, 300), (0, 100), (-100, 100), (-500, 100),
          (100, -100), (100, -500), (-100, -100), (-500, -500))
DIGEST = 'a' * 64


def report(expect='candidate', family='v5'):
    result = {'synthetic_only': True, 'display': 'own-Xvfb-noWM', 'family': family,
              'fixtures_unchanged': True, 'cases': []}
    for dpi in (96, 192):
        rows = [{'kind': 'inventory', 'units': 'PMv2-caller-screen', 'dpi': dpi, 'work': [0, 0, 1920, 1080]}]
        for x, y in POINTS:
            passed = expect == 'candidate' or (x >= 0 and y >= 0)
            expected = [max(x, 0), y]
            actual = list(expected) if passed else [1800 if x < 0 else x, 800 if y < 0 else y]
            rows.append({'kind': 'case', 'request': [x, y], 'expected_client_origin': expected,
                'actual_client_origin': actual, 'window_rect': [actual[0]-1, actual[1]-1, actual[0]+100, actual[1]+20],
                'passed': passed})
        rows.append({'kind': 'result', 'cases': 9, 'failures': 0 if expect == 'candidate' else 6, 'host_ui_test': False})
        module = 'comctl32.dll' if family == 'v5' else 'comctl32_v6.dll'
        result['cases'].append({'dpi': dpi, 'exit_code': 0 if expect == 'candidate' else 1,
            # Pure offline classification inputs, never injected Wine geometry.
            'monitor_inventory':[{'kind':'active-monitor','work':[0,0,1920,1080]},
                {'kind':'active-monitor','work':[-1920,-1080,1920,1080]},
                {'kind':'monitor-result','dpi':dpi,'count':2,'failures':0}],
            'rows': rows, 'result': rows[-1], 'exact_module_verified': True,
            'mapped_comctl32': {'/runtime/wine/lib/wine/x86_64-windows/' + module: DIGEST},
            'identities': [{'pid': 10, 'start_ticks': 20, 'uid': 1000,
                'executable': '/runtime/wine/lib/wine/x86_64-unix/wine-preloader'}]})
    return result


class TooltipEvidenceTests(unittest.TestCase):
    def test_exact_negative_and_nonnegative_matrix_for_both_families(self):
        self.assertEqual(runner.REQUESTS, POINTS)
        self.assertEqual(sum(x < 0 or y < 0 for x, y in POINTS), 6)
        for family in ('v5', 'v6'):
            for expect in ('baseline', 'candidate'):
                self.assertTrue(runner.verify_report(report(expect, family), expect, family, DIGEST))

    def test_coordinate_pass_does_not_hide_collapsed_or_invalid_monitor_targets(self):
        data=report()
        for case in data['cases']:
            case['monitor_inventory']=[{'kind':'active-monitor','work':[0,0,1920,1080]},
                {'kind':'monitor-result','dpi':case['dpi'],'count':1,'failures':0}]
        data['cases'][0]['rows'][5]['window_rect']=[-1,99,-1,118]
        self.assertTrue(runner.verify_coordinate_evidence(data,'candidate','v5',DIGEST))
        self.assertFalse(runner.verify_report(data,'candidate','v5',DIGEST))
        geometry=runner.classify_targets(data)
        self.assertTrue(geometry['monitor_catalogue_verified'])
        self.assertFalse(geometry['all_rectangles_nonempty'])
        self.assertFalse(geometry['all_targets_inside_workareas'])
        self.assertFalse(geometry['negative_monitor_acceptance'])
        self.assertEqual(geometry['valid_negative_monitor_targets'],0)
        self.assertEqual(len(geometry['outside_workarea_cases']),12)
        self.assertEqual(len(geometry['collapsed_rectangle_cases']),1)

    def test_missing_or_malformed_actual_monitor_catalogue_cannot_pass_visibility(self):
        for change in ('missing','count','dpi','extent'):
            data=report()
            case=data['cases'][0]
            if change=='missing':case.pop('monitor_inventory')
            elif change=='count':case['monitor_inventory'][-1]['count']=99
            elif change=='dpi':case['monitor_inventory'][-1]['dpi']=192
            else:case['monitor_inventory'][0]['work']=[0,0,0,0]
            with self.subTest(change=change):
                self.assertFalse(runner.verify_report(data,'candidate','v5',DIGEST))

    def test_equal_failure_total_with_wrong_failed_cases_is_refused(self):
        data = report('baseline')
        positive, negative = data['cases'][0]['rows'][1], data['cases'][0]['rows'][4]
        positive.update(passed=False, actual_client_origin=[1800, 800], window_rect=[1799, 799, 1900, 820])
        negative.update(passed=True, actual_client_origin=negative['expected_client_origin'], window_rect=[-1, 99, 100, 120])
        self.assertFalse(runner.verify_report(data, 'baseline', 'v5', DIGEST))

    def test_forged_expected_coordinate_and_invalid_geometry_refuse(self):
        for change in ('expected', 'empty-rect', 'boolean-coordinate', 'wrong-dpi'):
            data = report()
            row = data['cases'][0]['rows'][4]
            if change == 'expected':
                row.update(expected_client_origin=[1800, 100], actual_client_origin=[1800, 100], window_rect=[1799, 99, 1900, 120])
            elif change == 'empty-rect':
                row['window_rect'] = [0, 0, 0, 0]
            elif change == 'boolean-coordinate':
                row['actual_client_origin'] = [False, 100]
            else:
                data['cases'][0]['rows'][0]['dpi'] = 192
            with self.subTest(change=change):
                self.assertFalse(runner.verify_report(data, 'candidate', 'v5', DIGEST))

    def test_missing_reordered_duplicate_or_wrong_family_evidence_refuses(self):
        for change in ('missing', 'reordered', 'duplicate', 'family', 'exit', 'result'):
            data = report()
            if change == 'missing':
                data['cases'][0]['rows'].pop(1)
            elif change == 'reordered':
                rows = data['cases'][0]['rows']; rows[1], rows[2] = rows[2], rows[1]
            elif change == 'duplicate':
                data['cases'][0]['rows'][2] = copy.deepcopy(data['cases'][0]['rows'][1])
            elif change == 'family':
                data['family'] = 'v6'
            elif change == 'exit':
                data['cases'][0]['exit_code'] = 1
            else:
                data['cases'][0]['result']['failures'] = 1
            with self.subTest(change=change):
                self.assertFalse(runner.verify_report(data, 'candidate', 'v5', DIGEST))
        self.assertFalse(runner.verify_report(report(), 'unknown', 'v5', DIGEST))

    def test_unverified_module_missing_birth_root_uid_and_changed_fixture_refuse(self):
        for change in ('hash', 'empty-maps', 'no-identity', 'birth', 'uid', 'role', 'fixture'):
            data = report()
            case = data['cases'][0]
            if change == 'hash':
                case['mapped_comctl32'] = {next(iter(case['mapped_comctl32'])): 'b' * 64}
            elif change == 'empty-maps':
                case['mapped_comctl32'] = {}
            elif change == 'no-identity':
                case['identities'] = []
            elif change == 'birth':
                case['identities'][0]['start_ticks'] = 0
            elif change == 'uid':
                case['identities'][0]['uid'] = 0
            elif change == 'role':
                case['identities'][0]['executable'] = '/usr/bin/Xwayland'
            else:
                data['fixtures_unchanged'] = False
            with self.subTest(change=change):
                self.assertFalse(runner.verify_report(data, 'candidate', 'v5', DIGEST))


class TooltipRefusalTests(unittest.TestCase):
    def test_original_c_manifest_resource_hashes_are_pinned(self):
        self.assertEqual(builder.INPUTS, runner.SOURCE_INPUTS)
        for name, expected in builder.INPUTS.items():
            self.assertEqual(hashlib.sha256((FIXTURES / name).read_bytes()).hexdigest(), expected)

    def test_existing_output_and_symlink_refuse_before_compiler(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            existing = root / 'existing'; existing.mkdir()
            linked = root / 'linked'; linked.symlink_to(root / 'missing')
            for path in (existing, linked):
                with self.subTest(path=path), mock.patch.object(builder.subprocess, 'run') as compile_:
                    with self.assertRaises(FileExistsError):
                        builder.main(['--output-dir', str(path)])
                    compile_.assert_not_called()

    def test_missing_compiler_and_mutated_source_do_not_create_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'new'
            with mock.patch.object(builder.shutil, 'which', return_value=None), mock.patch.object(builder.subprocess, 'run') as compile_:
                with self.assertRaises(ValueError):builder.main(['--output-dir', str(output)])
                self.assertFalse(output.exists()); compile_.assert_not_called()
            changed = dict(builder.INPUTS, **{'tooltip-track-coordinates.c': '0' * 64})
            with mock.patch.object(builder, 'INPUTS', changed), mock.patch.object(builder.subprocess, 'run') as compile_:
                with self.assertRaises(ValueError):builder.main(['--output-dir', str(output)])
                self.assertFalse(output.exists()); compile_.assert_not_called()

    def test_container_refusal_happens_before_output_or_process(self):
        arguments = ['--family', 'v5', '--expect', 'candidate', '--expected-comctl32-sha256', DIGEST]
        with mock.patch.object(runner, 'container_guard', side_effect=RuntimeError('guard refusal')), \
                mock.patch.object(runner.Path, 'mkdir') as mkdir, mock.patch.object(runner.subprocess, 'Popen') as popen:
            with self.assertRaisesRegex(RuntimeError, 'guard refusal'):runner.main(arguments)
            mkdir.assert_not_called(); popen.assert_not_called()

    def test_external_network_refuses(self):
        with mock.patch.object(runner.Path, 'is_file', return_value=True), \
                mock.patch.object(runner.os, 'getuid', return_value=1000), \
                mock.patch.dict(runner.os.environ, {'QTK_SYNTHETIC_DATAONLY': '1'}), \
                mock.patch.object(runner.Path, 'iterdir', return_value=iter([Path('/sys/class/net/lo'), Path('/sys/class/net/eth0')])):
            with self.assertRaises(RuntimeError):runner.container_guard()


class FakeProcess:
    name = '123'

    def __init__(self):
        stat = '123 (own tooltip) ' + ' '.join(['R'] + ['0'] * 18 + ['456'])
        self.uid = 1000
        self.values = {'stat': [stat, stat],
            'environ': [b'WINEPREFIX=/test/results/prefix\0QUANTOWER_LAUNCH_SESSION=fixture-token\0'] * 2,
            'cmdline': b'Z:\\probe\\tooltip-track-coordinates.exe\0',
            'exe': ['/runtime/wine/lib/wine/x86_64-unix/wine-preloader'] * 2,
            'maps': '1000-2000 r-xp 0 00:00 1 /runtime/wine/lib/wine/x86_64-windows/comctl32.dll\n'}

    def stat(self):
        return SimpleNamespace(st_uid=self.uid)

    def __truediv__(self, name):
        def read():
            value = self.values[name]
            return value.pop(0) if isinstance(value, list) and len(value) > 1 else value[0] if isinstance(value, list) else value
        return SimpleNamespace(read_text=read, read_bytes=read, readlink=read)


class TooltipObservationTests(unittest.TestCase):
    def observe(self, proc):
        with mock.patch.object(runner.os, 'getuid', return_value=1000):
            return runner.fixture_observation(proc, '/test/results/prefix', 'fixture-token', '/probe/tooltip-track-coordinates.exe')

    def test_exact_uid_prefix_token_birth_role_and_windows_path_are_accepted(self):
        identity, modules = self.observe(FakeProcess())
        self.assertEqual(identity['pid'], 123)
        self.assertEqual(identity['start_ticks'], 456)
        self.assertEqual(identity['uid'], 1000)
        self.assertEqual(modules, {'/runtime/wine/lib/wine/x86_64-windows/comctl32.dll'})

    def test_reused_birth_role_exec_and_credential_loss_are_refused(self):
        for change in ('birth', 'exec', 'token', 'prefix', 'uid', 'command'):
            proc = FakeProcess()
            if change == 'birth':
                proc.values['stat'][1] = proc.values['stat'][1].rsplit(' ', 1)[0] + ' 999'
            elif change == 'exec':
                proc.values['exe'][1] = '/usr/bin/Xwayland'
            elif change == 'token':
                proc.values['environ'][1] = b'WINEPREFIX=/test/results/prefix\0QUANTOWER_LAUNCH_SESSION=changed\0'
            elif change == 'prefix':
                proc.values['environ'][0] = b'WINEPREFIX=/another/prefix\0QUANTOWER_LAUNCH_SESSION=fixture-token\0'
            elif change == 'uid':
                proc.uid = 1001
            else:
                proc.values['cmdline'] = b'/unrelated/tooltip-track-coordinates.exe\0'
            with self.subTest(change=change):
                self.assertIsNone(self.observe(proc))


if __name__ == '__main__':
    unittest.main()
