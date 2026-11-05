"""Offline ELF closure tests: synthetic files, mocked metadata readers, no execution."""
import hashlib
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location(
    'collect_runtime_libs', Path(__file__).resolve().parents[1] / 'tools/collect-runtime-libs.py')
collector = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(collector)


class RuntimeDependencyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.root = self.directory / 'stage'
        self.root.mkdir()
        self.entry = self.elf('stage/bin/wine', b'synthetic executable')
        self.closures = {str(self.entry): [self.entry]}
        self.cache = ''
        self.calls = []

    def elf(self, name, data=b'synthetic library'):
        path = self.directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'\x7fELF' + data)
        return path

    def runner(self, args, **kwargs):
        self.calls.append((args, kwargs))
        self.assertTrue(kwargs.get('check'))
        if args == ['ldconfig', '-p']:
            return subprocess.CompletedProcess(args, 0, self.cache, '')
        self.assertEqual(args[:2], ['lddtree', '-l'])
        values = self.closures[args[2]]
        return subprocess.CompletedProcess(args, 0, '\n'.join(map(str, values)) + '\n', '')

    def collect(self, **kwargs):
        return collector.collect(self.root, 'payload/wine', runner=self.runner, **kwargs)

    def test_transitive_closure_flattens_links_and_records_exact_source_hash(self):
        direct = self.elf('system/libdirect.so.1')
        actual = self.elf('system/libtransitive.so.2.3', b'transitive bytes')
        linked = actual.with_name('libtransitive.so.2')
        linked.symlink_to(actual.name)
        self.closures[str(self.entry)] += [direct, linked]
        result = self.collect()
        records = {record['path']: record for record in result['files']}
        self.assertEqual(set(records), {'payload/wine/lib/libdirect.so.1',
                                        'payload/wine/lib/libtransitive.so.2'})
        target = self.root / 'lib/libtransitive.so.2'
        self.assertFalse(target.is_symlink())
        self.assertEqual(target.read_bytes(), actual.read_bytes())
        self.assertEqual(records['payload/wine/lib/libtransitive.so.2']['system_path'], str(actual))
        self.assertEqual(records['payload/wine/lib/libtransitive.so.2']['sha256'],
                         hashlib.sha256(actual.read_bytes()).hexdigest())

    def test_host_loader_glibc_and_gpu_stack_are_excluded_but_other_runtimes_ship(self):
        names = ['ld-linux-x86-64.so.2', 'libc.so.6', 'libm.so.6', 'libpthread.so.0',
                 'libmvec.so.1', 'libthread_db.so.1', 'libBrokenLocale.so.1',
                 'libGL.so.1', 'libEGL.so.1', 'libGLX_mesa.so.0', 'libGLdispatch.so.0',
                 'libdrm_amdgpu.so.1', 'libvulkan.so.1', 'libnvidia-glcore.so.1',
                 'libva-drm.so.2', 'libOpenCL.so.1']
        external = [self.elf('system/' + name) for name in names]
        runtime = self.elf('system/libstdc++.so.6')
        self.closures[str(self.entry)] += external + [runtime]
        result = self.collect()
        self.assertEqual(set(result['host_requirements']), set(names))
        self.assertEqual([p.name for p in (self.root / 'lib').iterdir()], ['libstdc++.so.6'])
        self.assertEqual(len(result['files']), 1)

    def test_configured_dlopen_library_and_its_dependencies_are_included(self):
        configured = self.elf('system64/libfontconfig.so.1')
        transitive = self.elf('system64/libexpat.so.1')
        ignored32 = self.elf('system32/libfontconfig.so.1', b'wrong ELF architecture')
        self.cache = (' libfontconfig.so.1 (libc6) => ' + str(ignored32) + '\n'
                      ' libfontconfig.so.1 (libc6,x86-64) => ' + str(configured) + '\n')
        config = self.directory / 'config.h'
        config.write_text('#define SONAME_LIBFONTCONFIG "libfontconfig.so.1"\n'
                          '#define SONAME_LIBVULKAN "libvulkan.so.1"\n')
        self.closures[str(configured)] = [configured, transitive]
        result = self.collect(config=config)
        self.assertEqual({Path(item['path']).name for item in result['files']},
                         {'libfontconfig.so.1', 'libexpat.so.1'})
        self.assertNotIn(str(ignored32), [call[0][-1] for call in self.calls])

    def test_missing_dlopen_or_unresolved_closure_aborts_before_any_copy(self):
        good = self.elf('system/libgood.so.1')
        self.closures[str(self.entry)] += [good, 'libmissing.so.1']
        with self.assertRaisesRegex(ValueError, 'Unresolved ELF dependency'):
            self.collect()
        self.assertFalse((self.root / 'lib').exists())
        self.closures[str(self.entry)] = [self.entry]
        config = self.directory / 'config.h'
        config.write_text('#define SONAME_LIBMISSING "libmissing.so.1"\n')
        with self.assertRaisesRegex(ValueError, 'dlopen dependency is absent'):
            self.collect(config=config)
        self.assertFalse((self.root / 'lib').exists())

    def test_metadata_reader_uses_only_staged_libs_and_discards_preload(self):
        with mock.patch.dict(os.environ, {'LD_PRELOAD': '/untrusted/injected.so',
                                          'LD_LIBRARY_PATH': '/untrusted/libraries'}):
            self.collect()
        environment = self.calls[0][1]['env']
        self.assertNotIn('LD_PRELOAD', environment)
        self.assertEqual(environment['LD_LIBRARY_PATH'], str(self.root / 'lib'))

    def test_only_explicit_missing_optional_dlopen_soname_is_reported(self):
        config = self.directory / 'config.h'
        config.write_text('#define SONAME_LIBODBC "libodbc.so"\n')
        with self.assertRaisesRegex(ValueError, 'dlopen dependency is absent'):
            self.collect(config=config)
        result = self.collect(config=config, optional_sonames=['libodbc.so'])
        self.assertEqual(result['unavailable_optional'], ['libodbc.so'])
        self.assertEqual(result['files'], [])
        self.assertFalse((self.root / 'lib').exists())

    def test_optional_dlopen_label_does_not_hide_unresolved_needed_dependency(self):
        self.closures[str(self.entry)].append('libodbc.so')
        with self.assertRaisesRegex(ValueError, 'Unresolved ELF dependency'):
            self.collect(optional_sonames=['libodbc.so'])
        self.assertFalse((self.root / 'lib').exists())

    def test_available_optional_dlopen_library_is_still_shipped(self):
        library = self.elf('system/libodbc.so')
        self.cache = ' libodbc.so (libc6,x86-64) => ' + str(library) + '\n'
        self.closures[str(library)] = [library]
        config = self.directory / 'config.h'
        config.write_text('#define SONAME_LIBODBC "libodbc.so"\n')
        result = self.collect(config=config, optional_sonames=['libodbc.so'])
        self.assertEqual(result['unavailable_optional'], [])
        self.assertEqual([item['path'] for item in result['files']], ['payload/wine/lib/libodbc.so'])

    def test_different_libraries_with_same_soname_fail_before_writes(self):
        one = self.elf('first/libduplicate.so.1', b'first')
        two = self.elf('second/libduplicate.so.1', b'second')
        self.closures[str(self.entry)] += [one, two]
        with self.assertRaisesRegex(ValueError, 'Conflicting libraries'):
            self.collect()
        self.assertFalse((self.root / 'lib').exists())

    def test_identical_libraries_from_two_paths_are_deduplicated(self):
        one = self.elf('first/libduplicate.so.1')
        two = self.elf('second/libduplicate.so.1')
        self.closures[str(self.entry)] += [one, two]
        result = self.collect()
        self.assertEqual(len(result['files']), 1)
        self.assertEqual((self.root / 'lib/libduplicate.so.1').read_bytes(), one.read_bytes())

    def test_explicit_helper_is_copied_with_its_runtime_closure(self):
        helper = self.elf('system/bin/xkbcomp', b'helper')
        library = self.elf('system/libhelper.so.1')
        self.closures[str(helper)] = [helper, library]
        result = self.collect(helpers=[helper])
        self.assertEqual({item['path'] for item in result['files']},
                         {'payload/wine/bin/xkbcomp', 'payload/wine/lib/libhelper.so.1'})
        self.assertEqual((self.root / 'bin/xkbcomp').read_bytes(), helper.read_bytes())

    def test_same_named_different_helpers_fail_before_mutating_stage(self):
        one = self.elf('first/helper', b'first helper')
        two = self.elf('second/helper', b'second helper')
        self.closures[str(one)] = [one]
        self.closures[str(two)] = [two]
        with self.assertRaisesRegex(ValueError, 'Conflicting|same.*name|duplicate'):
            self.collect(helpers=[one, two])
        self.assertFalse((self.root / 'bin/helper').exists())

    def test_existing_different_target_is_preserved_and_preflight_is_atomic(self):
        (self.root / 'lib').mkdir()
        keep = self.root / 'lib/libsecond.so.1'
        keep.write_bytes(b'existing bytes')
        first = self.elf('system/libfirst.so.1')
        second = self.elf('system/libsecond.so.1')
        self.closures[str(self.entry)] += [first, second]
        with self.assertRaisesRegex(ValueError, 'already differs'):
            self.collect()
        self.assertEqual(keep.read_bytes(), b'existing bytes')
        self.assertFalse((self.root / 'lib/libfirst.so.1').exists())

    def test_dependency_target_symlink_is_never_followed(self):
        library = self.elf('system/libexternal.so.1')
        (self.root / 'lib').mkdir()
        (self.root / 'lib/libexternal.so.1').symlink_to(library)
        self.closures[str(self.root / 'lib/libexternal.so.1')] = [library]
        self.closures[str(self.entry)].append(library)
        with self.assertRaisesRegex(ValueError, 'symlink|escape'):
            self.collect()
        self.assertTrue((self.root / 'lib/libexternal.so.1').is_symlink())

    def test_parent_symlink_cannot_redirect_copy_outside_staged_root(self):
        external = self.directory / 'outside'
        external.mkdir()
        (self.root / 'lib').symlink_to(external, target_is_directory=True)
        library = self.elf('system/libescaped.so.1')
        self.closures[str(self.entry)].append(library)
        with self.assertRaisesRegex(ValueError, 'symlink|outside|escape|redirected'):
            self.collect()
        self.assertFalse((external / library.name).exists())

    def test_private_x11_is_not_replaced_by_builder_system_xlib(self):
        xlib = self.elf('system/libX11.so.6')
        bridge = self.elf('system/libX11-xcb.so.1')
        xcb = self.elf('system/libxcb.so.1')
        self.closures[str(self.entry)] += [xlib, bridge, xcb]
        result = self.collect(private_x11=True)
        self.assertEqual([Path(item['path']).name for item in result['files']], ['libxcb.so.1'])
        self.assertFalse((self.root / 'lib/libX11.so.6').exists())

    def test_wine_prefix_and_unsafe_payload_are_rejected_without_metadata_reads(self):
        for payload in ('../outside', 'payload/../../outside', '/payload/wine', 'payload/Wine'):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                collector.collect(self.root, payload, runner=self.runner)
        (self.directory / 'user.reg').write_text('synthetic prefix marker')
        with self.assertRaisesRegex(ValueError, 'Wine prefix'):
            self.collect()
        self.assertEqual(self.calls, [])


if __name__ == '__main__':
    unittest.main()
