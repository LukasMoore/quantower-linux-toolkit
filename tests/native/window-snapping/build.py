#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Compile only original snapping probes; never execute Wine or touch a prefix."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

HERE = Path(__file__).resolve().parent
SOURCES = ('snap-eight.c', 'snap-hold.c', 'child-keyboard-v3.c', 'child-keyboard-v4.c')
FLAGS = ('-std=c11', '-O2', '-Wall', '-Wextra', '-Werror', '-static-libgcc',
         '-Wl,--no-insert-timestamp')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--compiler', default='x86_64-w64-mingw32-gcc')
    args = parser.parse_args()
    compiler = shutil.which(args.compiler)
    if not compiler:
        raise ValueError('An x86-64 MinGW C compiler is required.')
    output = args.output_dir.absolute()
    if output.exists() or output.is_symlink():
        raise FileExistsError('Use a new output directory; probes are never overwritten.')
    output.mkdir(mode=0o700)
    manifest = {'compile_only': True, 'executed': False, 'flags': list(FLAGS),
                'compiler': subprocess.check_output([compiler, '--version'], text=True).splitlines()[0],
                'sources': {}, 'binaries': {}}
    for name in SOURCES:
        source = HERE / name
        target = output / (source.stem + '.exe')
        subprocess.run([compiler, *FLAGS, str(source), '-o', str(target), '-luser32'], check=True)
        manifest['sources'][name] = hashlib.sha256(source.read_bytes()).hexdigest()
        manifest['binaries'][target.name] = hashlib.sha256(target.read_bytes()).hexdigest()
    (output / 'build.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps({'compiled': len(SOURCES), 'executed': False}))


if __name__ == '__main__':
    main()
