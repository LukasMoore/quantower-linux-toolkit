"""Pinned DXVK preparation is exercised with synthetic PE/archive fixtures."""
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import struct
import tarfile
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('fetch_dxvk', Path(__file__).resolve().parents[1] / 'tools/fetch-dxvk.py')
dxvk = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(dxvk)


def sha(data):
    return hashlib.sha256(data).hexdigest()


class DXVKFetchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cache = self.root / 'cache'
        self.cache.mkdir()
        self.output = self.root / 'dxvk'
        dll = bytearray(160)
        dll[:2] = b'MZ'
        struct.pack_into('<I', dll, 0x3c, 64)
        dll[64:68] = b'PE\0\0'
        struct.pack_into('<H', dll, 68, 0x8664)
        struct.pack_into('<H', dll, 86, 0x2000)
        struct.pack_into('<H', dll, 88, 0x20b)
        self.dll = bytes(dll)
        self.notice = b'Synthetic complete license notice\n'
        binary = self.archive('binary.tar.gz', {'binary/x64/d3d9.dll': self.dll})
        source = self.archive('source.tar.gz', {'source/LICENSE': self.notice, 'source/build.sh': b'build recipe'})
        self.lock = {'schema_version': 1, 'component': 'DXVK', 'version': 'fixture',
                     'binary_archive': binary, 'source_archive': source,
                     'selected_files': [{'archive_path': 'binary/x64/d3d9.dll',
                                         'bundle_path': 'payload/dxvk/x64/d3d9.dll', 'sha256': sha(self.dll)}],
                     'notice_files': [{'path': 'dxvk/LICENSE', 'archive': 'source.tar.gz',
                                       'member': 'source/LICENSE', 'sha256': sha(self.notice)}]}

    def archive(self, name, files):
        path = self.cache / name
        with tarfile.open(path, 'w:gz') as tar:
            for member_name, data in files.items():
                info = tarfile.TarInfo(member_name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        return {'filename': name, 'sha256': sha(path.read_bytes()), 'url': 'https://example.org/' + name}

    def test_verified_offline_preparation_selects_only_x64_and_copies_notices(self):
        result = dxvk.prepare(self.lock, self.cache, self.output, offline=True)
        self.assertEqual((self.output / 'x64/d3d9.dll').read_bytes(), self.dll)
        self.assertEqual((self.output / 'notices/dxvk/LICENSE').read_bytes(), self.notice)
        self.assertFalse(result['executed'])
        self.assertEqual(result['source_archives_verified'], 1)
        self.assertFalse((self.output / 'x32').exists())

    def test_corrupt_cached_archive_fails_before_output(self):
        (self.cache / 'binary.tar.gz').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            dxvk.prepare(self.lock, self.cache, self.output, offline=True)
        self.assertFalse(self.output.exists())

    def test_wrong_machine_missing_notice_and_unsafe_path_are_rejected(self):
        wrong = bytearray(self.dll)
        struct.pack_into('<H', wrong, 68, 0x14c)
        self.lock['binary_archive'] = self.archive('wrong.tar.gz', {'binary/x64/d3d9.dll': bytes(wrong)})
        self.lock['selected_files'][0]['sha256'] = sha(bytes(wrong))
        with self.assertRaisesRegex(ValueError, 'pinned x64 DLL'):
            dxvk.prepare(self.lock, self.cache, self.output, offline=True)
        self.lock['binary_archive'] = self.archive('fixed.tar.gz', {'binary/x64/d3d9.dll': self.dll})
        self.lock['selected_files'][0]['sha256'] = sha(self.dll)
        self.lock['notice_files'][0]['sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'notice checksum'):
            dxvk.prepare(self.lock, self.cache, self.output, offline=True)
        self.lock['notice_files'][0]['path'] = '../escape'
        with self.assertRaisesRegex(ValueError, 'Unsafe relative path'):
            dxvk.prepare(self.lock, self.cache, self.output, offline=True)
        self.assertFalse(self.output.exists())

    def test_existing_output_is_never_overwritten(self):
        self.output.mkdir()
        keep = self.output / 'keep'
        keep.write_bytes(b'keep this')
        with self.assertRaises(FileExistsError):
            dxvk.prepare(self.lock, self.cache, self.output, offline=True)
        self.assertEqual(keep.read_bytes(), b'keep this')


if __name__ == '__main__':
    unittest.main()
