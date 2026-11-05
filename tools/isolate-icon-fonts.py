#!/usr/bin/env python3
"""Preview or isolate local icon-font identities; no font data is distributed.

Use only on installed font files you are permitted to modify. The default is a
read-only preview. Apply requires an inactive dedicated prefix and launch lock.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import struct
import sys
import uuid
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import application_runtime
import profile_manager

MAX_FONT_BYTES = 32 * 1024 * 1024
IDENTITY_IDS = {1, 3, 4, 6, 16, 21}
SPECS = (
    ('ui', 'icomoon.ttf', 'Toolkit UI Icons', 'ToolkitUIIcons'),
    ('chart', 'chart/icomoon.ttf', 'Toolkit Chart Icons', 'ToolkitChartIcons'),
    ('drawing', 'drawing/icomoon.ttf', 'Toolkit Drawing Icons', 'ToolkitDrawingIcons'),
)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def checksum(data):
    padded = data + b'\0' * (-len(data) % 4)
    return sum(struct.unpack('>' + str(len(padded) // 4) + 'I', padded)) & 0xffffffff


def read_font(data):
    """Accept a bounded, static TrueType SFNT with intact table checksums."""
    if len(data) < 12 or len(data) > MAX_FONT_BYTES or data[:4] != b'\0\1\0\0':
        raise ValueError('Only bounded TrueType SFNT fonts are supported; CFF and font collections are unsupported.')
    count, search, selector, shift = struct.unpack_from('>4H', data, 4)
    if not 1 <= count <= 256 or len(data) < 12 + 16 * count:
        raise ValueError('Invalid SFNT table directory.')
    power = 1 << (count.bit_length() - 1)
    if (search, selector, shift) != (16 * power, power.bit_length() - 1, 16 * (count - power)):
        raise ValueError('Invalid SFNT directory search fields.')
    order, tables, ranges = [], {}, []
    for index in range(count):
        tag, expected, offset, length = struct.unpack_from('>4s3I', data, 12 + 16 * index)
        if (tag in tables or any(byte < 32 or byte > 126 for byte in tag) or
                offset % 4 or offset < 12 + 16 * count or offset + length > len(data)):
            raise ValueError('Invalid, overlapping or duplicate SFNT table.')
        if length:
            ranges.append((offset, offset + length))
        value = data[offset:offset + length]
        if tag == b'head' and len(value) < 54:
            raise ValueError('Invalid TrueType head table.')
        checked = value[:8] + bytes(4) + value[12:] if tag == b'head' else value
        if checksum(checked) != expected:
            raise ValueError('Invalid SFNT table checksum.')
        order.append(tag)
        tables[tag] = value
    ranges.sort()
    if any(left[1] > right[0] for left, right in zip(ranges, ranges[1:])):
        raise ValueError('Overlapping SFNT table ranges.')
    if order != sorted(order):
        raise ValueError('The SFNT directory must be sorted by table tag.')
    required = {b'head', b'name', b'maxp', b'cmap', b'glyf', b'loca', b'hhea', b'hmtx'}
    if not required <= set(tables) or b'DSIG' in tables or b'fvar' in tables:
        raise ValueError('Only unsigned static TrueType outline fonts with the required tables are supported.')
    head = tables[b'head']
    if head[:4] != b'\0\1\0\0' or struct.unpack_from('>I', head, 12)[0] != 0x5f0f3cf5:
        raise ValueError('Invalid TrueType head magic number.')
    if len(tables[b'maxp']) < 32 or tables[b'maxp'][:4] != b'\0\1\0\0':
        raise ValueError('Unsupported TrueType maxp table.')
    glyphs = struct.unpack_from('>H', tables[b'maxp'], 4)[0]
    locations = struct.unpack_from('>h', head, 50)[0]
    if not glyphs or locations not in (0, 1) or len(tables[b'loca']) < (glyphs + 1) * (2 if locations == 0 else 4):
        raise ValueError('Invalid TrueType glyph-location table.')
    offsets = struct.unpack_from('>' + ('H' if locations == 0 else 'I') * (glyphs + 1), tables[b'loca'])
    offsets = [offset * 2 for offset in offsets] if locations == 0 else offsets
    if offsets != sorted(offsets) or offsets[-1] > len(tables[b'glyf']):
        raise ValueError('Invalid TrueType glyph-location bounds.')
    if len(tables[b'hhea']) < 36:
        raise ValueError('Invalid TrueType horizontal-header table.')
    metrics = struct.unpack_from('>H', tables[b'hhea'], 34)[0]
    if not 1 <= metrics <= glyphs or len(tables[b'hmtx']) < 4 * metrics + 2 * (glyphs - metrics):
        raise ValueError('Invalid TrueType horizontal metrics table.')
    if checksum(data) != 0xb1b0afba:
        raise ValueError('Invalid whole-font checksum.')
    return order, tables


def name_records(data):
    if len(data) < 6:
        raise ValueError('Invalid name table.')
    form, count, storage = struct.unpack_from('>3H', data)
    if form != 0 or storage < 6 + 12 * count or storage > len(data):
        raise ValueError('Only bounded name-table format 0 is supported.')
    records, seen = [], set()
    for index in range(count):
        platform, encoding, language, number, length, offset = struct.unpack_from('>6H', data, 6 + 12 * index)
        identity = platform, encoding, language, number
        if identity in seen or storage + offset + length > len(data):
            raise ValueError('Invalid or duplicate font name record.')
        seen.add(identity)
        records.append((identity, data[storage + offset:storage + offset + length]))
    if not {1, 3, 4, 6} <= {identity[3] for identity, value in records}:
        raise ValueError('The font lacks required family, full, unique or PostScript identities.')
    return records


def name_codec(platform, encoding):
    if platform in (0, 3):
        return 'utf-16-be'
    if platform == 1 and encoding == 0:
        return 'mac_roman'
    raise ValueError('Unsupported encoding of a font identity record.')


def renamed_names(data, family, postscript):
    records = name_records(data)
    entries, strings, offsets = bytearray(), bytearray(), {}
    for identity, raw in records:
        platform, encoding, language, number = identity
        if number in IDENTITY_IDS:
            codec = name_codec(platform, encoding)
            raw.decode(codec)  # Reject malformed source strings rather than guessing.
            text = postscript if number == 6 else postscript + '-Identity1' if number == 3 else family
            raw = text.encode(codec)
        if raw not in offsets:
            offsets[raw] = len(strings)
            strings.extend(raw)
        if len(raw) > 65535 or offsets[raw] > 65535:
            raise ValueError('Rewritten name table exceeds SFNT record limits.')
        entries.extend(struct.pack('>6H', *identity, len(raw), offsets[raw]))
    start = 6 + len(entries)
    if start > 65535:
        raise ValueError('Rewritten name table has too many records.')
    return struct.pack('>3H', 0, len(records), start) + entries + strings


def assemble(header, order, tables):
    result = bytearray(header[:12] + bytes(16 * len(order)))
    head_offset = None
    for index, tag in enumerate(order):
        result.extend(bytes(-len(result) % 4))
        offset = len(result)
        value = tables[tag]
        if tag == b'head':
            head_offset = offset
            value = value[:8] + bytes(4) + value[12:]
        struct.pack_into('>4s3I', result, 12 + 16 * index, tag, checksum(value), offset, len(value))
        result.extend(value)
    result.extend(bytes(-len(result) % 4))
    struct.pack_into('>I', result, head_offset + 8, (0xb1b0afba - checksum(result)) & 0xffffffff)
    return bytes(result)


def isolate_identity(data, family, postscript):
    if (not family.isascii() or not family or len(family) > 31 or
            not postscript.isascii() or len(postscript) > 63 or not postscript.isalnum()):
        raise ValueError('Unsupported target identity.')
    order, tables = read_font(data)
    changed = dict(tables)
    changed[b'name'] = renamed_names(tables[b'name'], family, postscript)
    # A second invocation leaves an already canonical font byte-identical.
    if changed[b'name'] == tables[b'name']:
        return data
    result = assemble(data, order, changed)
    new_order, output = read_font(result)
    if new_order != order:
        raise RuntimeError('Font table ordering changed.')
    for tag in order:
        before, after = tables[tag], output[tag]
        if tag == b'head':
            before, after = before[:8] + bytes(4) + before[12:], after[:8] + bytes(4) + after[12:]
        if tag != b'name' and before != after:
            raise RuntimeError('Non-identity font table bytes changed.')
    old_records, new_records = name_records(tables[b'name']), name_records(output[b'name'])
    if len(old_records) != len(new_records):
        raise RuntimeError('Font name-record count changed.')
    for (identity, before), (new_identity, after) in zip(old_records, new_records):
        if identity != new_identity or (identity[3] not in IDENTITY_IDS and before != after):
            raise RuntimeError('A non-identity font name record changed.')
    return result


def regular_local_file(path, base):
    if not path.resolve().is_relative_to(base) or not path.is_file():
        raise RuntimeError('A required local file is missing or outside this installation.')
    for parent in (path, *path.parents):
        if parent == base:
            break
        if parent.is_symlink():
            raise RuntimeError('Redirected local font or metadata path is unsupported.')
    return path


def plan(base):
    base = Path(base).resolve()
    application = application_runtime.load(base)
    fonts = application.directory / 'bin/Themes/Default/fonts'
    changes = []
    for role, relative, family, postscript in SPECS:
        path = regular_local_file(fonts / relative, base)
        if path.stat().st_size > MAX_FONT_BYTES:
            raise ValueError('The local font exceeds the supported size limit.')
        before = path.read_bytes()
        after = isolate_identity(before, family, postscript)
        changes.append({'role': role, 'path': path, 'before': before, 'after': after,
                        'mode': stat.S_IMODE(path.stat().st_mode), 'family': family,
                        'old_sha256': digest(before), 'new_sha256': digest(after)})
    return application.version, changes


def report(version, changes, *, mode, backup=None):
    return {'schema_version': 1, 'mode': mode, 'application_version': version,
            'assets': [{'role': item['role'], 'family': item['family'],
                        'changed': item['before'] != item['after'],
                        'old_sha256': item['old_sha256'], 'new_sha256': item['new_sha256']} for item in changes],
            'backup_directory': str(backup) if backup else None,
            'scope': 'Local identity names and required font checksum adjustment only; glyph, mapping and metric table bytes preserved.'}


def private_directory(path, base):
    if not path.resolve().is_relative_to(base):
        raise RuntimeError('Private backup path escapes the installation.')
    for parent in (path, *path.parents):
        if parent == base:
            break
        if parent.is_symlink():
            raise RuntimeError('Private backup directory is redirected.')
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.stat().st_uid != os.getuid():
        raise RuntimeError('Private backup directory ownership differs.')
    path.chmod(0o700)


def apply(base):
    base = Path(base).resolve()
    prefix = base / 'prefix'
    # Refuse an active prefix before even creating a lock directory.
    profile_manager.no_running_prefix(prefix)
    logs = base / 'logs'
    private_directory(logs, base)
    descriptor = os.open(logs / 'launch.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'a') as lock:
        identity = os.fstat(lock.fileno())
        if (not stat.S_ISREG(identity.st_mode) or identity.st_uid != os.getuid() or
                identity.st_mode & 0o077):
            raise RuntimeError('The launch lock ownership or permissions are unsafe.')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError('The launcher owns this installation; close it before applying font changes.') from error
        profile_manager.no_running_prefix(prefix)
        profile_manager.load(base)
        manifest_path = regular_local_file(base / 'runtime-components/manifest.json', base)
        manifest_before = manifest_path.read_bytes()
        manifest_mode = stat.S_IMODE(manifest_path.stat().st_mode)
        manifest = json.loads(manifest_before)
        version, changes = plan(base)
        pins = {str(item['path'].relative_to(base)): item['new_sha256'] for item in changes}
        if all(item['before'] == item['after'] for item in changes) and all(manifest['required_files'].get(path) == value for path, value in pins.items()):
            return report(version, changes, mode='unchanged')
        backups = logs / 'font-backups'
        private_directory(backups, base)
        transaction = backups / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + uuid.uuid4().hex[:8])
        transaction.mkdir(mode=0o700)
        for item in changes:
            profile_manager.atomic_bytes(transaction / (item['role'] + '.ttf'), item['before'])
        profile_manager.atomic_bytes(transaction / 'manifest-before.json', manifest_before)
        record = {'schema_version': 1, 'status': 'prepared', 'application_version': version,
                  'assets': [{'role': item['role'], 'path': str(item['path'].relative_to(base)),
                              'mode': item['mode'], 'old_sha256': item['old_sha256'],
                              'new_sha256': item['new_sha256'], 'backup': item['role'] + '.ttf'} for item in changes]}
        profile_manager.atomic_bytes(transaction / 'transaction.json', (json.dumps(record, indent=2) + '\n').encode())
        manifest['required_files'].update(pins)
        manifest['local_font_identities'] = {'schema_version': 1, 'application_version': version,
                                             'families': {item['role']: item['family'] for item in changes}}
        # Preflight refusals must not enter rollback: an independent editor may
        # have changed a source, and no live file has been written by us yet.
        profile_manager.no_running_prefix(prefix)
        for item in changes:
            if digest(item['path'].read_bytes()) != item['old_sha256']:
                raise RuntimeError('A local font changed while preparing the transaction.')
        try:
            for item in changes:
                profile_manager.atomic_bytes(item['path'], item['after'], item['mode'])
            profile_manager.atomic_bytes(manifest_path, (json.dumps(manifest, indent=2) + '\n').encode(), manifest_mode)
            profile_manager.load(base)
            record['status'] = 'complete'
            profile_manager.atomic_bytes(transaction / 'transaction.json', (json.dumps(record, indent=2) + '\n').encode())
        except BaseException as error:
            rollback_errors = []
            for path, data, mode in [(item['path'], item['before'], item['mode']) for item in changes] + [(manifest_path, manifest_before, manifest_mode)]:
                try:
                    profile_manager.atomic_bytes(path, data, mode)
                except OSError as rollback_error:
                    rollback_errors.append(type(rollback_error).__name__)
            record['status'] = 'rollback_incomplete' if rollback_errors else 'rolled_back'
            try:
                profile_manager.atomic_bytes(transaction / 'transaction.json', (json.dumps(record, indent=2) + '\n').encode())
            except OSError:
                pass
            if rollback_errors:
                raise RuntimeError('Font update failed and rollback is incomplete; original files remain in ' + str(transaction)) from error
            raise RuntimeError('Font update failed; original font and manifest bytes were restored. Backup: ' + str(transaction)) from error
        return report(version, changes, mode='applied', backup=transaction)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', required=True, type=Path, help='Dedicated installation containing the selected locally licensed app.')
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--dry-run', action='store_true', help='Read-only preview (the default).')
    action.add_argument('--apply', action='store_true', help='Apply while the entire dedicated prefix is stopped; use only fonts you may modify.')
    args = parser.parse_args(argv)
    try:
        if args.apply:
            result = apply(args.base)
        else:
            version, changes = plan(args.base)
            result = report(version, changes, mode='dry_run')
        print(json.dumps(result, indent=2))
        return 0
    except (OSError, ValueError, RuntimeError, TypeError, KeyError, struct.error) as error:
        print('Font identity tool refused the operation: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
