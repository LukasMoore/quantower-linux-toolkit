#!/usr/bin/env python3
"""Collect native runtime dependencies inside the Debian build image, without execution.

lddtree parses ELF metadata. Compiler SDKs, the loader/glibc and host GPU stack are
not copied. Wine configure SONAME entries cover libraries loaded with dlopen.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

HOST_PATTERNS = (
    r'ld-linux.*', r'libc\.so.*', r'libm\.so.*', r'libdl\.so.*', r'libpthread\.so.*',
    r'librt\.so.*', r'libresolv\.so.*', r'libutil\.so.*', r'libanl\.so.*', r'libnss_.*',
    r'libmvec\.so.*', r'libthread_db\.so.*', r'libBrokenLocale\.so.*',
    r'libGL\.so.*', r'libEGL[^/]*', r'libGLES[^/]*', r'libGLX[^/]*', r'libGLdispatch[^/]*',
    r'libglapi\.so.*', r'libgallium[^/]*',
    r'libOpenGL[^/]*', r'libgbm[^/]*', r'libdrm[^/]*', r'libvulkan[^/]*',
    r'libnvidia[^/]*', r'libcuda[^/]*', r'libnvcuvid[^/]*', r'libvdpau[^/]*',
    r'libva\.so.*', r'libva-[^/]*', r'libOpenCL[^/]*',
)


def file_sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def is_elf(path):
    try:
        with Path(path).open('rb') as stream:
            return stream.read(4) == b'\x7fELF'
    except OSError:
        return False


def host_library(name):
    return any(re.fullmatch(pattern, name) for pattern in HOST_PATTERNS)


def cache_libraries(runner=subprocess.run):
    result = runner(['ldconfig', '-p'], capture_output=True, text=True, check=True)
    found = {}
    for line in result.stdout.splitlines():
        match = re.match(r'\s*(\S+) \(([^)]*)\) => (\S+)', line)
        if match and 'x86-64' in match[2]:
            found.setdefault(match[1], Path(match[3]))
    return found


def dependencies(path, root, runner=subprocess.run):
    environment = os.environ.copy()
    environment.pop('LD_PRELOAD', None)
    environment['LD_LIBRARY_PATH'] = str(root / 'lib')
    result = runner(['lddtree', '-l', str(path)], env=environment, capture_output=True,
                    text=True, check=True)
    paths = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        if not line.startswith('/') or not Path(line).is_file():
            raise ValueError('Unresolved ELF dependency for ' + str(path) + ': ' + line)
        paths.append(Path(line))
    return paths


def collect(root, payload, *, helpers=(), config=None, private_x11=False, optional_sonames=(), runner=subprocess.run):
    root = Path(root).resolve(strict=True)
    if not root.is_dir() or not re.fullmatch(r'payload/[a-z0-9_-]+', payload):
        raise ValueError('Expected a staged component root and a payload/component prefix.')
    if any((parent / 'user.reg').is_file() for parent in (root, *root.parents)):
        raise ValueError('Dependency collection must not operate inside a Wine prefix.')
    # Run once on a fresh build-stage copy, retaining its external provenance
    # report. Re-running on an already populated closure loses upstream origins.
    for name in ('lib', 'bin'):
        directory = root / name
        if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
            raise ValueError('Staged runtime library/helper directory is redirected or not a directory.')
    inputs = [p for p in sorted(root.rglob('*')) if p.is_file() and is_elf(p)]
    helper_targets = []
    for helper in helpers:
        helper = Path(helper).resolve(strict=True)
        if not is_elf(helper):
            raise ValueError('Runtime helper must be an ELF executable: ' + str(helper))
        helper_targets.append((helper, root / 'bin' / helper.name))
        inputs.append(helper)
    unavailable_optional = []
    if any(not re.fullmatch(r'[A-Za-z0-9_.+-]+', name) for name in optional_sonames):
        raise ValueError('Optional SONAMEs must be library basenames.')
    if config:
        names = re.findall(r'^#define SONAME_\w+ "([^"/]+)"', Path(config).read_text(), re.M)
        available = cache_libraries(runner)
        for name in names:
            if host_library(name) or (private_x11 and name in ('libX11.so.6', 'libX11-xcb.so.1')):
                continue
            if name not in available:
                if name in optional_sonames:
                    unavailable_optional.append(name)
                    continue
                raise ValueError('Configured dlopen dependency is absent: ' + name)
            inputs.append(available[name])
    pending, excluded = {}, {}
    for source, destination in helper_targets:
        previous = pending.get(str(destination))
        if previous is not None and file_sha(previous) != file_sha(source):
            raise ValueError('Conflicting helpers share a basename: ' + source.name)
        pending[str(destination)] = source
    for entry in inputs:
        for source in dependencies(entry, root, runner):
            if source.resolve().is_relative_to(root):
                continue
            if host_library(source.name):
                excluded[source.name] = str(source)
                continue
            if private_x11 and source.name in ('libX11.so.6', 'libX11-xcb.so.1'):
                continue
            if any(source == helper for helper, _ in helper_targets):
                continue
            destination = root / 'lib' / source.name
            previous = pending.get(str(destination))
            if previous is not None and file_sha(previous) != file_sha(source):
                raise ValueError('Conflicting libraries share a basename: ' + source.name)
            pending[str(destination)] = source
    # Complete preflight before changing the isolated staged component.
    for destination, source in pending.items():
        target = Path(destination)
        if not target.resolve().is_relative_to(root):
            raise ValueError('Staged dependency destination escapes its runtime root.')
        if target.exists() and file_sha(target) != file_sha(source):
            raise ValueError('Staged file already differs: ' + str(target))
        if target.is_symlink():
            raise ValueError('Staged dependency destination is a symlink.')
    records = []
    for destination, source in sorted(pending.items()):
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            shutil.copy2(source, target, follow_symlinks=True)
        records.append({'path': payload + '/' + target.relative_to(root).as_posix(),
                        'system_path': str(source.resolve()), 'sha256': file_sha(target)})
    return {'files': records, 'host_requirements': excluded, 'unavailable_optional': unavailable_optional,
            'scope': 'ELF DT_NEEDED closure plus configured Wine dlopen SONAMEs; explicit plugin/data review is still required.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--payload', required=True)
    parser.add_argument('--helper', action='append', default=[], type=Path)
    parser.add_argument('--wine-config', type=Path)
    parser.add_argument('--private-x11', action='store_true')
    parser.add_argument('--optional-soname', action='append', default=[], help='Explicitly report a known optional fallback SONAME when configure found no library.')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists() or args.output.is_symlink():
        parser.error('Output inventory must be a new file.')
    try:
        result = collect(args.root, args.payload, helpers=args.helper, config=args.wine_config,
                         private_x11=args.private_x11, optional_sonames=args.optional_soname)
        args.output.write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps({'copied_files': len(result['files']), 'host_requirements': sorted(result['host_requirements'])}))
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        parser.exit(1, 'Dependency collection stopped: ' + str(error) + '\n')


if __name__ == '__main__':
    main()
