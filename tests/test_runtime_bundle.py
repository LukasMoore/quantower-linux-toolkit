"""Bundle tests use synthetic runtimes; no Wine, installer or GPU is executed."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import runtime_bundle as bundle


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'tools' / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


packager = load('runtime_packager_test', 'package-runtime.py')
setup = load('runtime_setup_test', 'setup.py')
notices = load('runtime_notices_test', 'bundle-notices.py')
installer = load('runtime_installer_test', 'install.py')


class RuntimeBundleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.wine = self.root / 'wine'
        self.game = self.root / 'gamescope'
        self.x11 = self.root / 'x11'
        self.staging = self.root / 'staging'
        self.output = self.root / 'runtime.tar.gz'
        for path in (self.wine / 'bin/wine', self.wine / 'bin/wineserver', self.wine / 'bin/innoextract',
                     self.game / 'bin/gamescope', self.game / 'bin/Xwayland'):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('#!/bin/sh\nexit 99\n')
            path.chmod(0o755)
        self.x11.mkdir()
        for name in ('libX11.so.6', 'libX11-xcb.so.1'):
            (self.x11 / name).write_bytes(b'synthetic Xlib')
        self.dxvk = self.root / 'synthetic-d3d9.dll'
        self.dxvk.write_bytes(b'synthetic DXVK')

    def stage_args(self):
        return packager.parser().parse_args([
            'stage', '--staging', str(self.staging), '--wine-home', str(self.wine),
            '--gamescope-home', str(self.game), '--x11-home', str(self.x11),
            '--dxvk-d3d9', str(self.dxvk), '--version', '0.2.0'])

    def stage(self):
        return packager.stage(self.stage_args())

    def add_notices(self):
        source = self.root / 'sources.tar.gz'
        artifacts = {'source.c': b'int synthetic_source;\n', 'build.sh': b'#!/bin/sh\nexit 0\n'}
        with tarfile.open(source, 'w:gz') as archive:
            top = tarfile.TarInfo('sources')
            top.type = tarfile.DIRTYPE
            archive.addfile(top)
            for name, data in artifacts.items():
                member = tarfile.TarInfo('sources/' + name)
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
        notice_root = self.root / 'notice-inputs'
        notice_root.mkdir()
        (notice_root / 'LICENSE').write_text('Synthetic permissive fixture notice.\n')
        inventory = {'schema_version': 1, 'source_archive_root': 'sources', 'components': [
            {'id': 'fixture', 'name': 'Synthetic runtime fixture', 'version': '1', 'license': 'MIT',
             'covers': ['payload/'],
             'notices': [{'path': 'LICENSE', 'sha256': bundle.sha256(notice_root / 'LICENSE')}],
             'sources': [{'path': 'source.c', 'sha256': hashlib.sha256(artifacts['source.c']).hexdigest()}],
             'recipes': [{'path': 'build.sh', 'sha256': hashlib.sha256(artifacts['build.sh']).hexdigest()}]}
        ]}
        notices.collect(self.staging, inventory, notice_root, source, self.staging / 'licenses',
                        source_url='https://example.invalid/sources.tar.gz')
        return source

    def seal(self):
        self.stage()
        source = self.add_notices()
        args = packager.parser().parse_args(['seal', '--staging', str(self.staging),
                                            '--source-archive', str(source), '--output', str(self.output)])
        result = packager.seal(args)
        self.assertEqual(result['mode'], 'sealed')
        return result

    def setup_args(self, *extra):
        return setup.parser().parse_args(['--bundle-root', str(self.staging),
                                          '--target', str(self.root / 'installed'),
                                          '--profile', 'compatible', *extra])

    def test_stage_is_new_and_contains_no_application_or_prefix(self):
        result = self.stage()
        self.assertEqual(result['mode'], 'staged')
        self.assertTrue((self.staging / 'tools/setup.py').is_file())
        self.assertTrue((self.staging / 'payload/wine/bin/wine').is_file())
        self.assertFalse((self.staging / 'prefix').exists())
        self.assertFalse((self.staging / 'installers').exists())
        subprocess.run(['bash', '-n', str(self.staging / 'setup.sh')], check=True)
        with self.assertRaises(FileExistsError):
            self.stage()

    def test_component_input_escape_is_refused_and_partial_stage_is_removed(self):
        outside = self.root / 'outside'
        outside.write_text('outside state')
        (self.wine / 'escape').symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'escapes'):
            self.stage()
        self.assertFalse(self.staging.exists())
        self.assertEqual(outside.read_text(), 'outside state')

    def test_private_prefix_and_missing_xwayland_are_refused(self):
        (self.wine / 'user.reg').write_text('synthetic user state')
        with self.assertRaisesRegex(ValueError, 'Private/application'):
            self.stage()
        (self.wine / 'user.reg').unlink()
        (self.game / 'bin/Xwayland').unlink()
        with self.assertRaisesRegex(ValueError, 'Xwayland'):
            self.stage()
        self.assertFalse(self.staging.exists())

    def test_absolute_in_tree_library_link_becomes_relocatable(self):
        library = self.wine / 'lib'
        library.mkdir()
        (library / 'libfixture.so.1').write_bytes(b'synthetic library')
        (library / 'libfixture.so').symlink_to(library / 'libfixture.so.1')
        self.seal()
        target = self.root / 'relocated with spaces'
        bundle.extract_bundle(self.output, target, expected_sha256=bundle.sha256(self.output))
        link = target / 'payload/wine/lib/libfixture.so'
        self.assertTrue(link.is_symlink())
        self.assertFalse(Path(os.readlink(link)).is_absolute())
        self.assertTrue(link.resolve().is_relative_to(target))
        self.assertEqual(link.read_bytes(), b'synthetic library')

    def test_full_x11_prefix_retains_libraries_and_locale_data(self):
        (self.x11 / 'lib').mkdir()
        for name in ('libX11.so.6', 'libX11-xcb.so.1'):
            (self.x11 / name).rename(self.x11 / 'lib' / name)
        locale = self.x11 / 'share/X11/locale/fixture/Compose'
        locale.parent.mkdir(parents=True)
        locale.write_text('synthetic locale data\n')
        self.stage()
        self.assertTrue((self.staging / 'payload/x11/lib/libX11.so.6').is_file())
        self.assertEqual((self.staging / 'payload/x11/share/X11/locale/fixture/Compose').read_text(), 'synthetic locale data\n')

    def test_archive_is_deterministic_and_extracted_payload_is_verified(self):
        self.seal()
        second = self.root / 'second.tar.gz'
        bundle.archive_bundle(self.staging, second)
        self.assertEqual(bundle.sha256(self.output), bundle.sha256(second))
        destination = self.root / 'extracted'
        manifest = bundle.extract_bundle(self.output, destination, expected_sha256=bundle.sha256(self.output))
        self.assertEqual(bundle.verify_bundle(destination), manifest)
        self.assertEqual(manifest['platform'], 'debian13-x86_64')
        with self.assertRaises(FileExistsError):
            bundle.extract_bundle(self.output, destination)

    def test_tamper_and_extra_files_fail_before_setup(self):
        self.seal()
        target = self.staging / 'payload/dxvk/d3d9.dll'
        original = target.read_bytes()
        target.write_bytes(b'changed runtime')
        with self.assertRaisesRegex(ValueError, 'integrity mismatch'):
            setup.execute(self.setup_args())
        target.write_bytes(original)
        (self.staging / 'unlisted').write_text('unexpected input')
        with self.assertRaisesRegex(ValueError, 'integrity mismatch'):
            bundle.verify_bundle(self.staging)
        self.assertFalse((self.root / 'installed').exists())

    def test_restrictive_extraction_permissions_are_safe_but_extra_bits_are_refused(self):
        self.seal()
        destination = self.root / 'restrictive-umask'
        destination.mkdir()
        subprocess.run(['tar', '-xzf', str(self.output), '-C', str(destination)],
                       umask=0o077, capture_output=True, check=True)
        extracted = destination / bundle.read_json(self.staging / bundle.MANIFEST)['archive_root']
        bundle.verify_bundle(extracted)
        self.assertEqual(stat.S_IMODE((extracted / 'payload/dxvk/d3d9.dll').stat().st_mode), 0o600)
        executable = self.staging / 'payload/wine/bin/wine'
        library = self.staging / 'payload/dxvk/d3d9.dll'
        executable.chmod(0o700)
        library.chmod(0o600)
        bundle.verify_bundle(self.staging)
        executable.chmod(0o555)
        library.chmod(0o444)
        bundle.verify_bundle(self.staging)
        executable.chmod(0o777)
        with self.assertRaisesRegex(ValueError, 'integrity mismatch'):
            bundle.verify_bundle(self.staging)

    def test_archive_checksum_sidecar_is_not_overwritten(self):
        self.seal()
        other = self.root / 'other.tar.gz'
        checksum = other.with_name(other.name + '.sha256')
        checksum.write_text('existing user file')
        with self.assertRaises(FileExistsError):
            bundle.archive_bundle(self.staging, other)
        self.assertFalse(other.exists())
        self.assertEqual(checksum.read_text(), 'existing user file')

    def test_extraction_uses_the_same_verified_file_when_archive_path_is_replaced(self):
        self.seal()
        pinned = bundle.sha256(self.output)
        original_bytes = (self.staging / 'payload/dxvk/d3d9.dll').read_bytes()
        replacement = self.root / 'replacement.tar.gz'
        # A different, internally valid bundle should not replace pinned bytes
        # between the verification and extraction phases.
        file = self.staging / 'payload/dxvk/d3d9.dll'
        file.write_bytes(b'other internally valid content')
        path = self.staging / bundle.MANIFEST
        data = json.loads(path.read_text())
        data['files']['payload/dxvk/d3d9.dll'].update(size=file.stat().st_size, sha256=bundle.sha256(file))
        path.write_text(json.dumps(data))
        bundle.archive_bundle(self.staging, replacement)
        original_verify = bundle._verify_open_archive
        def verified_then_replace(archive):
            result = original_verify(archive)
            os.replace(replacement, self.output)
            return result
        destination = self.root / 'extracted-pinned'
        with mock.patch.object(bundle, '_verify_open_archive', side_effect=verified_then_replace):
            bundle.extract_bundle(self.output, destination, expected_sha256=pinned)
        self.assertEqual((destination / 'payload/dxvk/d3d9.dll').read_bytes(), original_bytes)

    def test_wrong_archive_digest_and_path_traversal_write_nothing(self):
        self.seal()
        destination = self.root / 'rejected'
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            bundle.extract_bundle(self.output, destination, expected_sha256='0' * 64)
        self.assertFalse(destination.exists())
        bad = self.root / 'bad.tar.gz'
        with tarfile.open(bad, 'w:gz') as archive:
            root = tarfile.TarInfo('runtime')
            root.type, root.mode = tarfile.DIRTYPE, 0o755
            archive.addfile(root)
            item = tarfile.TarInfo('runtime/../escape')
            item.size = 1
            archive.addfile(item, io.BytesIO(b'x'))
        with self.assertRaises(ValueError):
            bundle.extract_bundle(bad, destination)
        self.assertFalse(destination.exists())
        self.assertFalse((self.root / 'escape').exists())

    def test_archive_links_cannot_escape(self):
        bad = self.root / 'bad-link.tar.gz'
        with tarfile.open(bad, 'w:gz') as archive:
            root = tarfile.TarInfo('runtime')
            root.type, root.mode = tarfile.DIRTYPE, 0o755
            archive.addfile(root)
            item = tarfile.TarInfo('runtime/redirect')
            item.type, item.linkname = tarfile.SYMTYPE, '../../outside'
            archive.addfile(item)
        with self.assertRaisesRegex(ValueError, 'Unsafe'):
            bundle.extract_bundle(bad, self.root / 'rejected')
        self.assertFalse((self.root / 'rejected').exists())

    def test_seal_requires_complete_license_and_source_coverage(self):
        self.stage()
        source = self.add_notices()
        manifest = self.staging / 'licenses/manifest.json'
        data = json.loads(manifest.read_text())
        data['payload_owners'].pop(next(iter(data['payload_owners'])))
        manifest.write_text(json.dumps(data))
        args = packager.parser().parse_args(['seal', '--staging', str(self.staging),
                                            '--source-archive', str(source), '--output', str(self.output)])
        with self.assertRaisesRegex(ValueError, 'Every runtime payload'):
            packager.seal(args)
        self.assertFalse(self.output.exists())
        self.assertFalse((self.staging / bundle.MANIFEST).exists())

    def test_wrong_corresponding_source_cannot_be_sealed(self):
        self.stage()
        source = self.add_notices()
        source.write_bytes(b'changed source archive')
        args = packager.parser().parse_args(['seal', '--staging', str(self.staging),
                                            '--source-archive', str(source), '--output', str(self.output)])
        with self.assertRaisesRegex(ValueError, 'does not match'):
            packager.seal(args)
        self.assertFalse(self.output.exists())

    def test_published_bundle_requires_matching_source_download_url(self):
        self.stage()
        source = self.add_notices()
        offer = self.staging / 'source-offer.json'
        data = json.loads(offer.read_text())
        data['url'] = None
        offer.write_text(json.dumps(data))
        args = packager.parser().parse_args(['seal', '--staging', str(self.staging),
                                            '--source-archive', str(source), '--output', str(self.output)])
        with self.assertRaisesRegex(ValueError, 'public HTTPS URL'):
            packager.seal(args)
        self.assertFalse(self.output.exists())

    def test_setup_plan_has_no_writes_or_installer_execution(self):
        self.seal()
        before = bundle.inventory(self.staging)
        with mock.patch.object(installer.subprocess, 'Popen', side_effect=AssertionError('No subprocess may run')):
            result = setup.execute(self.setup_args(), installer=installer, environment={})
        self.assertEqual(result['mode'], 'plan')
        self.assertFalse(result['writes_performed'])
        self.assertFalse((self.root / 'installed').exists())
        self.assertEqual(bundle.inventory(self.staging), before)

    def test_setup_stage_maps_all_bundle_inputs_without_downloads_or_execution(self):
        self.seal()
        with mock.patch.object(installer.subprocess, 'Popen', side_effect=AssertionError('No subprocess may run')):
            result = setup.execute(self.setup_args('--stage'), installer=installer, environment={})
        self.assertEqual(result['mode'], 'staged')
        target = self.root / 'installed'
        self.assertEqual((target / 'runtime-components/d3d9.dll').read_bytes(), self.dxvk.read_bytes())
        self.assertEqual((target / 'runner-quantower/opt/wine-devel/bin/wine').read_bytes(), (self.wine / 'bin/wine').read_bytes())
        self.assertFalse((target / 'prefix/user.reg').exists())
        self.assertFalse((target / 'installers').exists())

    def test_setup_refuses_installer_download_when_host_or_display_is_missing(self):
        self.seal()
        fetcher = mock.Mock()
        checks = [{'name': 'distribution', 'okay': False}]
        with self.assertRaisesRegex(ValueError, 'Host baseline checks failed'):
            setup.execute(self.setup_args('--install'), installer=installer, fetcher=fetcher,
                          environment={}, checks=checks)
        with self.assertRaisesRegex(ValueError, 'DISPLAY'):
            setup.execute(self.setup_args('--install', '--allow-unsupported-host'), installer=installer,
                          fetcher=fetcher, environment={}, checks=checks)
        fetcher.fetch.assert_not_called()
        self.assertFalse((self.root / 'installed').exists())

    def test_setup_install_uses_pinned_inputs_then_repairs_only_after_clean_cleanup(self):
        self.seal()
        calls = []
        fake_installer = mock.Mock()
        fake_installer.parser = installer.parser
        def assemble(args):
            calls.append(('assemble', args))
            return {'mode': 'plan'} if args.plan else {'setup_cleanup_complete': True}
        fake_installer.assemble.side_effect = assemble
        fetcher = mock.Mock()
        def fetch(item, cache, **kwargs):
            calls.append(('fetch', item['filename']))
            self.assertIn('opener', kwargs)  # --offline must forbid a cache miss download.
            return cache / item['filename']
        fetcher.fetch.side_effect = fetch
        fetcher.digest.return_value = 'a' * 64
        cef_helper, font_helper = mock.Mock(), mock.Mock()
        cef_helper.apply.side_effect = lambda *args: calls.append(('cef', args)) or {'mode': 'restored'}
        font_helper.apply.side_effect = lambda *args: calls.append(('icons', args)) or {'mode': 'applied'}
        args = self.setup_args('--install', '--offline', '--repair-icons', '--cache', str(self.root / 'cache'))
        result = setup.execute(args, installer=fake_installer, fetcher=fetcher, cef_helper=cef_helper,
                               font_helper=font_helper, environment={'DISPLAY': ':synthetic'}, checks=[])
        self.assertEqual([call[0] for call in calls], ['assemble', 'fetch', 'fetch', 'fetch', 'assemble', 'cef', 'icons'])
        options = calls[4][1]
        self.assertTrue(options.extract_app and options.initialize_prefix and options.run_installers)
        self.assertEqual(options.application_version, '1.147.5')
        self.assertEqual(options.app_installer_sha256, 'a' * 64)
        self.assertEqual(result['installation']['cef_resource_restore']['mode'], 'restored')
        self.assertEqual(result['installation']['icon_font_repair']['mode'], 'applied')
        self.assertFalse((self.root / 'cache').exists())

    def test_incomplete_setup_cleanup_prevents_all_post_install_repairs(self):
        self.seal()
        fake_installer = mock.Mock()
        fake_installer.parser = installer.parser
        fake_installer.assemble.side_effect = [{'mode': 'plan'}, {'setup_cleanup_complete': False}]
        fetcher = mock.Mock()
        fetcher.fetch.side_effect = lambda item, cache: cache / item['filename']
        fetcher.digest.return_value = 'a' * 64
        cef_helper, font_helper = mock.Mock(), mock.Mock()
        with self.assertRaisesRegex(RuntimeError, 'without confirmed guarded cleanup'):
            setup.execute(self.setup_args('--install', '--repair-icons'), installer=fake_installer,
                          fetcher=fetcher, cef_helper=cef_helper, font_helper=font_helper,
                          environment={'DISPLAY': ':synthetic'}, checks=[])
        cef_helper.apply.assert_not_called()
        font_helper.apply.assert_not_called()

    def test_missing_native_extractor_blocks_downloads(self):
        (self.wine / 'bin/innoextract').unlink()
        self.seal()
        fetcher = mock.Mock()
        with self.assertRaisesRegex(ValueError, 'bin/innoextract'):
            setup.execute(self.setup_args('--install'), installer=installer, fetcher=fetcher,
                          environment={'DISPLAY': ':synthetic'}, checks=[])
        fetcher.fetch.assert_not_called()

    def test_setup_target_cannot_be_inside_immutable_bundle(self):
        self.seal()
        args = self.setup_args()
        args.target = self.staging / 'installation'
        with self.assertRaisesRegex(ValueError, 'outside'):
            setup.execute(args, installer=installer)

    def test_host_baseline_and_auto_profile_are_explicit(self):
        good = setup.host_checks(release={'ID': 'debian', 'VERSION_ID': '13.7'}, system='Linux',
                                 machine='x86_64', libc=('glibc', '2.41'))
        self.assertTrue(all(x['okay'] for x in good))
        bad = setup.host_checks(release={'ID': 'debian', 'VERSION_ID': '12'}, system='Linux',
                                machine='aarch64', libc=('glibc', '2.36'))
        self.assertEqual({x['name'] for x in bad if not x['okay']}, {'distribution', 'architecture', 'glibc'})
        environment = {'XDG_RUNTIME_DIR': str(self.root), 'WAYLAND_DISPLAY': 'wayland-0'}
        with mock.patch.object(Path, 'exists', return_value=True), \
                mock.patch.object(Path, 'stat', return_value=mock.Mock(st_mode=stat.S_IFSOCK | 0o600)):
            self.assertEqual(setup.choose_profile('auto', environment=environment, find_library=lambda _: 'libvulkan.so.1'), 'gpu')
            self.assertEqual(setup.choose_profile('auto', environment=environment, find_library=lambda _: None), 'compatible')


if __name__ == '__main__':
    unittest.main()
