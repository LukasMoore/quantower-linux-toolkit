"""Installation tests use fake runtimes and never start Wine or the application."""
import fcntl
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import shutil
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
        guide = self.target / 'docs/INTEGRATED-WINDOWS.md'
        self.assertEqual(guide.read_bytes(), (ROOT / 'docs/INTEGRATED-WINDOWS.md').read_bytes())
        inventory = json.loads((self.target / 'runtime-components/install-inventory.json').read_text())
        self.assertEqual(inventory['docs/INTEGRATED-WINDOWS.md'], hashlib.sha256(guide.read_bytes()).hexdigest())
        config = json.loads((self.target / 'launcher-config.json').read_text())
        self.assertEqual(config['window_mode'], 'nested')
        self.assertIsNone(config['host_display'])
        self.assertIsNone(config['wine_dpi'])
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

    def fixture_writable(self, root):
        if root.exists():
            root.chmod(0o700)
            for path in root.rglob('*'):
                if not path.is_symlink():
                    path.chmod(0o700 if path.is_dir() else 0o600)

    def readonly_fixture(self, root):
        for path in root.rglob('*'):
            if not path.is_symlink():
                path.chmod(0o555 if path.is_dir() or path.stat().st_mode & 0o111 else 0o444)
        root.chmod(0o555)
        self.addCleanup(self.fixture_writable, root)

    def fixture_snapshot(self, root):
        result = {}
        for path in (root, *root.rglob('*')):
            name = path.relative_to(root).as_posix()
            if path.is_symlink():
                result[name] = ('link', os.readlink(path))
            else:
                result[name] = (stat.S_IMODE(path.stat().st_mode), path.read_bytes() if path.is_file() else None)
        return result

    def test_readonly_runtime_sources_stage_writable_private_copies_without_source_changes(self):
        self.target = self.root / 'installed gpu target with spaces'
        library = self.wine / 'lib/fixture.so'
        library.parent.mkdir()
        library.write_bytes(b'\x7fELF synthetic runtime')
        (self.wine / 'lib/absolute.so').symlink_to(library)
        game = self.root / 'gamescope-source'
        (game / 'bin').mkdir(parents=True)
        for name in ('gamescope', 'gamescope-brokey', 'gamescopereaper', 'Xwayland'):
            path = game / 'bin' / name
            path.write_bytes(('synthetic ' + name).encode())
            path.chmod(0o700)
        x11 = self.root / 'x11-source'
        (x11 / 'lib').mkdir(parents=True)
        for name in ('libX11.so.6', 'libX11-xcb.so.1', 'libfixture.so.1'):
            (x11 / 'lib' / name).write_bytes(b'\x7fELF synthetic ' + name.encode())
        locale = x11 / 'share/X11/locale/fixture/Compose'
        locale.parent.mkdir(parents=True)
        locale.write_bytes(b'synthetic locale data')
        dxvk = self.root / 'd3d9.dll'
        dxvk.write_bytes(b'synthetic DXVK')
        dxvk.chmod(0o444)
        self.addCleanup(dxvk.chmod, 0o600)
        roots = (self.wine, game, x11)
        for root in roots:
            self.readonly_fixture(root)
        # Privileged bits from a user-supplied source must not reach its copy.
        (self.wine / 'bin/wine').chmod(0o4555)
        before = {root: self.fixture_snapshot(root) for root in roots}
        with mock.patch.object(installer.subprocess, 'Popen', side_effect=AssertionError('No runtime may execute')):
            installer.assemble(self.options('--profile', 'gpu', '--gamescope-home', str(game),
                                            '--x11-home', str(x11), '--dxvk-d3d9', str(dxvk)))
        self.assertEqual({root: self.fixture_snapshot(root) for root in roots}, before)
        self.assertEqual(installer.profile_manager.load(self.target), 'gpu')
        copied = self.target / 'runner-quantower/opt/wine-devel'
        self.assertEqual(stat.S_IMODE((copied / 'bin/wine').stat().st_mode), 0o755)
        self.assertFalse(Path(os.readlink(copied / 'lib/absolute.so')).is_absolute())
        self.assertEqual((copied / 'lib/absolute.so').read_bytes(), library.read_bytes())
        self.assertEqual((self.target / 'gamescope/files/bin/gamescope-brokey').read_bytes(),
                         (game / 'bin/gamescope').read_bytes())
        launch = installer.runtime_config.build_render_launch(
            self.target, installer.runtime_config.RuntimeConfig(), {'PATH': '/fixture-missing-system-tools'})
        self.assertEqual(shutil.which('gamescopereaper', path=launch.environment['PATH']),
                         str(self.target / 'gamescope/files/bin/gamescopereaper'))
        self.assertEqual((self.target / 'runtime-components/x11/libX11.so.6').read_bytes(),
                         (x11 / 'lib/libX11.so.6').read_bytes())
        self.assertTrue((self.target / 'runtime-components/x11/share/X11/locale/fixture/Compose').is_file())
        for path in self.target.rglob('*'):
            if not path.is_symlink():
                mode = stat.S_IMODE(path.stat().st_mode)
                self.assertFalse(mode & 0o7000, str(path))
                self.assertTrue(mode & 0o200, str(path))
        self.assertFalse((self.target / 'prefix/user.reg').exists())

    def test_copy_tree_refuses_existing_destination_without_changing_its_permissions(self):
        self.target.mkdir()
        sentinel = self.target / 'keep'
        sentinel.write_bytes(b'keep existing data')
        sentinel.chmod(0o400)
        self.target.chmod(0o500)
        self.addCleanup(self.fixture_writable, self.target)
        before = self.fixture_snapshot(self.target)
        with self.assertRaises(FileExistsError):
            installer.copy_tree(self.wine, self.target)
        self.assertEqual(self.fixture_snapshot(self.target), before)

    def test_partial_readonly_copy_is_removed_without_masking_the_copy_error(self):
        child = self.wine / 'a-directory/file'
        child.parent.mkdir()
        child.write_bytes(b'copied before failure')
        (self.wine / 'z-failure').write_bytes(b'trigger failure')
        self.readonly_fixture(self.wine)
        before = self.fixture_snapshot(self.wine)
        original = installer.copy_owned_file
        def fail(source, destination):
            if Path(source).name == 'z-failure':
                raise OSError('simulated copy failure')
            return original(source, destination)
        with mock.patch.object(installer, 'copy_owned_file', side_effect=fail):
            with self.assertRaisesRegex(OSError, 'simulated copy failure'):
                installer.copy_tree(self.wine, self.target)
        self.assertFalse(self.target.exists())
        self.assertEqual(self.fixture_snapshot(self.wine), before)

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

    def test_native_extract_validates_version_and_rejects_extracted_symlinks(self):
        (self.target / 'prefix/drive_c').mkdir(parents=True)
        def extract(command, **kwargs):
            output = Path(command[command.index('--output-dir') + 1])
            app = output / 'app/TradingPlatform/v1.147.5'
            app.mkdir(parents=True)
            (app / 'Starter.exe').write_bytes(b'synthetic executable')
            self.assertEqual(command[1:3], ['--extract', '--silent'])
            return mock.Mock(returncode=0)
        with mock.patch.object(installer.subprocess, 'run', side_effect=extract):
            result = installer.extract_application(self.target, {}, io.BytesIO(), '1.147.5')
        self.assertEqual(result['exit_code'], 0)
        destination = self.target / 'prefix/drive_c/AMPQuantowerInstalled'
        self.assertTrue((destination / 'TradingPlatform/v1.147.5/Starter.exe').is_file())
        with self.assertRaisesRegex(RuntimeError, 'must be new'):
            installer.extract_application(self.target, {}, io.BytesIO(), '1.147.5')
        import shutil
        shutil.rmtree(destination)
        def unsafe(command, **kwargs):
            result = extract(command, **kwargs)
            output = Path(command[command.index('--output-dir') + 1])
            (output / 'app/escape').symlink_to(self.wine)
            return result
        with mock.patch.object(installer.subprocess, 'run', side_effect=unsafe):
            with self.assertRaisesRegex(RuntimeError, 'link or special'):
                installer.extract_application(self.target, {}, io.BytesIO(), '1.147.5')
        self.assertFalse(destination.exists())

    def test_bootstrap_overrides_and_complete_open_font_families_are_scoped(self):
        # No real Wine: this records process arguments/environment and imitates
        # only prefix initialization, leaving ownership to isolated mocks.
        code = '#!/usr/bin/env python3\n' + textwrap.dedent('''
            import json, os, pathlib, sys
            prefix = pathlib.Path(os.environ['WINEPREFIX'])
            with (prefix.parent / 'setup-commands.jsonl').open('a') as output:
                output.write(json.dumps({'argv': sys.argv[1:], 'overrides': os.environ.get('WINEDLLOVERRIDES')}) + '\\n')
            if sys.argv[1] == 'wineboot.exe':
                (prefix / 'user.reg').write_text('synthetic registry')
        ''')
        (self.wine / 'bin/wine').write_text(code)
        fonts = self.wine / 'share/wine/fonts'
        fonts.mkdir(parents=True)
        for name in installer.FONT_FALLBACKS[0][1]:
            (fonts / name).write_bytes(b'synthetic font')
        (fonts / 'DejaVuSansMono.ttf').write_bytes(b'incomplete family')
        with self.isolated_process_discovery():
            installer.assemble(self.options('--initialize-prefix'))
        commands = [json.loads(line) for line in (self.target / 'setup-commands.jsonl').read_text().splitlines()]
        self.assertIn('mscoree,mshtml=', commands[0]['overrides'])
        self.assertTrue(all(command['overrides'] == 'winemenubuilder.exe=d' for command in commands[1:]))
        alias_commands = [command['argv'] for command in commands if 'FontSubstitutes' in ' '.join(command['argv'])]
        self.assertEqual({args[args.index('/v') + 1] for args in alias_commands}, set(installer.FONT_FALLBACKS[0][2]))
        dwrite_commands = [command['argv'] for command in commands if r'Wine\Fonts\Replacements' in ' '.join(command['argv'])]
        self.assertEqual({args[args.index('/v') + 1] for args in dwrite_commands}, set(installer.FONT_FALLBACKS[0][2]))
        (self.target / 'runner-quantower/opt/wine-devel/share/wine/fonts/DejaVuSans.ttf').write_bytes(b'changed')
        with self.assertRaisesRegex(RuntimeError, 'failed verification'):
            installer.profile_manager.load(self.target)


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
