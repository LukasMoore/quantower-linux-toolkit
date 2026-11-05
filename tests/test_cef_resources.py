"""Synthetic browser resources only; no vendor bytes or app execution."""
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('cef_resources', ROOT / 'tools/restore-cef-resources.py')
cef = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cef)


class CefResourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name) / 'target'
        self.native = self.base / 'prefix/drive_c/AMPQuantowerInstalled/TradingPlatform/v1.147.5/bin/Native/Windows'
        self.native.mkdir(parents=True)
        (self.native / 'libcef.dll').write_bytes(b'synthetic engine')
        (self.base / 'application-version.json').write_text(json.dumps({'schema_version': 1, 'application_version': '1.147.5'}))
        (self.base / 'logs').mkdir(mode=0o700)
        (self.base / 'runtime-components').mkdir()
        self.manifest = self.base / 'runtime-components/manifest.json'
        self.manifest.write_text(json.dumps({'version': 1, 'required_files': {}}))
        self.package = Path(self.temp.name) / 'synthetic.zip'
        with zipfile.ZipFile(self.package, 'w') as archive:
            for name in ('libcef.dll', 'libEGL.dll', 'libGLESv2.dll'):
                archive.writestr('runtimes/win-x64/native/' + name, b'synthetic engine' if name == 'libcef.dll' else name.encode())
            for index in range(55):
                archive.writestr('CEF/win-x64/locales/test_' + str(index) + '.pak', str(index).encode())
        for name, value in [('PACKAGE_SHA256', cef.digest(self.package)), ('ENGINE_SHA256', cef.digest(self.native / 'libcef.dll'))]:
            patch = mock.patch.object(cef, name, value)
            patch.start(); self.addCleanup(patch.stop)
        for name in ('no_running_prefix', 'load'):
            patch = mock.patch.object(cef.profile_manager, name)
            patch.start(); self.addCleanup(patch.stop)

    def test_plan_writes_nothing_and_apply_is_idempotent_and_pinned(self):
        before = sorted(str(p) for p in self.base.rglob('*'))
        _, changes = cef.plan(self.base, self.package)
        self.assertEqual(len(changes), 57)
        self.assertEqual(before, sorted(str(p) for p in self.base.rglob('*')))
        first = cef.apply(self.base, self.package)
        self.assertEqual(first['resources_added'], 57)
        self.assertEqual(len(json.loads(self.manifest.read_text())['required_files']), 57)
        self.assertEqual(cef.apply(self.base, self.package)['resources_added'], 0)
        self.assertEqual((self.native / 'libcef.dll').read_bytes(), b'synthetic engine')

    def test_engine_mismatch_conflict_and_symlink_are_preserved(self):
        engine = self.native / 'libcef.dll'
        engine.write_bytes(b'newer engine')
        with self.assertRaisesRegex(ValueError, 'engine does not match'):
            cef.plan(self.base, self.package)
        engine.write_bytes(b'synthetic engine')
        resource = self.native / 'libEGL.dll'
        resource.write_bytes(b'preserve')
        with self.assertRaisesRegex(ValueError, 'differs'):
            cef.apply(self.base, self.package)
        self.assertEqual(resource.read_bytes(), b'preserve')
        resource.unlink()
        resource.symlink_to(engine)
        with self.assertRaisesRegex(ValueError, 'symlinks'):
            cef.apply(self.base, self.package)

    def test_launch_lock_and_active_prefix_block_mutation(self):
        with (self.base / 'logs/launch.lock').open('a') as lock:
            lock_path = self.base / 'logs/launch.lock'
            lock_path.chmod(0o600)
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):
                cef.apply(self.base, self.package)
        cef.profile_manager.no_running_prefix.side_effect = RuntimeError('active')
        with self.assertRaisesRegex(RuntimeError, 'active'):
            cef.apply(self.base, self.package)
        self.assertFalse((self.native / 'libEGL.dll').exists())

    def test_manifest_failure_removes_only_new_resources(self):
        before = self.manifest.read_bytes()
        original = cef.profile_manager.atomic_bytes
        calls = 0
        def fail_once(path, data, *args):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError('simulated storage failure')
            return original(path, data, *args)
        with mock.patch.object(cef.profile_manager, 'atomic_bytes', side_effect=fail_once):
            with self.assertRaisesRegex(OSError, 'storage failure'):
                cef.apply(self.base, self.package)
        self.assertEqual(self.manifest.read_bytes(), before)
        self.assertFalse((self.native / 'libEGL.dll').exists())

    def test_package_path_replacement_cannot_substitute_unverified_resource_bytes(self):
        replacement = self.package.with_name('replacement.zip')
        original_zip = zipfile.ZipFile
        with original_zip(self.package) as source, original_zip(replacement, 'w') as target:
            for entry in source.infolist():
                data = b'substituted DLL bytes' if entry.filename.endswith('/libEGL.dll') else source.read(entry)
                target.writestr(entry, data)
        def replace_path_then_open(source, *args, **kwargs):
            # The verified description stays open even though the public cache
            # pathname now identifies a different, internally valid package.
            os.replace(replacement, self.package)
            return original_zip(source, *args, **kwargs)
        with mock.patch.object(cef.zipfile, 'ZipFile', side_effect=replace_path_then_open):
            result = cef.apply(self.base, self.package)
        self.assertEqual(result['resources_added'], 57)
        self.assertEqual((self.native / 'libEGL.dll').read_bytes(), b'libEGL.dll')
        self.assertNotEqual(cef.digest(self.package), cef.PACKAGE_SHA256)

    def test_package_in_place_mutation_is_refused_before_any_resource_write(self):
        original_read = zipfile.ZipFile.read
        manifest_before = self.manifest.read_bytes()
        def mutate_after_last_member(archive, entry, *args, **kwargs):
            result = original_read(archive, entry, *args, **kwargs)
            if entry.filename == 'CEF/win-x64/locales/test_54.pak':
                # Change a trailing header byte after all member bytes were
                # read, proving the final description hash is checked too.
                with self.package.open('r+b') as stream:
                    stream.seek(-1, 2)
                    value = stream.read(1)
                    stream.seek(-1, 2)
                    stream.write(bytes([value[0] ^ 1]))
            return result
        with mock.patch.object(cef.zipfile.ZipFile, 'read', new=mutate_after_last_member):
            with self.assertRaisesRegex(ValueError, 'changed while reading'):
                cef.apply(self.base, self.package)
        self.assertFalse((self.native / 'libEGL.dll').exists())
        self.assertEqual(self.manifest.read_bytes(), manifest_before)
