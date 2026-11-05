"""Prepare runtime trees using synthetic files; no compiler or runtime is run."""
import importlib.util
import os
from pathlib import Path
import shutil
import tempfile
import unittest

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


if __name__ == '__main__':
    unittest.main()
