"""Debian provenance collection uses a fake root and mocked dpkg discovery."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('collect_debian_notices',
                                             Path(__file__).resolve().parents[1] / 'tools/collect-debian-notices.py')
collector = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(collector)


def sha(data):
    return hashlib.sha256(data).hexdigest()


class DebianNoticesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.rootfs = self.root / 'rootfs'
        (self.rootfs / 'usr/lib').mkdir(parents=True)
        (self.rootfs / 'usr/lib/libexample.so').write_bytes(b'synthetic library')
        (self.rootfs / 'lib').symlink_to('usr/lib', target_is_directory=True)
        (self.rootfs / 'usr/lib/libalias.so').symlink_to('/usr/lib/libexample.so')
        (self.rootfs / 'usr/share/doc/libexample1').mkdir(parents=True)
        self.copyright = b'Copyright: Synthetic fixture authors\nLicense: LGPL-2.1+\n See /usr/share/common-licenses/LGPL-2.1.\n'
        (self.rootfs / 'usr/share/doc/libexample1/copyright').write_bytes(self.copyright)
        (self.rootfs / 'usr/share/common-licenses').mkdir()
        (self.rootfs / 'usr/share/common-licenses/LGPL-2.1').write_bytes(b'Synthetic complete common-license text')
        self.sources = self.root / 'sources'
        self.sources.mkdir()
        data = b'synthetic upstream and packaging source'
        (self.sources / 'example_1.2-3.tar.xz').write_bytes(data)
        self.control = ('Source: example\nVersion: 1.2-3\nChecksums-Sha256:\n '
                        + sha(data) + ' ' + str(len(data)) + ' example_1.2-3.tar.xz\n')
        (self.sources / 'example_1.2-3.dsc').write_text(self.control)
        self.output = self.root / 'notices'
        self.mappings = [{'path': 'payload/wine/lib/libexample.so', 'system_path': '/lib/libalias.so'}]

    def runner(self, args, **kwargs):
        if '-S' in args:
            if args[-1] == '/usr/lib/libexample.so':
                return subprocess.CompletedProcess(args, 0, 'libexample1:amd64: /usr/lib/libexample.so\n', '')
            return subprocess.CompletedProcess(args, 1, '', 'not found')
        self.assertIn('-W', args)
        return subprocess.CompletedProcess(args, 0, 'libexample1:amd64\t1.2-3+b1\texample\t1.2-3\n', '')

    def test_rooted_resolution_stays_inside_export_even_for_absolute_links(self):
        self.assertEqual(collector.rooted(self.rootfs, '/lib/libalias.so'), self.rootfs / 'usr/lib/libexample.so')
        (self.rootfs / 'bad').symlink_to('../../outside')
        with self.assertRaisesRegex(ValueError, 'escapes'):
            collector.rooted(self.rootfs, '/bad')

    def test_exact_package_and_source_metadata_notices_and_common_licenses_are_collected(self):
        result = collector.collect(self.rootfs, self.mappings, self.sources, self.output, self.runner)
        self.assertEqual(len(result), 1)
        component = result[0]
        self.assertEqual(component['version'], '1.2-3+b1')
        self.assertEqual(component['debian_source']['version'], '1.2-3')
        self.assertEqual(component['covers'], ['payload/wine/lib/libexample.so'])
        self.assertEqual(component['declared_license_labels'], ['LGPL-2.1+'])
        self.assertEqual((self.output / 'debian/libexample1:amd64/copyright').read_bytes(), self.copyright)
        self.assertTrue((self.output / 'debian/common-licenses/LGPL-2.1').is_file())
        self.assertNotIn(str(self.root), json.dumps(result))
        self.assertEqual(json.loads((self.output / 'components.json').read_text())['components'], result)

    def test_ambiguous_package_owner_is_refused(self):
        def ambiguous(args, **kwargs):
            return subprocess.CompletedProcess(args, 0,
                'libexample1:amd64: /usr/lib/libexample.so\nlibother1:amd64: /usr/lib/libexample.so\n', '')
        with self.assertRaisesRegex(ValueError, 'one exact Debian package owner'):
            collector.package_for_file(self.rootfs, '/usr/lib/libexample.so', ambiguous)

    def test_equivalent_debian_common_license_filename_is_recorded(self):
        copyright = self.rootfs / 'usr/share/doc/libexample1/copyright'
        copyright.write_text('License: GPL-3.0\n See "/usr/share/common-licenses/GPL-3.0".\n')
        (self.rootfs / 'usr/share/common-licenses/GPL-3').write_bytes(b'Synthetic complete GPL version 3')
        result = collector.collect(self.rootfs, self.mappings, self.sources, self.output, self.runner)
        notice = result[0]['notices'][1]
        self.assertEqual(notice['path'], 'debian/common-licenses/GPL-3')
        self.assertEqual(notice['copyright_reference'], '/usr/share/common-licenses/GPL-3.0')

    def test_copied_dependency_hash_must_match_package_file(self):
        self.mappings[0]['sha256'] = sha(b'wrong input')
        with self.assertRaisesRegex(ValueError, 'hash differs'):
            collector.collect(self.rootfs, self.mappings, self.sources, self.output, self.runner)
        self.assertFalse(self.output.exists())

    def test_wrong_or_tampered_source_package_cannot_create_notices(self):
        path = self.sources / 'example_1.2-3.dsc'
        path.write_text(self.control.replace('Version: 1.2-3', 'Version: 1.2-4'))
        with self.assertRaisesRegex(ValueError, 'Exact Debian source package is unavailable'):
            collector.collect(self.rootfs, self.mappings, self.sources, self.output, self.runner)
        self.assertFalse(self.output.exists())
        path.write_text(self.control)
        (self.sources / 'example_1.2-3.tar.xz').write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError, 'Missing or changed Debian source artifact'):
            collector.collect(self.rootfs, self.mappings, self.sources, self.output, self.runner)

    def test_missing_copyright_or_referenced_common_license_fails_closed(self):
        (self.rootfs / 'usr/share/common-licenses/LGPL-2.1').unlink()
        with self.assertRaisesRegex(ValueError, 'unavailable common license'):
            collector.collect(self.rootfs, self.mappings, self.sources, self.output, self.runner)
        self.assertFalse(self.output.exists())
        (self.rootfs / 'usr/share/doc/libexample1/copyright').unlink()
        with self.assertRaisesRegex(ValueError, 'copyright notice is missing'):
            collector.collect(self.rootfs, self.mappings, self.sources, self.output, self.runner)

    def test_existing_collection_output_is_never_overwritten(self):
        self.output.mkdir()
        keep = self.output / 'keep'
        keep.write_text('existing data')
        with self.assertRaises(FileExistsError):
            collector.collect(self.rootfs, self.mappings, self.sources, self.output, self.runner)
        self.assertEqual(keep.read_text(), 'existing data')


if __name__ == '__main__':
    unittest.main()
