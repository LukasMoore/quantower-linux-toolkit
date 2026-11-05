#!/usr/bin/env python3
"""Prepare the pinned official x64 DXVK D3D9 DLL and its source/notice inputs.

This maintainer tool never starts Wine, executes a DLL or accesses an app prefix.
The verified cache contains source archives for the separate source companion.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import struct
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('runtime_source_builder', ROOT / 'tools/build-runtime.py')
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


def member_bytes(archive, name, maximum):
    builder.safe_relative(name)
    with tarfile.open(archive, 'r:*') as source:
        found = [m for m in source.getmembers() if m.name == name]
        if len(found) != 1 or not found[0].isfile() or found[0].size > maximum:
            raise ValueError('Required DXVK archive member is missing, duplicated or unsafe.')
        with source.extractfile(found[0]) as stream:
            return stream.read()


def x64_dll(data):
    if len(data) < 0x40 or data[:2] != b'MZ':
        return False
    offset = struct.unpack_from('<I', data, 0x3c)[0]
    if offset + 26 > len(data) or data[offset:offset + 4] != b'PE\0\0':
        return False
    machine = struct.unpack_from('<H', data, offset + 4)[0]
    characteristics = struct.unpack_from('<H', data, offset + 22)[0]
    optional_magic = struct.unpack_from('<H', data, offset + 24)[0]
    return machine == 0x8664 and bool(characteristics & 0x2000) and optional_magic == 0x20b


def prepare(lock, cache, output, offline=False):
    if not isinstance(lock, dict) or lock.get('schema_version') != 1 or lock.get('component') != 'DXVK':
        raise ValueError('Unsupported DXVK lock schema.')
    output = Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise FileExistsError('DXVK output must be new; existing files are never overwritten.')
    specs = [lock['binary_archive'], lock['source_archive'], *lock.get('extra_sources', [])]
    archives = {}
    for spec in specs:
        if spec['filename'] in archives:
            raise ValueError('DXVK archive filenames must be unique.')
        archives[spec['filename']] = builder.fetch_archive(spec, Path(cache), offline)
    selected = lock.get('selected_files', [])
    if len(selected) != 1 or selected[0].get('bundle_path') != 'payload/dxvk/x64/d3d9.dll':
        raise ValueError('The reviewed runtime selects only the x64 D3D9 DLL.')
    file = selected[0]
    data = member_bytes(archives[lock['binary_archive']['filename']], file['archive_path'], 64 * 1024 ** 2)
    if hashlib.sha256(data).hexdigest() != file['sha256'] or not x64_dll(data):
        raise ValueError('DXVK D3D9 bytes do not match the pinned x64 DLL.')
    notice_data = []
    for notice in lock.get('notice_files', []):
        builder.safe_relative(notice['path'])
        if notice['archive'] not in archives:
            raise ValueError('DXVK notice refers to an unpinned source archive.')
        value = member_bytes(archives[notice['archive']], notice['member'], 2 * 1024 ** 2)
        if hashlib.sha256(value).hexdigest() != notice['sha256']:
            raise ValueError('DXVK notice checksum mismatch.')
        notice_data.append((notice['path'], value))
    if not notice_data or not any(name == 'dxvk/LICENSE' for name, _ in notice_data):
        raise ValueError('The full upstream DXVK notice is required.')
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix='.dxvk-', dir=output.parent))
    try:
        (temporary / 'x64').mkdir()
        (temporary / 'x64/d3d9.dll').write_bytes(data)
        for name, value in notice_data:
            target = temporary / 'notices' / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(value)
            if name == 'dxvk/LICENSE':
                (temporary / 'LICENSE').write_bytes(value)
        (temporary / 'provenance.json').write_text(json.dumps(lock, indent=2) + '\n')
        output.mkdir(mode=0o700)
        try:
            os.rename(temporary, output)
        except BaseException:
            output.rmdir()
            raise
        temporary = None
    finally:
        if temporary is not None:
            shutil.rmtree(temporary)
    return {'component': 'DXVK', 'version': lock['version'], 'dll_sha256': file['sha256'],
            'source_archives_verified': len(specs) - 1, 'notice_files': len(notice_data), 'executed': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--offline', action='store_true')
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        result = prepare(json.loads((ROOT / 'packaging/dxvk-lock.json').read_text()), args.cache, args.output, args.offline)
    except (OSError, ValueError, KeyError, TypeError, tarfile.TarError) as error:
        parser.exit(1, 'DXVK preparation stopped: ' + str(error) + '\n')
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
