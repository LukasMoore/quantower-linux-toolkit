"""Synthetic, locally generated SFNT fixtures; no application fonts or glyphs."""
import contextlib
import fcntl
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('isolate_icon_fonts', ROOT / 'tools/isolate-icon-fonts.py')
fonts = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fonts)


def sum_words(data):
    data += bytes(-len(data) % 4)
    return sum(int.from_bytes(data[index:index + 4], 'big') for index in range(0, len(data), 4)) & 0xffffffff


def fixture_names():
    entries, storage = [], bytearray()
    for platform, encoding, language, codec in ((1, 0, 0, 'mac_roman'), (3, 1, 1033, 'utf-16-be')):
        for name_id, text in ((0, 'Generated test data only'), (1, 'Shared Synthetic'),
                              (2, 'Regular'), (3, 'Synthetic-1'), (4, 'Shared Synthetic'),
                              (6, 'SharedSynthetic'), (13, 'Generated locally for tests'),
                              (16, 'Shared Synthetic'), (21, 'Shared Synthetic')):
            raw = text.encode(codec)
            entries.append((platform, encoding, language, name_id, len(raw), len(storage)))
            storage.extend(raw)
    return struct.pack('>3H', 0, len(entries), 6 + 12 * len(entries)) + b''.join(struct.pack('>6H', *entry) for entry in entries) + storage


def synthetic_font(extra=None):
    # One empty .notdef glyph. These fields describe the format, not artwork.
    head = bytearray(54)
    struct.pack_into('>II', head, 0, 0x00010000, 0x00010000)
    struct.pack_into('>I', head, 12, 0x5f0f3cf5)
    struct.pack_into('>H', head, 18, 1000)
    maxp = struct.pack('>IH13H', 0x00010000, 1, *([0] * 13))
    hhea = bytearray(36)
    struct.pack_into('>I', hhea, 0, 0x00010000)
    struct.pack_into('>H', hhea, 34, 1)
    cmap_format4 = struct.pack('>12H', 4, 24, 0, 2, 2, 0, 0, 0xffff, 0, 0xffff, 1, 0)
    tables = {b'head': bytes(head), b'maxp': maxp, b'hhea': bytes(hhea),
              b'hmtx': struct.pack('>Hh', 1000, 0), b'loca': bytes(4), b'glyf': b'',
              b'cmap': struct.pack('>HHHHI', 0, 1, 3, 1, 12) + cmap_format4,
              b'name': fixture_names()}
    if extra:
        tables.update(extra)
    order = sorted(tables)
    count = len(order)
    power = 1 << (count.bit_length() - 1)
    result = bytearray(struct.pack('>I4H', 0x00010000, count, power * 16,
                                   power.bit_length() - 1, count * 16 - power * 16) + bytes(count * 16))
    head_offset = None
    for index, tag in enumerate(order):
        result.extend(bytes(-len(result) % 4))
        position = len(result)
        payload = tables[tag]
        if tag == b'head':
            head_offset = position
        struct.pack_into('>4s3I', result, 12 + 16 * index, tag, sum_words(payload), position, len(payload))
        result.extend(payload)
    result.extend(bytes(-len(result) % 4))
    struct.pack_into('>I', result, head_offset + 8, (0xb1b0afba - sum_words(result)) & 0xffffffff)
    return bytes(result)


def source_table(data, tag):
    count = struct.unpack_from('>H', data, 4)[0]
    for index in range(count):
        name, _, offset, length = struct.unpack_from('>4s3I', data, 12 + index * 16)
        if name == tag:
            return data[offset:offset + length]
    raise AssertionError('missing table')


