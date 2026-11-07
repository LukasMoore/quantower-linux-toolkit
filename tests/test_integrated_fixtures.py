"""Offline safety guards for original fixtures; never start a display, Wine or container."""
import contextlib
import importlib.util
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

FIXTURES = Path(__file__).resolve().parent / 'native/integrated-windows'


def load_script(name):
    spec = importlib.util.spec_from_file_location('integrated_fixture_' + name.replace('-', '_'), FIXTURES / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FixtureExecutionGuards(unittest.TestCase):
    def setUp(self):
        self.driver = load_script('run-container')

    def route_paths(self, *, container=True, interfaces=('lo',), results=None):
        def path(value):
            if value == '/.dockerenv':
                return types.SimpleNamespace(is_file=lambda: container)
            if value == '/sys/class/net':
                return types.SimpleNamespace(iterdir=lambda: [types.SimpleNamespace(name=name) for name in interfaces])
            if value == '/test/results' and results is not None:
                return results
            raise AssertionError('Refusal must precede other filesystem access: ' + str(value))
        return path

    def test_container_network_root_and_optin_refusals_precede_all_processes(self):
        cases = [dict(container=False), dict(uid=0), dict(optin=None), dict(optin='0'),
                 dict(optin=''), dict(interfaces=('lo', 'eth0')), dict(interfaces=())]
        for case in cases:
            with self.subTest(case=case), contextlib.ExitStack() as stack:
                stack.enter_context(mock.patch.object(self.driver, 'Path', side_effect=self.route_paths(
                    container=case.get('container', True), interfaces=case.get('interfaces', ('lo',)))))
                stack.enter_context(mock.patch.object(self.driver.os, 'getuid', return_value=case.get('uid', 1000)))
                stack.enter_context(mock.patch.object(self.driver.os, 'getenv', return_value=case.get('optin', '1')))
                spawn = stack.enter_context(mock.patch.object(self.driver.subprocess, 'Popen'))
                run = stack.enter_context(mock.patch.object(self.driver.subprocess, 'run'))
                pipe = stack.enter_context(mock.patch.object(self.driver.os, 'pipe'))
                umask = stack.enter_context(mock.patch.object(self.driver.os, 'umask'))
                with self.assertRaisesRegex(RuntimeError, 'non-root networkless data-only container'):
                    self.driver.main()
                spawn.assert_not_called()
                run.assert_not_called()
                pipe.assert_not_called()
                umask.assert_not_called()

    def test_existing_results_or_dangling_symlink_refuse_before_display_spawn(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            results = base / 'results'
            results.mkdir()
            marker = results / 'retained.txt'
            marker.write_text('Existing evidence must survive.\n')
            dangling = base / 'dangling'
            dangling.symlink_to(base / 'missing')
            for target in (results, dangling):
                with self.subTest(target=target.name), contextlib.ExitStack() as stack:
                    stack.enter_context(mock.patch.object(self.driver, 'Path', side_effect=self.route_paths(results=target)))
                    stack.enter_context(mock.patch.object(self.driver.os, 'getuid', return_value=1000))
                    stack.enter_context(mock.patch.object(self.driver.os, 'getenv', return_value='1'))
                    spawn = stack.enter_context(mock.patch.object(self.driver.subprocess, 'Popen'))
                    run = stack.enter_context(mock.patch.object(self.driver.subprocess, 'run'))
                    with self.assertRaisesRegex(FileExistsError, 'never reuse a prefix'):
                        self.driver.main()
                    spawn.assert_not_called()
                    run.assert_not_called()
            self.assertEqual(marker.read_text(), 'Existing evidence must survive.\n')
            self.assertTrue(dangling.is_symlink())


class FixtureBuildGuards(unittest.TestCase):
    def test_native_existing_directory_and_symlink_never_invoke_compiler(self):
        builder = load_script('build')
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            existing = base / 'existing'
            existing.mkdir()
            marker = existing / 'window-probe.exe'
            marker.write_bytes(b'original fixture bytes')
            dangling = base / 'dangling'
            dangling.symlink_to(base / 'missing')
            for target in (existing, dangling):
                with self.subTest(target=target.name), \
                        mock.patch.object(sys, 'argv', ['build.py', '--output-dir', str(target)]), \
                        mock.patch.object(builder.shutil, 'which', return_value='/trusted/fixture-compiler'), \
                        mock.patch.object(builder.subprocess, 'run') as run:
                    with self.assertRaisesRegex(FileExistsError, 'new output directory'):
                        builder.main()
                    run.assert_not_called()
            self.assertEqual(marker.read_bytes(), b'original fixture bytes')
            self.assertTrue(dangling.is_symlink())

    def test_native_missing_compiler_does_not_create_output(self):
        builder = load_script('build')
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'new-build'
            with mock.patch.object(sys, 'argv', ['build.py', '--output-dir', str(target)]), \
                    mock.patch.object(builder.shutil, 'which', return_value=None), \
                    mock.patch.object(builder.subprocess, 'run') as run:
                with self.assertRaisesRegex(ValueError, 'MinGW compiler'):
                    builder.main()
                run.assert_not_called()
            self.assertFalse(target.exists())

    def test_wpf_build_never_overwrites_or_builds_inside_prefix(self):
        builder = load_script('wpf-cef-build')
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            existing = base / 'existing'
            existing.mkdir()
            marker = existing / 'WpfCefFixture.dll'
            marker.write_bytes(b'original managed fixture bytes')
            prefix = base / 'prefix'
            prefix.mkdir()
            (prefix / 'drive_c').mkdir()
            (prefix / 'user.reg').write_text('Synthetic prefix marker\n')
            target_in_prefix = prefix / 'drive_c/new-build'
            arguments = ['--dotnet', '/compiler/dotnet', '--compiler', '/compiler/csc.dll',
                         '--framework-root', '/official/frameworks', '--cefsharp-home', '/official/cefsharp']
            for target, message in ((existing, 'must be new'), (target_in_prefix, 'outside Wine prefixes')):
                with self.subTest(target=target.name), mock.patch.object(builder.subprocess, 'run') as run:
                    with self.assertRaisesRegex(ValueError, message):
                        builder.main([*arguments, '--output', str(target)])
                    run.assert_not_called()
            self.assertEqual(marker.read_bytes(), b'original managed fixture bytes')
            self.assertFalse(target_in_prefix.exists())
            self.assertEqual((prefix / 'user.reg').read_text(), 'Synthetic prefix marker\n')


if __name__ == '__main__':
    unittest.main()
