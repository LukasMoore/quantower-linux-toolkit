"""Installation tests use fake runtimes and never start Wine or the application."""
import fcntl
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import textwrap
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def module(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'tools' / file)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


installer = module('toolkit_installer', 'install.py')
fetcher = module('installer_fetcher', 'fetch-installers.py')


class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.wine = self.root / 'wine-source'
        (self.wine / 'bin').mkdir(parents=True)
        for name in ('wine', 'wineserver'):
            path = self.wine / 'bin' / name
            path.write_text('#!/bin/sh\nexit 99\n')
            path.chmod(0o700)
        self.target = self.root / 'installed'

    def options(self, *extra):
        return installer.parser().parse_args(['--target', str(self.target), '--wine-home', str(self.wine), *extra])

    @contextlib.contextmanager
    def isolated_process_discovery(self):
        # These fixtures exercise setup commands and filesystem effects. Process
        # identity/permission behavior belongs to the mocked cleanup regression
        # suite, not to whichever processes happen to be on a CI host.
        def observe(session):
            session.identities.clear()
            session.save()
            return set()
        with (
            mock.patch.object(installer.supervisor, 'prefix_processes', return_value=set()),
            mock.patch.object(installer.profile_manager, 'no_running_prefix'),
            mock.patch.object(installer.supervisor.SessionProcesses, 'remember_child'),
            mock.patch.object(installer.supervisor.SessionProcesses, 'observe', observe),
            mock.patch.object(installer.supervisor.SessionProcesses, 'signal_identity',
                              side_effect=AssertionError('Setup fixture must not signal host processes')),
        ):
            yield

    def test_clean_install_is_relocatable_pinned_and_does_not_execute(self):
        with mock.patch.object(installer.subprocess, 'Popen', side_effect=AssertionError('No process may run')):
            result = installer.assemble(self.options())
        self.assertFalse(result['prefix_initialized'])
        self.assertFalse(result['installers_executed'])
        self.assertEqual(installer.profile_manager.load(self.target), 'compatible')
        for name in installer.SOURCE_FILES:
            self.assertTrue((self.target / name).is_file(), name)
        self.assertTrue((self.target / 'runtime-components/install-inventory.json').is_file())
        self.assertFalse((self.target / 'prefix/user.reg').exists())
        self.assertEqual(self.target.stat().st_mode & 0o777, 0o700)
        # Hash failures block future launches after the copied source changes.
        (self.target / 'runner-quantower/opt/wine-devel/bin/wine').write_text('changed')
        with self.assertRaisesRegex(RuntimeError, 'failed verification'):
            installer.profile_manager.load(self.target)

    def test_plan_performs_no_writes(self):
        result = installer.assemble(self.options('--plan'))
        self.assertFalse(result['writes_performed'])
        self.assertFalse(self.target.exists())
        self.assertFalse(list(self.root.glob('.quantower-install-*')))

    def test_existing_target_is_preserved_and_nested_runtime_targets_are_rejected(self):
        self.target.mkdir()
        sentinel = self.target / 'keep'
        sentinel.write_text('existing installation')
        with self.assertRaisesRegex(ValueError, 'new path'):
            installer.assemble(self.options())
        self.assertEqual(sentinel.read_text(), 'existing installation')
        self.target = self.wine / 'nested'
        with self.assertRaisesRegex(ValueError, 'separate'):
            installer.assemble(self.options())
        self.assertFalse(list(self.wine.glob('.quantower-install-*')))

    def test_external_runtime_symlink_and_missing_gpu_prerequisites_fail_before_writes(self):
        outside = self.root / 'outside'
        outside.write_text('host file')
        (self.wine / 'escape').symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'outside'):
            installer.assemble(self.options())
        (self.wine / 'escape').unlink()
        with self.assertRaisesRegex(ValueError, 'requires both'):
            installer.assemble(self.options('--profile', 'gpu'))
        self.assertFalse(self.target.exists())

    def test_parent_aliases_are_canonical_and_existing_prefixes_are_not_install_targets(self):
        physical = self.root / 'physical'
        physical.mkdir()
        alias = self.root / 'alias'
        alias.symlink_to(physical, target_is_directory=True)
        self.target = alias / 'installation'
        result = installer.assemble(self.options('--plan'))
        self.assertEqual(Path(result['target']), physical / 'installation')
        (physical / 'user.reg').write_text('synthetic registry')
        (physical / 'drive_c').mkdir()
        with self.assertRaisesRegex(ValueError, 'outside existing Wine prefixes'):
            installer.assemble(self.options('--plan'))

    def test_internal_absolute_symlink_is_relocated_inside_new_bundle(self):
        (self.wine / 'tool').write_text('data')
        (self.wine / 'absolute-link').symlink_to(self.wine / 'tool')
        installer.assemble(self.options())
        link = self.target / 'runner-quantower/opt/wine-devel/absolute-link'
        self.assertTrue(link.is_symlink())
        self.assertFalse(Path(os.readlink(link)).is_absolute())
        self.assertTrue(link.resolve().is_relative_to(self.target))
        self.assertEqual(link.read_text(), 'data')

    def test_installer_checksums_are_required_and_only_verified_files_are_staged(self):
        file = self.root / 'vendor.exe'
        file.write_bytes(b'synthetic installer')
        with self.assertRaisesRegex(ValueError, 'requires its expected'):
            installer.assemble(self.options('--app-installer', str(file)))
        self.assertFalse(self.target.exists())
        digest = hashlib.sha256(file.read_bytes()).hexdigest()
        installer.assemble(self.options('--app-installer', str(file), '--app-installer-sha256', digest))
        self.assertEqual((self.target / 'installers/Quantower.exe').read_bytes(), file.read_bytes())
        self.assertEqual((self.target / 'installers/Quantower.exe').stat().st_mode & 0o777, 0o600)

    def test_setup_lock_is_held_before_published_install_can_be_launched(self):
        def initialize(target, run, *, setup_lock):
            self.assertIsNotNone(setup_lock)
            with (target / 'logs/launch.lock').open('a') as competing:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(competing, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return {'prefix_initialized': True}
        with mock.patch.object(installer, 'initialize', side_effect=initialize):
            result = installer.assemble(self.options('--initialize-prefix'))
        self.assertTrue(result['prefix_initialized'])
        with (self.target / 'logs/launch.lock').open('a') as after:
            fcntl.flock(after, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_initialization_refuses_an_existing_lock_or_active_starter(self):
        installer.assemble(self.options())
        with (self.target / 'logs/launch.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):
                installer.initialize(self.target)
        with mock.patch.object(installer.supervisor, 'prefix_processes', return_value={123}):
            with self.assertRaisesRegex(RuntimeError, 'active or unverifiable'):
                installer.initialize(self.target)
        self.assertFalse((self.target / 'logs/session-processes.json').exists())

    def test_invalid_complete_x11_pair_is_rejected_even_by_plan(self):
        directory = self.root / 'x11'
        directory.mkdir()
        (directory / 'libX11.so.6').write_bytes(b'one only')
        with self.assertRaisesRegex(ValueError, 'missing libX11-xcb'):
            installer.assemble(self.options('--x11-home', str(directory), '--plan'))
        self.assertFalse(self.target.exists())

    def test_fake_wine_exercises_prefix_and_gui_setup_without_real_wine(self):
        # A synthetic local executable imitates only the filesystem effects of
        # setup. It proves command/exit handling and ledger persistence;
        # identity discovery is isolated and tested separately.
        code = '#!/usr/bin/env python3\n' + textwrap.dedent('''
            import os, pathlib, sys
            prefix = pathlib.Path(os.environ['WINEPREFIX'])
            if sys.argv[1] == 'wineboot.exe':
                (prefix / 'user.reg').write_text('WINE REGISTRY Version 2\\n')
            elif sys.argv[1].endswith('WindowsDesktopRuntime.exe'):
                root = prefix / 'drive_c/Program Files/dotnet'
                for name in ['shared/Microsoft.WindowsDesktop.App/10.0.0/PresentationFramework.dll',
                             'shared/Microsoft.NETCore.App/10.0.0/System.Private.CoreLib.dll', 'dotnet.exe']:
                    file = root / name
                    file.parent.mkdir(parents=True, exist_ok=True)
                    file.write_bytes(b'synthetic')
                sys.exit(194)
            elif sys.argv[1].endswith('Quantower.exe'):
                root = prefix / 'drive_c/AMPQuantowerInstalled/TradingPlatform/v1.147.5'
                root.mkdir(parents=True, exist_ok=True)
                (root / 'Starter.exe').write_bytes(b'synthetic')
        ''')
        (self.wine / 'bin/wine').write_text(code)
        file = self.root / 'synthetic-installer.exe'
        file.write_bytes(b'synthetic setup')
        pin = hashlib.sha256(file.read_bytes()).hexdigest()
        with self.isolated_process_discovery():
            result = installer.assemble(self.options('--app-installer', str(file), '--app-installer-sha256', pin,
                                                    '--desktop-runtime-installer', str(file), '--desktop-runtime-sha256', pin,
                                                    '--initialize-prefix', '--run-installers'))
        self.assertTrue(result['prefix_initialized'])
        self.assertTrue(result['setup_cleanup_complete'])
        self.assertEqual([x['exit_code'] for x in result['setup_steps']], [0, 0, 194, 0])
        self.assertEqual(json.loads((self.target / 'logs/session-processes.json').read_text())['processes'], [])

    def test_failed_setup_keeps_its_target_and_private_diagnostic_log(self):
        with self.isolated_process_discovery(), self.assertRaisesRegex(RuntimeError, 'Setup step failed'):
            installer.assemble(self.options('--initialize-prefix'))
        self.assertTrue(self.target.is_dir())
        self.assertTrue((self.target / 'logs/setup.raw.log').is_file())
        self.assertTrue((self.target / 'runtime-components/manifest.json').is_file())


class FetchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cache = Path(self.temp.name) / 'cache'
        self.data = b'synthetic pinned download'
        self.item = {'filename': 'example.exe', 'url': 'https://updates.quantower.com/example.exe',
                     'algorithm': 'sha256', 'digest': hashlib.sha256(self.data).hexdigest()}

    def opener(self, request, timeout):
        response = io.BytesIO(self.data)
        response.geturl = lambda: self.item['url']
        return response

    def test_download_is_verified_private_cached_and_never_executed(self):
        path = fetcher.fetch(self.item, self.cache, opener=self.opener)
        self.assertEqual(path.read_bytes(), self.data)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.cache.stat().st_mode & 0o777, 0o700)
        with mock.patch.object(fetcher.urllib.request, 'urlopen', side_effect=AssertionError('Cache must be used')):
            self.assertEqual(fetcher.fetch(self.item, self.cache), path)

    def test_bad_download_and_bad_existing_cache_are_preserved_or_removed_safely(self):
        self.item['digest'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            fetcher.fetch(self.item, self.cache, opener=self.opener)
        self.assertFalse(list(self.cache.iterdir()))
        cached = self.cache / 'example.exe'
        cached.write_bytes(b'keep this evidence')
        with self.assertRaisesRegex(ValueError, 'was not changed'):
            fetcher.fetch(self.item, self.cache, opener=self.opener)
        self.assertEqual(cached.read_bytes(), b'keep this evidence')

    def test_redirect_host_path_escape_and_symlink_cache_are_rejected(self):
        def redirect(request, timeout):
            response = io.BytesIO(self.data)
            response.geturl = lambda: 'http://updates.quantower.com/example.exe'
            return response
        with self.assertRaisesRegex(ValueError, 'HTTPS'):
            fetcher.fetch(self.item, self.cache, opener=redirect)
        self.item['filename'] = '../outside.exe'
        with self.assertRaisesRegex(ValueError, 'basename'):
            fetcher.fetch(self.item, self.cache, opener=self.opener)
        alias = self.cache.parent / 'alias'
        alias.symlink_to(self.cache, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            fetcher.fetch(self.item, alias, opener=self.opener)

    def test_intermediate_redirect_downgrade_is_refused(self):
        handler = fetcher.OfficialRedirectHandler()
        request = fetcher.urllib.request.Request(self.item['url'])
        with self.assertRaisesRegex(ValueError, 'HTTPS'):
            handler.redirect_request(request, None, 302, 'Moved', {}, 'http://updates.quantower.com/example.exe')


if __name__ == '__main__':
    unittest.main()
