#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Compile the original own-window fixture. Never run Wine or touch a prefix."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--compiler', default='x86_64-w64-mingw32-gcc')
    args = parser.parse_args()
    compiler = shutil.which(args.compiler)
    if not compiler:
        raise ValueError('An x86-64 MinGW compiler is required.')
    target = args.output_dir.absolute()
    if target.exists() or target.is_symlink():
        raise FileExistsError('Use a new output directory.')
    target.mkdir(mode=0o700)
    source = Path(__file__).with_name('window-probe.c')
    flags = ['-DUNICODE', '-D_UNICODE', '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
             '-Wl,--no-insert-timestamp']
    binary = target / 'window-probe.exe'
    subprocess.run([compiler, *flags, str(source), '-o', str(binary), '-luser32', '-lgdi32', '-lcomctl32'], check=True)
    (target/'build.json').write_text(json.dumps({'compile_only': True, 'flags': flags,
        'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
        'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest()}, indent=2)+'\n')

if __name__ == '__main__':
    main()
