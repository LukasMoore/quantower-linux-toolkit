#!/usr/bin/env python3
"""Copy a build stage into a new lean runtime prefix; never edit its build output."""
import argparse
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess


def prepare(source, target, strip=False):
    source = Path(source).resolve(strict=True)
    target = Path(target).resolve()
    if target.exists() or target.is_relative_to(source) or source.is_relative_to(target):
        raise ValueError('Runtime output must be a new directory separate from the build stage.')
    for file in source.rglob('*'):
        if file.is_symlink() and not file.resolve(strict=True).is_relative_to(source):
            raise ValueError('Build stage contains an external symlink.')
    shutil.copytree(source, target, symlinks=True,
                    ignore=shutil.ignore_patterns('include', 'pkgconfig', 'man', '*.a', '*.la'))
    for file in target.rglob('*'):
        if file.is_symlink() and Path(os.readlink(file)).is_absolute():
            original = source / file.relative_to(target)
            relocated = target / original.resolve(strict=True).relative_to(source)
            file.unlink()
            file.symlink_to(os.path.relpath(relocated, file.parent))
    stripped = {'elf': 0, 'pe': 0}
    if strip:
        for file in sorted(target.rglob('*')):
            if file.is_symlink() or not file.is_file():
                continue
            with file.open('rb') as stream:
                magic = stream.read(4)
                pe = False
                if magic[:2] == b'MZ' and file.stat().st_size >= 64:
                    stream.seek(60)
                    offset = struct.unpack('<I', stream.read(4))[0]
                    if offset <= file.stat().st_size - 4:
                        stream.seek(offset)
                        pe = stream.read(4) == b'PE\0\0'
            if magic == b'\x7fELF':
                subprocess.run(['strip', '--strip-debug', str(file)], check=True)
                stripped['elf'] += 1
            elif pe:
                subprocess.run(['x86_64-w64-mingw32-strip', '--strip-debug', str(file)], check=True)
                stripped['pe'] += 1
    return {'pruned': ['include/', 'pkgconfig/', 'man/', '*.a', '*.la'],
            'strip_debug': strip, 'stripped': stripped,
            'retained': 'Runtime ELF/PE modules, exports, fonts, locale data and runtime helpers.'}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', required=True, type=Path)
    p.add_argument('--target', required=True, type=Path)
    p.add_argument('--strip-debug', action='store_true')
    args = p.parse_args()
    print(json.dumps(prepare(args.source, args.target, args.strip_debug), indent=2))


if __name__ == '__main__':
    main()
