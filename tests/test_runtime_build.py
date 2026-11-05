"""Offline source-integrity and synthetic assertion-trace regression tests."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import resource
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('runtime_builder', ROOT / 'tools/build-runtime.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def make_archive(path, entries):
    with tarfile.open(path, 'w:gz') as output:
        for name, content, kind, link in entries:
            member = tarfile.TarInfo(name)
            member.type = kind
            member.linkname = link
            member.mode = 0o755 if kind == tarfile.DIRTYPE else 0o644
            member.size = len(content) if kind == tarfile.REGTYPE else 0
            output.addfile(member, io.BytesIO(content) if kind == tarfile.REGTYPE else None)


class RuntimeSourceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def archive_spec(self, content=b'pinned source bytes'):
        return {'url': 'https://source.invalid/pinned.tar.gz', 'filename': 'pinned.tar.gz',
                'sha256': hashlib.sha256(content).hexdigest()}

    def test_offline_verified_cache_never_connects(self):
        cache = self.root / 'cache'
        cache.mkdir()
        spec = self.archive_spec()
        (cache / spec['filename']).write_bytes(b'pinned source bytes')
        with mock.patch.object(builder, 'open_https', side_effect=AssertionError('network forbidden')):
            self.assertEqual(builder.fetch_archive(spec, cache, offline=True), cache / spec['filename'])

    def test_corrupted_or_missing_offline_cache_fails_closed(self):
        cache = self.root / 'cache'
        cache.mkdir()
        spec = self.archive_spec()
        with self.assertRaises(FileNotFoundError):
            builder.fetch_archive(spec, cache, offline=True)
        (cache / spec['filename']).write_bytes(b'corrupted')
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            builder.fetch_archive(spec, cache)
        self.assertEqual((cache / spec['filename']).read_bytes(), b'corrupted')

    def test_symlink_cache_entry_and_unsafe_filename_are_rejected(self):
        cache = self.root / 'cache'
        cache.mkdir()
        target = self.root / 'untouched'
        target.write_bytes(b'pinned source bytes')
        (cache / 'pinned.tar.gz').symlink_to(target)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            builder.fetch_archive(self.archive_spec(), cache)
        spec = self.archive_spec()
        spec['filename'] = '../untouched'
        with self.assertRaises(ValueError):
            builder.fetch_archive(spec, cache)
        self.assertEqual(target.read_bytes(), b'pinned source bytes')

    def test_download_hash_mismatch_leaves_no_cache_entry(self):
        cache = self.root / 'cache'
        response = io.BytesIO(b'tampered transport')
        response.geturl = lambda: 'https://source.invalid/pinned.tar.gz'
        with mock.patch.object(builder, 'open_https', return_value=response):
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                builder.fetch_archive(self.archive_spec(), cache)
        self.assertEqual(list(cache.iterdir()), [])

    def test_plain_http_and_downgrade_redirect_are_rejected(self):
        spec = self.archive_spec()
        spec['url'] = 'http://source.invalid/pinned.tar.gz'
        with self.assertRaisesRegex(ValueError, 'HTTPS'):
            builder.fetch_archive(spec, self.root / 'http')
        response = io.BytesIO(b'pinned source bytes')
        response.geturl = lambda: 'http://source.invalid/pinned.tar.gz'
        with mock.patch.object(builder, 'open_https', return_value=response):
            with self.assertRaisesRegex(ValueError, 'HTTPS'):
                builder.fetch_archive(self.archive_spec(), self.root / 'redirect')
        self.assertEqual(list((self.root / 'redirect').iterdir()), [])

    def test_intermediate_http_redirect_is_rejected(self):
        request = builder.urllib.request.Request('https://source.invalid/original')
        handler = builder.HTTPSRedirectHandler()
        with self.assertRaisesRegex(ValueError, 'HTTPS'):
            handler.redirect_request(request, None, 302, 'redirect', {}, 'http://source.invalid/intermediate')

    def test_archive_extracts_only_named_root_and_preserves_source(self):
        archive = self.root / 'source.tar.gz'
        make_archive(archive, [('upstream', b'', tarfile.DIRTYPE, ''),
                               ('upstream/src', b'', tarfile.DIRTYPE, ''),
                               ('upstream/src/file.c', b'original source\n', tarfile.REGTYPE, '')])
        destination = self.root / 'source'
        builder.extract_archive(archive, destination, 'upstream')
        self.assertEqual((destination / 'src/file.c').read_bytes(), b'original source\n')
        with self.assertRaises(FileExistsError):
            builder.extract_archive(archive, destination, 'upstream')

    @unittest.skipUnless(shutil.which('make'), 'make is needed for the release-timestamp regression')
    def test_release_timestamps_do_not_trigger_unavailable_autotools(self):
        archive = self.root / 'release.tar.gz'
        files = [('Makefile', b'all: aclocal.m4\n\t@echo prepared\naclocal.m4: configure.ac\n\t@false\n', 300),
                 ('aclocal.m4', b'generated release input\n', 200),
                 ('configure.ac', b'original older source\n', 100)]
        # Archive order deliberately puts the older source last, reproducing the
        # spurious regeneration when extraction assigns fresh timestamps.
        with tarfile.open(archive, 'w:gz') as output:
            for name, content, modified in files:
                info = tarfile.TarInfo('upstream/' + name)
                info.size, info.mtime, info.mode = len(content), modified, 0o644
                output.addfile(info, io.BytesIO(content))
        destination = self.root / 'release'
        builder.extract_archive(archive, destination, 'upstream')
        result = subprocess.run(['make', '--no-print-directory', 'all'], cwd=destination,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('prepared', result.stdout)

    def test_archive_traversal_links_devices_and_wrong_roots_are_rejected(self):
        cases = [('../escape', tarfile.REGTYPE, ''), ('upstream/../../escape', tarfile.REGTYPE, ''),
                 ('/absolute', tarfile.REGTYPE, ''), ('other/file', tarfile.REGTYPE, ''),
                 ('upstream/link', tarfile.SYMTYPE, '../../escape'),
                 ('upstream/hard', tarfile.LNKTYPE, 'upstream/something'),
                 ('upstream/device', tarfile.CHRTYPE, '')]
        for index, (name, kind, link) in enumerate(cases):
            with self.subTest(name=name, kind=kind):
                archive = self.root / f'bad-{index}.tar.gz'
                destination = self.root / f'dest-{index}'
                make_archive(archive, [('upstream/safe', b'safe', tarfile.REGTYPE, ''),
                                      (name, b'unsafe', kind, link)])
                with self.assertRaises(ValueError):
                    builder.extract_archive(archive, destination, 'upstream')
                self.assertFalse(destination.exists())
        self.assertFalse((self.root / 'escape').exists())

    @unittest.skipUnless(shutil.which('patch'), 'GNU patch is required for source preparation')
    def test_verified_patch_applies_and_modified_patch_is_refused(self):
        repository = self.root / 'repository'
        repository.mkdir()
        source = self.root / 'source'
        source.mkdir()
        target = source / 'test.c'
        target.write_text('before\n')
        patch = repository / 'fix.patch'
        patch.write_text('--- a/test.c\n+++ b/test.c\n@@ -1 +1 @@\n-before\n+after\n')
        config = {'patches': [{'path': 'fix.patch', 'sha256': builder.sha256(patch)}]}
        with tempfile.TemporaryFile() as log:
            builder.apply_patches(config, source, log, repository)
        self.assertEqual(target.read_text(), 'after\n')
        target.write_text('before\n')
        patch.write_text(patch.read_text().replace('+after', '+unexpected'))
        with tempfile.TemporaryFile() as log:
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                builder.apply_patches(config, source, log, repository)
        self.assertEqual(target.read_text(), 'before\n')

    @unittest.skipUnless(shutil.which('patch'), 'GNU patch is required for source preparation')
    def test_context_mismatch_is_detected_before_patch_mutation(self):
        repository = self.root / 'repository'
        repository.mkdir()
        source = self.root / 'source'
        source.mkdir()
        target = source / 'test.c'
        target.write_text('different upstream\n')
        patch = repository / 'fix.patch'
        patch.write_text('--- a/test.c\n+++ b/test.c\n@@ -1 +1 @@\n-before\n+after\n')
        config = {'patches': [{'path': 'fix.patch', 'sha256': builder.sha256(patch)}]}
        with tempfile.TemporaryFile() as log:
            with self.assertRaises(subprocess.CalledProcessError):
                builder.apply_patches(config, source, log, repository)
        self.assertEqual(target.read_text(), 'different upstream\n')
        self.assertFalse((source / 'test.c.rej').exists())

    def test_committed_lock_pins_all_source_and_patch_bytes(self):
        lock = json.loads((ROOT / 'runtime-lock.json').read_text())
        self.assertEqual(lock['schema_version'], 1)
        self.assertEqual(set(lock['components']), {'wine', 'x11', 'gamescope'})
        for component in lock['components'].values():
            for archive in [component['archive'], *component.get('extra_sources', [])]:
                self.assertTrue(archive['url'].startswith('https://'))
                self.assertRegex(archive['sha256'], r'^[0-9a-f]{64}$')
            for patch in component['patches']:
                path = ROOT.joinpath(*builder.safe_relative(patch['path']).parts)
                self.assertEqual(builder.sha256(path), patch['sha256'])
            self.assertTrue((ROOT / component['license_file']).is_file())

    def test_wine_build_enables_both_pe_architectures(self):
        with mock.patch.object(builder, 'run') as run, tempfile.TemporaryFile() as log:
            builder.build_component('wine', self.root / 'source', self.root, '2', log)
        configure = run.call_args_list[0].args[0]
        self.assertIn('--enable-archs=i386,x86_64', configure)
        self.assertNotIn('--enable-win64', configure)

    def test_missing_i686_toolchain_fails_before_building(self):
        def which(tool):
            return None if tool.startswith('i686-w64-mingw32-') else '/tools/' + tool
        with mock.patch.object(builder.shutil, 'which', side_effect=which), \
             mock.patch.object(builder.platform, 'machine', return_value='x86_64'):
            with self.assertRaisesRegex(ValueError, 'i686-w64-mingw32'):
                builder.check_dependencies(['wine'])

    def test_gamescope_rejects_old_meson_before_downloads(self):
        result = subprocess.CompletedProcess(['meson', '--version'], 0, '1.2.3\n', '')
        with mock.patch.object(builder.shutil, 'which', side_effect=lambda x: '/tools/' + x), \
             mock.patch.object(builder.platform, 'machine', return_value='x86_64'), \
             mock.patch.object(builder.subprocess, 'run', return_value=result):
            with self.assertRaisesRegex(ValueError, 'Meson >=1.3'):
                builder.check_dependencies(['gamescope'])

    def test_source_preparation_requires_no_compiler_or_build_system(self):
        with mock.patch.object(builder.shutil, 'which', side_effect=lambda x: '/tools/patch' if x == 'patch' else None):
            tools = builder.check_dependencies(['wine', 'gamescope'], prepare_only=True)
        self.assertEqual(tools, {'patch': 'patch'})


@unittest.skipUnless(sys.platform.startswith('linux') and shutil.which('cc'), 'Linux C compiler required')
class NativeAssertionTests(unittest.TestCase):
    def test_selected_assertion_stays_fatal_and_trace_excludes_assertion_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            library = root / 'libtrace.so'
            subprocess.run([sys.executable, ROOT / 'native/build-assert-trace.py', '--output', library],
                           check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            probe = root / 'probe'
            subprocess.run(['cc', ROOT / 'native/assert-probe.c', '-o', probe], check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            traces = root / 'traces'
            traces.mkdir(mode=0o700)
            env = os.environ.copy()
            env.update(LD_PRELOAD=str(library), QUANTOWER_CRASH_TRACE_DIR=str(traces))
            def disable_core_dump():
                resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
            result = subprocess.run([probe], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    preexec_fn=disable_core_dump, timeout=5)
            self.assertEqual(result.returncode, -signal.SIGABRT)
            files = list(traces.iterdir())
            self.assertEqual(len(files), 1)
            self.assertEqual(files[0].stat().st_mode & 0o777, 0o600)
            data = files[0].read_text()
            self.assertIn('module=', data)
            self.assertNotIn('private assertion', data)
            self.assertNotIn('private-file-name', data)
            self.assertNotIn(str(root), data)


if __name__ == '__main__':
    unittest.main()
