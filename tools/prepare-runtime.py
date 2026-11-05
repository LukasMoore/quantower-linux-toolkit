#!/usr/bin/env python3
"""Copy a build stage into a new lean runtime prefix; never edit its build output."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import struct
import subprocess


MAX_FONT_BYTES = 32 * 1024 * 1024
MAX_FONT_TOTAL = 128 * 1024 * 1024


def no_symlink_ancestors(path):
    for ancestor in (path, *path.parents):
        if ancestor.is_symlink():
            raise ValueError('Font inputs and destination parents must not contain symlinks.')


def add_fonts(wine_home, files):
    """Add unchanged fonts to a build tree, refusing installed application state."""
    home = Path(wine_home).expanduser().absolute()
    no_symlink_ancestors(home)
    home = home.resolve(strict=True)
    if not home.is_dir():
        raise ValueError('Font destination must be a prepared Wine build directory.')
    for ancestor in (home, *home.parents):
        if ((ancestor / 'user.reg').exists() or (ancestor / 'user.reg').is_symlink() or
                (ancestor / 'prefix/user.reg').exists() or (ancestor / 'prefix/user.reg').is_symlink() or
                (ancestor / 'runtime-components/manifest.json').exists() or
                (ancestor / 'runtime-components/manifest.json').is_symlink() or
                (ancestor / 'application-version.json').exists()):
            raise ValueError('Fonts may only be added to a build tree, never an installed runtime or Wine prefix.')
    directory = home / 'share/wine/fonts'
    no_symlink_ancestors(directory)
    for ancestor in (directory, directory.parent, directory.parent.parent):
        if ancestor.exists() and not ancestor.is_dir():
            raise ValueError('Font destination ancestors must be directories.')
    pending, names, total = [], set(), 0
    existing = {entry.name.casefold() for entry in directory.iterdir()} if directory.exists() else set()
    for value in files:
        source = Path(value).expanduser().absolute()
        no_symlink_ancestors(source)
        source = source.resolve(strict=True)
        if source.suffix.lower() != '.ttf' or source.name.casefold() in names:
            raise ValueError('Each added TrueType font must have a unique .ttf filename.')
        if source.name.casefold() in existing:
            raise FileExistsError('A font destination already exists; no fonts were added: ' + source.name)
        descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or not 12 <= info.st_size <= MAX_FONT_BYTES:
                raise ValueError('A TrueType font must be a regular file between 12 bytes and 32 MiB.')
            data = stream.read(MAX_FONT_BYTES + 1)
            if len(data) != info.st_size or data[:4] not in (b'\0\1\0\0', b'true'):
                raise ValueError('Font input is not a bounded TrueType sfnt file.')
        total += len(data)
        if total > MAX_FONT_TOTAL:
            raise ValueError('The combined added font payload exceeds 128 MiB.')
        names.add(source.name.casefold())
        pending.append((source, data, hashlib.sha256(data).hexdigest()))
    if not pending:
        return []
    created_directories = []
    for path in (directory.parent.parent, directory.parent, directory):
        if not path.exists():
            created_directories.append(path)
    created = []
    try:
        directory.mkdir(parents=True, exist_ok=True, mode=0o755)
        for source, data, digest in pending:
            destination = directory / source.name
            with destination.open('xb') as stream:
                created.append(destination)
                stream.write(data)
            destination.chmod(0o644)
        return [{'path': (directory / source.name).relative_to(home).as_posix(),
                 'system_path': str(source), 'sha256': digest} for source, _, digest in pending]
    except BaseException:
        for path in created:
            path.unlink(missing_ok=True)
        for path in reversed(created_directories):
            try:
                path.rmdir()
            except OSError:
                pass
        raise


def prepare(source, target, strip=False, fonts=()):
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
    font_mapping = add_fonts(target, fonts) if fonts else []
    return {'pruned': ['include/', 'pkgconfig/', 'man/', '*.a', '*.la'],
            'strip_debug': strip, 'stripped': stripped,
            'fonts': font_mapping,
            'retained': 'Runtime ELF/PE modules, exports, fonts, locale data and runtime helpers.'}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', required=True, type=Path)
    p.add_argument('--target', required=True, type=Path)
    p.add_argument('--strip-debug', action='store_true')
    p.add_argument('--font', action='append', type=Path, default=[], help='Copy an unchanged .ttf into the build runtime; repeat for each face.')
    args = p.parse_args(argv)
    print(json.dumps(prepare(args.source, args.target, args.strip_debug, args.font), indent=2))


if __name__ == '__main__':
    main()
