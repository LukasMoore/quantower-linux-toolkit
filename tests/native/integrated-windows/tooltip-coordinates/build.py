#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Compile original v5/v6 tooltip fixtures; never execute Wine or touch a prefix."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

INPUTS = {
    'monitor-workareas.c': 'cade4936676bd79c10cf81b3835da077ef09b50ded0f1cf4a5be24296c09e0fa',
    'tooltip-track-coordinates.c': '5bb1680be15f365a0535d2254489c74f17623946d485cd3e7a15508712e2c20a',
    'tooltip-v6.rc': '1a5925ca7c097d852c9e18000b1adcb3de69892033cf56078eb6b3c0c9b37de8',
    'tooltip-v6.manifest': '6df4ec061ed7d8a508c2f3bf2d17f71e56d1cb8731d611e633b459016f30a651',
}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--compiler', default='x86_64-w64-mingw32-gcc')
    parser.add_argument('--resource-compiler', default='x86_64-w64-mingw32-windres')
    args = parser.parse_args(argv)
    output = args.output_dir.absolute()
    if output.exists() or output.is_symlink():
        raise FileExistsError('Use a new private output directory.')
    source = Path(__file__).resolve().parent
    for name, expected in INPUTS.items():
        if hashlib.sha256((source / name).read_bytes()).hexdigest() != expected:
            raise ValueError('Original tooltip fixture input changed: ' + name)
    compiler, resources = shutil.which(args.compiler), shutil.which(args.resource_compiler)
    if not compiler or not resources:
        raise ValueError('Existing x86-64 MinGW C and resource compilers are required.')
    os.umask(0o077)
    output.mkdir(parents=True, mode=0o700)
    flags = ['-DUNICODE', '-D_UNICODE', '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
             '-Wl,--no-insert-timestamp']
    resource = output / 'tooltip-v6-res.o'
    results = {}
    with (output / 'compile.log').open('xb') as log:
        monitor_binary=output/'monitor-workareas.exe'
        subprocess.run([compiler,*flags,str(source/'monitor-workareas.c'),'-o',str(monitor_binary),'-luser32'],
                       stdout=log,stderr=subprocess.STDOUT,check=True)
        results['monitors']={'filename':monitor_binary.name,'sha256':hashlib.sha256(monitor_binary.read_bytes()).hexdigest()}
        subprocess.run([resources, '-I', str(source), '-i', str(source / 'tooltip-v6.rc'),
                        '-o', str(resource)], cwd=source, stdout=log, stderr=subprocess.STDOUT, check=True)
        for family, name in (('v5', 'tooltip-track-coordinates.exe'), ('v6', 'tooltip-track-coordinates-v6.exe')):
            binary = output / name
            command = [compiler, *flags, str(source / 'tooltip-track-coordinates.c')]
            if family == 'v6':
                command.append(str(resource))
            command += ['-o', str(binary), '-luser32', '-lgdi32', '-lcomctl32']
            subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)
            results[family] = {'filename': name, 'sha256': hashlib.sha256(binary.read_bytes()).hexdigest()}
    (output / 'build.json').write_text(json.dumps({'compile_only': True, 'source_inputs': INPUTS,
        'flags': flags, 'binaries': results}, indent=2) + '\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
