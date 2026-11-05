"""Prepare runtime trees using synthetic files; no compiler or runtime is run."""
import importlib.util
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import struct
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('prepare_runtime_test', ROOT / 'tools/prepare-runtime.py')
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


class RuntimePrepareTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / 'stage'
        self.source.mkdir()
        self.target = self.root / 'prepared'

    def file(self, relative, data=b'synthetic runtime data'):
        path = self.source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def test_absolute_and_relative_links_survive_relocation_and_removed_build_stage(self):
        library = self.file('lib/libfixture.so.1')
        (self.source / 'lib/absolute.so').symlink_to(library)
        (self.source / 'lib/relative.so').symlink_to('absolute.so')
        prepare.prepare(self.source, self.target)
        self.assertEqual(os.readlink(self.source / 'lib/absolute.so'), str(library))
        for name in ('absolute.so', 'relative.so'):
            link = self.target / 'lib' / name
            self.assertFalse(Path(os.readlink(link)).is_absolute())
            self.assertTrue(link.resolve().is_relative_to(self.target))
        relocated = self.root / 'new location with spaces'
        self.target.rename(relocated)
        shutil.rmtree(self.source)
        self.assertEqual((relocated / 'lib/absolute.so').read_bytes(), b'synthetic runtime data')
        self.assertEqual((relocated / 'lib/relative.so').read_bytes(), b'synthetic runtime data')

    def test_development_files_are_pruned_and_runtime_data_is_retained(self):
        removed = ('include/header.h', 'lib/pkgconfig/fixture.pc', 'share/man/man1/fixture.1',
                   'lib/libfixture.a', 'lib/libfixture.la')
        retained = ('lib/libfixture.so.1', 'bin/helper', 'share/wine/fonts/font.ttf',
                    'share/X11/locale/fixture/Compose')
        for name in (*removed, *retained):
            self.file(name)
        result = prepare.prepare(self.source, self.target)
        self.assertFalse(result['strip_debug'])
        self.assertTrue(all(not (self.target / name).exists() for name in removed))
        self.assertTrue(all((self.target / name).read_bytes() == b'synthetic runtime data' for name in retained))
        self.assertTrue(all((self.source / name).exists() for name in removed))

    def test_external_links_fail_before_creating_target(self):
        outside = self.root / 'outside'
        outside.write_bytes(b'keep outside bytes')
        (self.source / 'escape').symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'external symlink'):
            prepare.prepare(self.source, self.target)
        self.assertFalse(self.target.exists())
        self.assertEqual(outside.read_bytes(), b'keep outside bytes')

    def test_existing_and_overlapping_targets_are_preserved(self):
        existing = self.target
        existing.mkdir()
        sentinel = existing / 'keep'
        sentinel.write_bytes(b'keep existing bytes')
        for target in (existing, self.source / 'nested', self.root):
            with self.assertRaisesRegex(ValueError, 'new directory separate'):
                prepare.prepare(self.source, target)
        self.assertEqual(sentinel.read_bytes(), b'keep existing bytes')

    def test_strip_distinguishes_pe_modules_from_ne_fonts_and_invalid_mz_files(self):
        def executable(signature, offset=64, size=96):
            data = bytearray(size)
            data[:2] = b'MZ'
            data[60:64] = struct.pack('<I', offset)
            if offset <= size - len(signature):
                data[offset:offset + len(signature)] = signature
            return bytes(data)
        self.file('lib/module.so', b'\x7fELFsynthetic module')
        self.file('bin/module.exe', executable(b'PE\0\0'))
        fonts = {'share/wine/fonts/fixture.fon': executable(b'NE\0\0'),
                 'share/wine/fonts/truncated.fon': b'MZshort',
                 'share/wine/fonts/invalid-offset.fon': executable(b'PE\0\0', offset=4096)}
        for name, data in fonts.items():
            self.file(name, data)
        with mock.patch.object(prepare.subprocess, 'run') as run:
            result = prepare.prepare(self.source, self.target, strip=True)
        self.assertEqual(result['stripped'], {'elf': 1, 'pe': 1})
        commands = {(call.args[0][0], Path(call.args[0][-1]).relative_to(self.target).as_posix())
                    for call in run.call_args_list}
        self.assertEqual(commands, {('strip', 'lib/module.so'),
                                    ('x86_64-w64-mingw32-strip', 'bin/module.exe')})
        self.assertTrue(all((self.target / name).read_bytes() == data for name, data in fonts.items()))
        self.assertTrue(all((self.source / name).read_bytes() == data for name, data in fonts.items()))

    def font(self, name, data=None):
        directory = self.root / 'font-inputs'
        directory.mkdir(exist_ok=True)
        path = directory / name
        path.write_bytes(data if data is not None else b'\0\1\0\0' + b'synthetic unchanged TrueType fixture')
        return path

    def test_add_twelve_font_faces_preserves_bytes_and_records_exact_sources_without_stripping(self):
        self.target.mkdir()
        fonts = [self.font(f'{family}-{face}.ttf') for family in ('Sans', 'Serif', 'SansMono')
                 for face in ('Regular', 'Bold', 'Oblique', 'BoldOblique')]
        before = {font.name: font.read_bytes() for font in fonts}
        with mock.patch.object(prepare.subprocess, 'run', side_effect=AssertionError('No stripping or runtime execution')):
            mapping = prepare.add_fonts(self.target, fonts)
        self.assertEqual(len(mapping), 12)
        for entry, font in zip(mapping, fonts):
            self.assertEqual(entry['path'], 'share/wine/fonts/' + font.name)
            self.assertEqual(entry['system_path'], str(font.resolve()))
            self.assertEqual(entry['sha256'], hashlib.sha256(before[font.name]).hexdigest())
            self.assertEqual((self.target / entry['path']).read_bytes(), before[font.name])
            self.assertEqual(font.read_bytes(), before[font.name])

    def test_font_batch_is_fully_validated_before_any_destination_creation(self):
        self.target.mkdir()
        good = self.font('good.ttf')
        bad = self.font('bad.ttf', b'OTTO' + b'not TrueType sfnt')
        with self.assertRaisesRegex(ValueError, 'TrueType sfnt'):
            prepare.add_fonts(self.target, [good, bad])
        self.assertFalse((self.target / 'share').exists())
        oversized = self.font('oversized.ttf')
        with oversized.open('ab') as stream:
            stream.truncate(prepare.MAX_FONT_BYTES + 1)
        with self.assertRaisesRegex(ValueError, '32 MiB'):
            prepare.add_fonts(self.target, [oversized])
        fifo = self.root / 'pipe.ttf'
        os.mkfifo(fifo)
        with self.assertRaisesRegex(ValueError, 'regular file'):
            prepare.add_fonts(self.target, [fifo])
        self.assertFalse((self.target / 'share').exists())

    def test_font_source_and_destination_symlinks_are_refused(self):
        self.target.mkdir()
        good = self.font('good.ttf')
        alias = self.root / 'alias.ttf'
        alias.symlink_to(good)
        with self.assertRaisesRegex(ValueError, 'symlinks'):
            prepare.add_fonts(self.target, [alias])
        alias_parent = self.root / 'alias-parent'
        alias_parent.symlink_to(good.parent, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlinks'):
            prepare.add_fonts(self.target, [alias_parent / good.name])
        outside = self.root / 'outside'
        outside.mkdir()
        (self.target / 'share').symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlinks'):
            prepare.add_fonts(self.target, [good])
        self.assertEqual(list(outside.iterdir()), [])

    def test_font_conflicts_and_duplicate_names_are_refused_before_copy(self):
        self.target.mkdir()
        good = self.font('good.ttf')
        other = self.font('other.ttf')
        directory = self.target / 'share/wine/fonts'
        directory.mkdir(parents=True)
        existing = directory / 'GOOD.TTF'
        existing.write_bytes(b'preserve existing font')
        with self.assertRaisesRegex(FileExistsError, 'already exists'):
            prepare.add_fonts(self.target, [other, good])
        self.assertFalse((directory / other.name).exists())
        self.assertEqual(existing.read_bytes(), b'preserve existing font')
        existing.unlink()
        with self.assertRaisesRegex(ValueError, 'unique'):
            prepare.add_fonts(self.target, [good, good])
        self.assertEqual(list(directory.iterdir()), [])

    def test_fonts_cannot_be_added_inside_installed_runtimes_or_wine_prefixes(self):
        good = self.font('good.ttf')
        cases = [('installed', 'prefix/user.reg', 'runner/opt/wine'),
                 ('wine-prefix', 'user.reg', 'drive_c/runtime'),
                 ('staged-install', 'runtime-components/manifest.json', 'runner/opt/wine')]
        for name, marker, location in cases:
            base = self.root / name
            runtime = base / location
            runtime.mkdir(parents=True)
            state = base / marker
            state.parent.mkdir(parents=True, exist_ok=True)
            state.write_bytes(b'private state preserved')
            with self.assertRaisesRegex(ValueError, 'never an installed runtime'):
                prepare.add_fonts(runtime, [good])
            self.assertFalse((runtime / 'share').exists())
            self.assertEqual(state.read_bytes(), b'private state preserved')

    def test_prepare_cli_repeated_font_inputs_return_mapping(self):
        self.file('lib/runtime.so')
        fonts = [self.font('first.ttf'), self.font('second.ttf')]
        output = io.StringIO()
        arguments = ['--source', str(self.source), '--target', str(self.target)]
        for font in fonts:
            arguments += ['--font', str(font)]
        with mock.patch('sys.stdout', output):
            prepare.main(arguments)
        report = json.loads(output.getvalue())
        self.assertEqual(len(report['fonts']), 2)
        self.assertTrue(all((self.target / item['path']).read_bytes() == font.read_bytes()
                            for item, font in zip(report['fonts'], fonts)))


if __name__ == '__main__':
    unittest.main()
