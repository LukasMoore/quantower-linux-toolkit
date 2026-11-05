#!/usr/bin/env python3
"""Build the optional Linux/glibc assertion trace library into a new local file."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cc', default='cc', help='C compiler executable (no shell command)')
    args = parser.parse_args()
    source = Path(__file__).resolve().with_name('assert_trace.c')
    output = args.output.absolute()
    if output.exists() or output.is_symlink():
        parser.error('--output must be a new file')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent, prefix='.assert-build-') as directory:
        temporary = Path(directory) / 'libquantower_assert_trace.so'
        subprocess.run([args.cc, '-std=c11', '-shared', '-fPIC', '-O2', '-g', '-fvisibility=hidden',
                        '-Wall', '-Wextra', '-Werror', '-Wl,-z,now', '-o', str(temporary), str(source),
                        '-Wl,--no-as-needed', '-lgcc_s', '-Wl,--as-needed', '-ldl'], check=True)
        os.link(temporary, output)
    with output.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    print(json.dumps({'output': str(output), 'sha256': digest, 'license': 'MIT',
                      'scope': 'Optional crash-only __assert_fail interposer; preserves original fatal assertion'}))


if __name__ == '__main__':
    main()
