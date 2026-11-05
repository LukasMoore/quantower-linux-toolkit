"""Binary/source coverage checks use synthetic artifacts, never installed apps."""
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('bundle_notices',
                                             Path(__file__).resolve().parents[1] / 'tools/bundle-notices.py')
notices = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(notices)


def digest(data):
    return hashlib.sha256(data).hexdigest()


class BundleNoticesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bundle = self.root / 'bundle'
        self.bundle.mkdir()
        path = self.bundle / 'payload/wine/bin/wine'
        path.parent.mkdir(parents=True)
        path.write_bytes(b'synthetic binary')
        self.notice_root = self.root / 'notice-inputs'
        (self.notice_root / 'wine').mkdir(parents=True)
        self.notice = b'Synthetic full license and copyright notice\n'
        (self.notice_root / 'wine/LICENSE').write_bytes(self.notice)
        self.artifacts = {'upstream/wine.tar.xz': b'synthetic source archive',
                          'toolkit/build.py': b'print("synthetic build recipe")\n'}
        self.inventory = {'schema_version': 1, 'source_archive_root': 'sources', 'components': [
            {'id': 'wine', 'name': 'Wine fixture', 'version': '1.0', 'license': 'LGPL-2.1-or-later',
             'covers': ['payload/wine/'],
             'notices': [{'path': 'wine/LICENSE', 'sha256': digest(self.notice)}],
             'sources': [{'path': 'upstream/wine.tar.xz', 'sha256': digest(self.artifacts['upstream/wine.tar.xz'])}],
             'recipes': [{'path': 'toolkit/build.py', 'sha256': digest(self.artifacts['toolkit/build.py'])}]}]}
        self.archive = self.root / 'runtime-sources.tar.gz'
        self.make_archive()

    def make_archive(self, extra=None):
        with tarfile.open(self.archive, 'w:gz') as tar:
            for name, data in self.artifacts.items():
                entry = tarfile.TarInfo('sources/' + name)
                entry.size = len(data)
                tar.addfile(entry, io.BytesIO(data))
            if extra is not None:
                tar.addfile(extra)

    def collect(self, inventory=None):
        return notices.collect(self.bundle, inventory or self.inventory, self.notice_root,
                                self.archive, self.bundle / 'licenses')

    def test_actual_sources_notices_and_payload_are_covered_without_absolute_paths(self):
        self.inventory['components'][0]['notices'][0]['copyright_reference'] = '/usr/share/common-licenses/GPL-3.0'
        manifest, offer = self.collect()
        self.assertEqual(manifest['payload_owners'], {'payload/wine/bin/wine': 'wine'})
        self.assertEqual(manifest['components'][0]['notices'][0]['bundle_path'],
                         'licenses/components/wine/wine/LICENSE')
        self.assertEqual(manifest['components'][0]['notices'][0]['copyright_reference'],
                         '/usr/share/common-licenses/GPL-3.0')
        self.assertEqual((self.bundle / 'licenses/components/wine/wine/LICENSE').read_bytes(), self.notice)
        self.assertEqual(offer['sha256'], digest(self.archive.read_bytes()))
        self.assertEqual(offer['archive'], 'runtime-sources.tar.gz')
        self.assertNotIn(str(self.root), json.dumps(manifest) + json.dumps(offer))
        self.assertEqual(json.loads((self.bundle / 'source-offer.json').read_text()), offer)

    def test_unowned_or_ambiguously_owned_payload_is_refused(self):
        path = self.bundle / 'payload/unknown.so'
        path.write_bytes(b'uncovered')
        with self.assertRaisesRegex(ValueError, 'no source/license'):
            self.collect()
        path.unlink()
        other = copy.deepcopy(self.inventory['components'][0])
        other['id'] = 'duplicate-coverage'
        self.inventory['components'].append(other)
        with self.assertRaisesRegex(ValueError, 'ambiguous'):
            self.collect()
        self.assertFalse((self.bundle / 'licenses').exists())

    def test_contained_output_with_parent_segments_has_canonical_bundle_paths(self):
        output = self.bundle / '..' / self.bundle.name / 'licenses'
        manifest, _ = notices.collect(self.bundle, self.inventory, self.notice_root,
                                      self.archive, output)
        notice = manifest['components'][0]['notices'][0]
        self.assertEqual(notice['bundle_path'], 'licenses/components/wine/wine/LICENSE')
        self.assertEqual((self.bundle / notice['bundle_path']).read_bytes(), self.notice)
        self.assertTrue((self.bundle / 'licenses/manifest.json').is_file())

    def test_tampered_notice_or_source_never_produces_publishable_metadata(self):
        (self.notice_root / 'wine/LICENSE').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'Notice checksum'):
            self.collect()
        (self.notice_root / 'wine/LICENSE').write_bytes(self.notice)
        self.artifacts['upstream/wine.tar.xz'] = b'changed'
        self.make_archive()
        with self.assertRaisesRegex(ValueError, 'Source companion checksum'):
            self.collect()
        self.assertFalse((self.bundle / 'source-offer.json').exists())

    def test_missing_recipe_and_unknown_license_are_refused(self):
        self.artifacts.pop('toolkit/build.py')
        self.make_archive()
        with self.assertRaisesRegex(ValueError, 'omits a required artifact'):
            self.collect()
        self.inventory['components'][0]['license'] = 'UNKNOWN'
        with self.assertRaisesRegex(ValueError, 'reviewed name, version and license'):
            self.collect()

    def test_companion_links_traversal_and_duplicate_members_are_refused(self):
        link = tarfile.TarInfo('sources/link')
        link.type = tarfile.SYMTYPE
        link.linkname = '/outside'
        self.make_archive(link)
        with self.assertRaisesRegex(ValueError, 'link or special'):
            self.collect()
        entry = tarfile.TarInfo('../outside')
        self.make_archive(entry)
        with self.assertRaisesRegex(ValueError, 'Unsafe inventory path'):
            self.collect()
        entry = tarfile.TarInfo('sources/toolkit/build.py')
        self.make_archive(entry)
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            self.collect()

    def test_payload_links_must_be_relative_contained_and_covered(self):
        link = self.bundle / 'payload/wine/bin/wine-alias'
        link.symlink_to('wine')
        manifest, _ = self.collect()
        self.assertEqual(manifest['payload_owners']['payload/wine/bin/wine-alias'], 'wine')

    def test_escaping_payload_or_notice_symlink_is_refused(self):
        outside = self.root / 'outside'
        outside.write_bytes(b'outside')
        link = self.bundle / 'payload/wine/escape'
        link.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'nonrelocatable or escaping'):
            self.collect()
        link.unlink()
        notice = self.notice_root / 'wine/LICENSE'
        notice.unlink()
        notice.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'escapes'):
            self.collect()

    def test_existing_notices_or_source_manifest_are_preserved(self):
        output = self.bundle / 'licenses'
        output.mkdir()
        keep = output / 'keep'
        keep.write_bytes(b'existing')
        with self.assertRaisesRegex(ValueError, 'new directory'):
            self.collect()
        self.assertEqual(keep.read_bytes(), b'existing')
        keep.unlink()
        output.rmdir()
        offer = self.bundle / 'source-offer.json'
        offer.write_bytes(b'keep metadata')
        with self.assertRaises(FileExistsError):
            self.collect()
        self.assertEqual(offer.read_bytes(), b'keep metadata')

    def add_debian_component(self):
        file = self.bundle / 'payload/wine/lib/libexample.so.1'
        file.parent.mkdir()
        file.write_bytes(b'packaged dependency')
        original, packaging = b'upstream source', b'Debian build rules'
        self.artifacts['debian/example_1.2.orig.tar.xz'] = original
        self.artifacts['debian/example_1.2-3.debian.tar.xz'] = packaging
        control = ('Source: example\nVersion: 1.2-3\nChecksums-Sha256:\n '
                   + digest(original) + ' ' + str(len(original)) + ' example_1.2.orig.tar.xz\n '
                   + digest(packaging) + ' ' + str(len(packaging)) + ' example_1.2-3.debian.tar.xz\n').encode()
        self.artifacts['debian/example_1.2-3.dsc'] = control
        component = copy.deepcopy(self.inventory['components'][0])
        component.update(id='debian-libexample1-amd64', name='libexample1:amd64', version='1.2-3+b1',
                         covers=['payload/wine/lib/libexample.so.1'],
                         sources=[{'path': name, 'sha256': digest(data)} for name, data in self.artifacts.items()
                                  if name.startswith('debian/')],
                         debian_source={'package': 'example', 'version': '1.2-3', 'dsc': 'debian/example_1.2-3.dsc'})
        self.inventory['components'].append(component)
        self.make_archive()
        return component

    def test_debian_exact_file_ownership_overrides_the_runtime_directory(self):
        self.add_debian_component()
        manifest, _ = self.collect()
        self.assertEqual(manifest['payload_owners']['payload/wine/lib/libexample.so.1'], 'debian-libexample1-amd64')

    def test_debian_dsc_version_and_complete_source_set_are_required(self):
        component = self.add_debian_component()
        component['debian_source']['version'] = 'different'
        with self.assertRaisesRegex(ValueError, 'package/version'):
            self.collect()
        component['debian_source']['version'] = '1.2-3'
        component['sources'] = [a for a in component['sources'] if '.orig.' not in a['path']]
        with self.assertRaisesRegex(ValueError, 'dsc source member'):
            self.collect()

    def test_incorporated_debian_runtime_sources_are_validated_without_fake_payload_owners(self):
        component = self.add_debian_component()
        self.inventory['components'].remove(component)
        wine = self.inventory['components'][0]
        wine['sources'].extend(component['sources'])
        wine['incorporated_debian_sources'] = [component['debian_source']]
        wine['incorporated_debian_sources'][0]['version'] = 'wrong'
        with self.assertRaisesRegex(ValueError, 'package/version'):
            self.collect()
        wine['incorporated_debian_sources'][0]['version'] = '1.2-3'
        manifest, _ = self.collect()
        self.assertEqual(set(manifest['payload_owners'].values()), {'wine'})

    def test_clear_signed_debian_control_body_and_crlf_are_supported(self):
        body = 'Source: example\r\nVersion: 1.2-3\r\nChecksums-Sha256:\r\n a 1 source.tar\r\n'
        signed = '-----BEGIN PGP SIGNED MESSAGE-----\r\nHash: SHA256\r\n\r\n' + body + '-----BEGIN PGP SIGNATURE-----\r\nignored'
        self.assertEqual(notices.debian_source_control(signed)['Version'], '1.2-3')


if __name__ == '__main__':
    unittest.main()
