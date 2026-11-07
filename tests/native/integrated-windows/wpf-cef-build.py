#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Compile original WPF/CEF fixture only; no downloads, Wine, app or prefix writes."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess

HERE = Path(__file__).resolve().parent


def managed(path):
    data = path.read_bytes()
    if len(data) < 64 or data[:2] != b'MZ':
        return False
    pe = struct.unpack_from('<I', data, 0x3c)[0]
    if len(data) < pe + 24 or data[pe:pe + 4] != b'PE\0\0':
        return False
    optional = pe + 24
    magic = struct.unpack_from('<H', data, optional)[0]
    if magic not in (0x10b, 0x20b):
        return False
    directories = optional + (112 if magic == 0x20b else 96)
    return len(data) >= directories + 15 * 8 and struct.unpack_from('<I', data, directories + 14 * 8)[0] != 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dotnet', required=True, type=Path, help='Existing Linux .NET host for the compiler.')
    parser.add_argument('--compiler', required=True, type=Path, help='Existing Roslyn csc.dll.')
    parser.add_argument('--framework-root', required=True, type=Path, help='Existing official Windows shared framework directory.')
    parser.add_argument('--framework-version', default='10.0.13')
    parser.add_argument('--cefsharp-home', required=True, type=Path, help='Existing matching CefSharp public dependency assemblies.')
    parser.add_argument('--output', required=True, type=Path, help='New private directory outside source and prefixes.')
    args = parser.parse_args(argv)
    os.umask(0o077)
    output = args.output.resolve()
    if output.exists() or args.output.is_symlink():
        raise ValueError('Fixture build output must be new.')
    if output.is_relative_to(HERE) or HERE.is_relative_to(output):
        raise ValueError('Fixture output must be separate from source.')
    if any((p / 'user.reg').is_file() and (p / 'drive_c').is_dir() for p in output.parents):
        raise ValueError('Fixture build output must be outside Wine prefixes.')
    refs = {}
    for framework in ('Microsoft.NETCore.App', 'Microsoft.WindowsDesktop.App'):
        folder = args.framework_root / framework / args.framework_version
        if not folder.is_dir():
            raise ValueError('Expected official Microsoft shared framework: ' + framework)
        for path in sorted(folder.glob('*.dll')):
            if managed(path):
                refs[path.name.casefold()] = path.resolve()
    for name in ('CefSharp.dll', 'CefSharp.Core.dll', 'CefSharp.Wpf.dll'):
        path = args.cefsharp_home / name
        if not path.is_file() or not managed(path):
            raise ValueError('Expected matching public CefSharp managed dependency: ' + name)
        refs[name.casefold()] = path.resolve()
    for path in (args.dotnet, args.compiler):
        if not path.is_file():
            raise ValueError('Existing compiler host and csc.dll are required.')
    output.mkdir(parents=True, mode=0o700)
    assembly = output / 'WpfCefFixture.dll'
    response = ['/nologo', '/nostdlib+', '/target:exe', '/optimize+', '/deterministic+',
                '/out:"' + str(assembly) + '"']
    response += ['/reference:"' + str(path) + '"' for path in refs.values()]
    response += ['"' + str(HERE / 'WpfCefFixture.cs') + '"']
    rsp = output / 'compile.rsp'
    rsp.write_text('\n'.join(response) + '\n')
    environment = dict(os.environ)
    for key in ('DOTNET_STARTUP_HOOKS', 'QUANTOWER_DIAGNOSTIC_STARTUP_HOOKS', 'LD_PRELOAD'):
        environment.pop(key, None)
    result = subprocess.run([str(args.dotnet.resolve()), 'exec', str(args.compiler.resolve()), '@' + str(rsp)], env=environment,
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    (output / 'compile.log').write_bytes(result.stdout)
    if result.returncode:
        raise RuntimeError('Fixture compiler failed; inspect private compile.log.')
    runtime = {'runtimeOptions': {'tfm': 'net10.0', 'framework': {
        'name': 'Microsoft.WindowsDesktop.App', 'version': args.framework_version},
        'configProperties': {'Switch.System.Windows.Media.MediaContext.DisableDirtyRectangles': True}}}
    config = output / 'WpfCefFixture.runtimeconfig.json'
    config.write_text(json.dumps(runtime, indent=2) + '\n')
    report = {'schema_version': 1, 'compiler_exit': result.returncode, 'fixture_executed': False,
              'reference_count': len(refs), 'references': {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in refs.values()},
              'outputs': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (assembly, config)},
              'source_sha256': hashlib.sha256((HERE / 'WpfCefFixture.cs').read_bytes()).hexdigest(),
              'limits': 'Compile only; no fixture launch, display connection, vendor resources, dependency copies, or application prefix mutation.'}
    (output / 'build-result.json').write_text(json.dumps(report, indent=2) + '\n')
    print('Compiled original WPF/CEF fixture; no fixture or application was launched.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