class FontFormatTests(unittest.TestCase):
    def test_only_identity_names_and_checksum_change(self):
        original = synthetic_font()
        after = fonts.isolate_identity(original, 'Toolkit UI Icons', 'ToolkitUIIcons')
        order, tables = fonts.read_font(original)
        new_order, new_tables = fonts.read_font(after)
        self.assertEqual(order, new_order)
        self.assertEqual(sum_words(after), 0xb1b0afba)
        for tag in order:
            if tag == b'head':
                self.assertEqual(tables[tag][:8] + bytes(4) + tables[tag][12:],
                                 new_tables[tag][:8] + bytes(4) + new_tables[tag][12:])
            elif tag != b'name':
                self.assertEqual(tables[tag], new_tables[tag])
        before_names = fonts.name_records(tables[b'name'])
        after_names = fonts.name_records(new_tables[b'name'])
        self.assertEqual(len(before_names), len(after_names))
        for (identity, before), (new_identity, new) in zip(before_names, after_names):
            self.assertEqual(identity, new_identity)
            if identity[3] not in fonts.IDENTITY_IDS:
                self.assertEqual(before, new)
        self.assertEqual(fonts.isolate_identity(after, 'Toolkit UI Icons', 'ToolkitUIIcons'), after)

    def test_roles_have_distinct_encoded_identities(self):
        results = [fonts.isolate_identity(synthetic_font(), family, postscript) for _, _, family, postscript in fonts.SPECS]
        self.assertEqual(len(set(results)), 3)
        for (_, _, family, postscript), result in zip(fonts.SPECS, results):
            for (platform, encoding, _, number), raw in fonts.name_records(source_table(result, b'name')):
                text = raw.decode(fonts.name_codec(platform, encoding))
                if number in (1, 4, 16, 21):
                    self.assertEqual(text, family)
                elif number == 6:
                    self.assertEqual(text, postscript)

    def test_unsupported_and_corrupt_fonts_are_rejected(self):
        original = synthetic_font()
        corrupt = bytearray(original)
        corrupt[-1] ^= 1
        truncated = original[:40]
        bad_bounds = bytearray(original)
        struct.pack_into('>I', bad_bounds, 20, len(original) + 4)
        overlap = bytearray(original)
        struct.pack_into('>I', overlap, 20, 12)
        for data in (b'OTTO' + original[4:], b'ttcf' + original[4:], bytes(corrupt), truncated,
                     bytes(bad_bounds), bytes(overlap), synthetic_font({b'DSIG': b''}), synthetic_font({b'fvar': bytes(16)}),
                     synthetic_font({b'loca': struct.pack('>HH', 0, 2)})):
            with self.subTest(length=len(data), prefix=data[:4]), self.assertRaises(ValueError):
                fonts.isolate_identity(data, 'Toolkit UI Icons', 'ToolkitUIIcons')

    def test_bad_name_bounds_and_unsupported_identity_encoding_are_rejected(self):
        raw = bytearray(fixture_names())
        struct.pack_into('>H', raw, 6 + 8, 65535)
        with self.assertRaises(ValueError):
            fonts.name_records(raw)
        raw = bytearray(fixture_names())
        # The first platform's family record is the second record.
        struct.pack_into('>H', raw, 6 + 12, 2)
        bad = synthetic_font({b'name': bytes(raw)})
        with self.assertRaisesRegex(ValueError, 'encoding'):
            fonts.isolate_identity(bad, 'Toolkit UI Icons', 'ToolkitUIIcons')


class TransactionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.version = '1.2.3'
        (self.base / 'application-version.json').write_text(json.dumps({'schema_version': 1, 'application_version': self.version}))
        (self.base / 'runtime-profile.json').write_text(json.dumps({'version': 1, 'mode': 'compatible'}))
        root = self.base / 'prefix/drive_c/AMPQuantowerInstalled/TradingPlatform/v1.2.3/bin/Themes/Default/fonts'
        self.paths = []
        for _, relative, _, _ in fonts.SPECS:
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(synthetic_font())
            path.chmod(0o640)
            self.paths.append(path)
        components = self.base / 'runtime-components'
        components.mkdir()
        self.manifest = components / 'manifest.json'
        self.manifest.write_text(json.dumps({'required_files': {str(path.relative_to(self.base)): hashlib.sha256(path.read_bytes()).hexdigest() for path in self.paths}}))
        patch = mock.patch.object(fonts.profile_manager, 'no_running_prefix')
        self.guard = patch.start()
        self.addCleanup(patch.stop)

    def states(self):
        return {path: (path.read_bytes(), path.stat().st_mode & 0o777) for path in [*self.paths, self.manifest]}

    def test_default_preview_has_no_filesystem_writes(self):
        before = self.states()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(fonts.main(['--base', str(self.base)]), 0)
        self.assertEqual(self.states(), before)
        self.guard.assert_not_called()
        self.assertFalse((self.base / 'logs').exists())

    def test_apply_pins_hashes_and_private_originals_then_is_idempotent(self):
        before = self.states()
        result = fonts.apply(self.base)
        self.assertEqual(result['mode'], 'applied')
        backup = Path(result['backup_directory'])
        self.assertTrue(backup.is_relative_to(self.base / 'logs/font-backups'))
        self.assertEqual(backup.stat().st_mode & 0o777, 0o700)
        manifest = json.loads(self.manifest.read_text())
        for (role, _, family, _), path in zip(fonts.SPECS, self.paths):
            self.assertEqual((backup / (role + '.ttf')).read_bytes(), before[path][0])
            self.assertEqual((backup / (role + '.ttf')).stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.stat().st_mode & 0o777, 0o640)
            self.assertEqual(manifest['required_files'][str(path.relative_to(self.base))], hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertEqual(manifest['local_font_identities']['families'][role], family)
        applied = self.states()
        second = fonts.apply(self.base)
        self.assertEqual(second['mode'], 'unchanged')
        self.assertEqual(self.states(), applied)
        self.assertEqual(len(list((self.base / 'logs/font-backups').iterdir())), 1)

    def test_active_prefix_refusal_precedes_even_lock_creation(self):
        self.guard.side_effect = RuntimeError('active synthetic prefix')
        before = self.states()
        with self.assertRaisesRegex(RuntimeError, 'active synthetic'):
            fonts.apply(self.base)
        self.assertEqual(self.states(), before)
        self.assertFalse((self.base / 'logs').exists())

    def test_owned_launch_lock_blocks_apply(self):
        (self.base / 'logs').mkdir(mode=0o700)
        descriptor = os.open(self.base / 'logs/launch.lock', os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(descriptor, 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            before = self.states()
            with self.assertRaisesRegex(RuntimeError, 'launcher owns'):
                fonts.apply(self.base)
            self.assertEqual(self.states(), before)
            self.assertFalse((self.base / 'logs/font-backups').exists())

    def test_atomic_failure_rolls_back_all_font_and_manifest_bytes(self):
        original_atomic = fonts.profile_manager.atomic_bytes
        for target in (self.paths[1], self.manifest):
            with self.subTest(target=target):
                before = self.states()
                failed = False
                def atomic(path, data, mode=0o600):
                    nonlocal failed
                    if path == target and not failed:
                        failed = True
                        raise OSError('synthetic transaction failure')
                    return original_atomic(path, data, mode)
                with mock.patch.object(fonts.profile_manager, 'atomic_bytes', side_effect=atomic), self.assertRaisesRegex(RuntimeError, 'were restored'):
                    fonts.apply(self.base)
                self.assertTrue(failed)
                self.assertEqual(self.states(), before)
                self.assertTrue(all(json.loads(path.read_text())['status'] == 'rolled_back' for path in (self.base / 'logs/font-backups').glob('*/transaction.json')))

    def test_stale_source_refusal_does_not_overwrite_an_external_change(self):
        original_atomic = fonts.profile_manager.atomic_bytes
        target = self.paths[1]
        external = b'externally edited bytes'
        def atomic(path, data, mode=0o600):
            result = original_atomic(path, data, mode)
            if path.name == 'transaction.json':
                target.write_bytes(external)
            return result
        with mock.patch.object(fonts.profile_manager, 'atomic_bytes', side_effect=atomic), self.assertRaisesRegex(RuntimeError, 'changed while preparing'):
            fonts.apply(self.base)
        self.assertEqual(target.read_bytes(), external)
        self.assertEqual(self.paths[0].read_bytes(), synthetic_font())

    def test_symlinked_input_and_backup_directories_are_refused(self):
        original = self.paths[0]
        alternate = self.base / 'other-font.ttf'
        original.rename(alternate)
        original.symlink_to(alternate)
        with self.assertRaisesRegex(RuntimeError, 'Redirected'):
            fonts.plan(self.base)
        original.unlink()
        alternate.rename(original)
        external = self.base / 'external-logs'
        external.mkdir()
        (self.base / 'logs').symlink_to(external, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, 'redirected'):
            fonts.apply(self.base)
        self.assertFalse((external / 'launch.lock').exists())


if __name__ == '__main__':
    unittest.main()
