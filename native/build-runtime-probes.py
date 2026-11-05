#!/usr/bin/env python3
"""Compile original synthetic Win32 probes; never start Wine or an application."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parent
ARCHITECTURES = {'i386': 'i686-w64-mingw32', 'x86_64': 'x86_64-w64-mingw32'}
ALLOWED_IMPORTS = {'kernel32.dll', 'msvcrt.dll', 'gdi32.dll', 'gdiplus.dll', 'usp10.dll', 'user32.dll', 'ole32.dll', 'advapi32.dll'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', required=True, type=Path)
    args = parser.parse_args(argv)
    os.umask(0o077)
    output = args.output_dir.absolute()
    if output.exists() or output.is_symlink():
        parser.error('--output-dir must be a new private directory.')
    for prefix in ARCHITECTURES.values():
        if not shutil.which(prefix + '-g++') or not shutil.which(prefix + '-objdump'):
            parser.error('Both MinGW C++ compilers and objdump targets are required.')
    output.mkdir(parents=True, mode=0o700)
    source = ROOT / 'runtime-probe.cpp'
    record = {'schema_version': 1, 'synthetic_only': True, 'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
              'compiler_runtime_notice': 'MinGW-w64 CRT and GCC Runtime Library Exception terms apply to linked startup/runtime code; retain the matching toolchain notices.',
              'artifacts': {}}
    with (output / 'build.log').open('xb') as log:
        for architecture, prefix in ARCHITECTURES.items():
            compiler = prefix + '-g++'
            executable = output / ('runtime-probe-' + architecture + '.exe')
            command = [compiler, '-std=gnu++17', '-O2', '-Wall', '-Wextra', '-Werror', '-fno-exceptions', '-fno-rtti',
                       '-static-libgcc', '-static-libstdc++', '-Wl,--no-insert-timestamp', '-municode', str(source),
                       '-o', str(executable), '-lgdiplus', '-lusp10', '-lgdi32', '-luser32', '-lole32']
            subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)
            imports = subprocess.run([prefix + '-objdump', '-p', str(executable)], capture_output=True, text=True, check=True).stdout
            imported = sorted(set(re.findall(r'DLL Name:\s*(\S+)', imports)))
            if not set(x.lower() for x in imported).issubset(ALLOWED_IMPORTS):
                raise RuntimeError('Unexpected external runtime DLL dependency: ' + ', '.join(imported))
            version = subprocess.run([compiler, '--version'], capture_output=True, text=True, check=True).stdout.splitlines()[0]
            record['artifacts'][architecture] = {'filename': executable.name, 'sha256': hashlib.sha256(executable.read_bytes()).hexdigest(),
                                                'compiler': compiler, 'compiler_version': version, 'imports': imported,
                                                'embedded_timestamp': 0, 'executed': False}
    (output / 'build-manifest.json').write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps(record, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
