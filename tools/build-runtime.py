#!/usr/bin/env python3
"""Fetch pinned open-source dependencies, apply reviewed patches, and stage builds.

This tool never starts Wine or the application and never installs system files.
The build's source identity is pinned; host compiler/dependency ABIs still affect
the resulting binary. It is not a bit-for-bit reproduction of a legacy runtime.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.parse
import urllib.request

REPOSITORY = Path(__file__).resolve().parents[1]


class HTTPSRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme != 'https':
            raise ValueError('Source download redirected away from HTTPS')
        return super().redirect_request(request, response, code, message, headers, newurl)


def open_https(request):
    # Enforce every redirect, including an intermediate downgrade that later
    # redirects back to HTTPS. Public sources never need authentication cookies.
    opener = urllib.request.build_opener(HTTPSRedirectHandler())
    return opener.open(request, timeout=60)


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def safe_relative(value):
    path = PurePosixPath(value)
    if not value or path.is_absolute() or '\\' in value or any(p in ('', '.', '..') for p in value.split('/')):
        raise ValueError('Unsafe relative path: ' + value)
    return path


def fetch_archive(spec, cache, offline=False):
    """Use a verified cache entry, or atomically download and verify HTTPS bytes."""
    name = spec['filename']
    if Path(name).name != name or name in ('', '.', '..'):
        raise ValueError('Archive filename must be a single filename')
    expected = spec['sha256']
    if len(expected) != 64 or any(c not in '0123456789abcdef' for c in expected):
        raise ValueError('Invalid pinned SHA256')
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    if cache.is_symlink():
        raise ValueError('Cache directory must not be a symlink')
    destination = cache / name
    if destination.is_symlink():
        raise ValueError('Archive cache entry must not be a symlink')
    if destination.exists():
        if not destination.is_file() or sha256(destination) != expected:
            raise ValueError('Cached archive hash mismatch: ' + name)
        return destination
    if offline:
        raise FileNotFoundError('Pinned archive is missing from offline cache: ' + name)
    parsed = urllib.parse.urlsplit(spec['url'])
    if parsed.scheme != 'https' or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError('Source downloads must use credential-free HTTPS')
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=cache, prefix='.download-', delete=False) as output:
            temporary = Path(output.name)
            request = urllib.request.Request(spec['url'], headers={'User-Agent': 'quantower-linux-toolkit-source-builder/0.1'})
            with open_https(request) as response:
                if urllib.parse.urlsplit(response.geturl()).scheme != 'https':
                    raise ValueError('Source download redirected away from HTTPS')
                total = 0
                while chunk := response.read(1024 * 1024):
                    total += len(chunk)
                    if total > spec.get('max_bytes', 256 * 1024 * 1024):
                        raise ValueError('Source archive exceeds its size limit')
                    output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        if sha256(temporary) != expected:
            raise ValueError('Downloaded archive hash mismatch: ' + name)
        os.chmod(temporary, 0o600)
        # Do not overwrite another fetcher's different cache entry.
        try:
            os.link(temporary, destination)
        except FileExistsError:
            if destination.is_symlink() or sha256(destination) != expected:
                raise ValueError('Concurrent archive cache entry differs')
        return destination
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def extract_archive(archive, destination, root_name):
    """Extract one root, refusing links/devices/traversal and existing destinations."""
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise FileExistsError('Source destination must be new: ' + str(destination))
    safe_relative(root_name)
    if '/' in root_name:
        raise ValueError('Archive root must be one directory')
    with tarfile.open(archive, 'r:*') as source:
        members = source.getmembers()
        if len(members) > 100000 or sum(max(0, m.size) for m in members) > 2 * 1024**3:
            raise ValueError('Extracted source exceeds its size limit')
        paths = []
        seen = set()
        for member in members:
            name = member.name.rstrip('/')
            relative = safe_relative(name)
            if relative.parts[0] != root_name or not (member.isdir() or member.isfile()):
                raise ValueError('Unsafe archive member: ' + member.name)
            if name in seen:
                raise ValueError('Duplicate archive path: ' + name)
            seen.add(name)
            paths.append((member, relative.parts[1:]))
        destination.mkdir(parents=True)
        try:
            for member, parts in paths:
                if not parts:
                    if not member.isdir():
                        raise ValueError('Archive root is not a directory')
                    continue
                target = destination.joinpath(*parts)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with source.extractfile(member) as stream, target.open('xb') as output:
                        shutil.copyfileobj(stream, output)
                    os.chmod(target, member.mode & 0o777)
        except BaseException:
            shutil.rmtree(destination)
            raise


def run(command, cwd, log, env=None):
    log.write(('\n$ ' + ' '.join(map(str, command)) + '\n').encode())
    log.flush()
    subprocess.run(list(map(str, command)), cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT, check=True)


def apply_patches(spec, source, log, repository=REPOSITORY):
    for patch in spec.get('patches', []):
        path = Path(repository).joinpath(*safe_relative(patch['path']).parts)
        if path.is_symlink() or sha256(path) != patch['sha256']:
            raise ValueError('Patch hash mismatch: ' + str(path))
        # --fuzz=0 refuses context approximation. Forward-only prevents reversal.
        command = ['patch', '-p1', '--fuzz=0', '--batch', '--forward', '-i', path.resolve()]
        run([*command, '--dry-run'], source, log)
        run(command, source, log)


def check_dependencies(components, prepare_only=False):
    """Fail before downloads for missing executable/toolchain prerequisites."""
    groups = [('patch',)]
    if not prepare_only:
        if sys.platform != 'linux' or platform.machine().lower() not in ('x86_64', 'amd64'):
            raise ValueError('Native builds require x86_64 Linux; source preparation can run separately')
        groups += [('cc',), ('make',), ('pkg-config',)]
        if 'wine' in components:
            groups += [('bison',), ('flex',),
                       ('i686-w64-mingw32-gcc', 'i686-w64-mingw32-clang'),
                       ('x86_64-w64-mingw32-gcc', 'x86_64-w64-mingw32-clang')]
        if 'gamescope' in components:
            groups += [('c++',), ('meson',), ('ninja',), ('cmake',), ('glslang', 'glslangValidator')]
    selected, missing = {}, []
    for alternatives in groups:
        found = next((tool for tool in alternatives if shutil.which(tool)), None)
        if found:
            selected[' or '.join(alternatives)] = found
        else:
            missing.append(' or '.join(alternatives))
    if missing:
        raise ValueError('Missing build tools: ' + ', '.join(missing) +
                         '. See native/README.txt for distribution packages. Wine needs both i686 and x86_64 PE compilers; no 32-bit Linux multilib is required by the selected WoW64 mode.')
    if not prepare_only and 'gamescope' in components:
        result = subprocess.run(['meson', '--version'], capture_output=True, text=True, check=True)
        match = re.match(r'^(\d+)\.(\d+)(?:\.(\d+))?', result.stdout.strip())
        if not match or tuple(map(int, (match[1], match[2]))) < (1, 3):
            raise ValueError('gamescope vendored wlroots requires Meson >=1.3; install a newer Meson before building')
        selected['meson_version'] = result.stdout.strip()
    return selected


def prepare_component(name, spec, work, cache, offline, log, repository=REPOSITORY):
    archive = fetch_archive(spec['archive'], cache, offline)
    source = work / 'source' / name
    extract_archive(archive, source, spec['archive']['root'])
    for extra in spec.get('extra_sources', []):
        archive = fetch_archive(extra, cache, offline)
        target = source.joinpath(*safe_relative(extra['destination']).parts)
        # Upstream source archives can include empty submodule placeholders.
        if target.is_dir() and not target.is_symlink() and not any(target.iterdir()):
            target.rmdir()
        extract_archive(archive, target, extra['root'])
    for overlay in spec.get('overlays', []):
        origin = source.joinpath(*safe_relative(overlay['from']).parts)
        destination = source.joinpath(*safe_relative(overlay['to']).parts)
        if not origin.is_file() or destination.is_symlink():
            raise ValueError('Invalid upstream build overlay')
        shutil.copy2(origin, destination)
    apply_patches(spec, source, log, repository)
    return source


def build_component(name, source, work, jobs, log):
    build = work / 'build' / name
    build.mkdir(parents=True)
    stage = work / 'stage' / name
    stage.mkdir(parents=True)
    if name == 'wine':
        # Modern WoW64 uses a 64-bit Unix host with both 32/64-bit PE modules.
        # --enable-win64 alone cannot execute common PE32 installer bootstraps.
        run([source / 'configure', '--prefix=/opt/wine-devel', '--enable-archs=i386,x86_64', '--without-wayland'], build, log)
        run(['make', '-j', jobs], build, log)
        run(['make', 'install', 'DESTDIR=' + str(stage)], build, log)
    elif name == 'x11':
        run([source / 'configure', '--prefix=/usr', '--libdir=/usr/lib', '--disable-static', '--disable-specs',
             '--disable-devel-docs', '--disable-xf86bigfont', '--without-xmlto', '--without-fop'], build, log)
        run(['make', '-j', jobs], build, log)
        run(['make', 'install', 'DESTDIR=' + str(stage)], build, log)
    elif name == 'gamescope':
        run(['meson', 'setup', build, source, '--prefix=/usr', '--buildtype=debugoptimized', '-Db_ndebug=false',
             '--wrap-mode=nodownload', '-Dpipewire=enabled', '-Ddrm_backend=enabled', '-Dsdl2_backend=disabled',
             '-Denable_openvr_support=false', '-Dinput_emulation=enabled', '-Davif_screenshots=disabled',
             '-Drt_cap=enabled', '-Denable_tests=false', '-Denable_gamescope_wsi_layer=false'], work, log)
        run(['ninja', '-C', build, '-j', jobs], work, log)
        environment = os.environ.copy()
        environment['DESTDIR'] = str(stage)
        run(['meson', 'install', '-C', build, '--no-rebuild'], work, log, environment)
    else:
        raise ValueError('Unsupported component')
    return stage


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--component', choices=('wine', 'x11', 'gamescope', 'all'), default='all')
    parser.add_argument('--work-dir', type=Path, help='A new directory outside the application install')
    parser.add_argument('--cache', type=Path, help='Archive cache; every use verifies SHA256')
    parser.add_argument('--offline', action='store_true', help='Require all selected archives in --cache')
    parser.add_argument('--prepare-only', action='store_true', help='Fetch, verify, extract and apply patches without compiling')
    parser.add_argument('--check-deps', action='store_true', help='Check executable/toolchain prerequisites only; no downloads or directory writes')
    parser.add_argument('--jobs', type=int, default=min(4, os.cpu_count() or 1))
    args = parser.parse_args(argv)
    if not 1 <= args.jobs <= 64:
        parser.error('--jobs must be between 1 and 64')
    os.umask(0o077)
    lock_path = REPOSITORY / 'runtime-lock.json'
    lock = json.loads(lock_path.read_text())
    if lock.get('schema_version') != 1:
        parser.error('Unsupported runtime lock schema')
    components = list(lock['components']) if args.component == 'all' else [args.component]
    try:
        dependencies = check_dependencies(components, args.prepare_only)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.error(str(error))
    if args.check_deps:
        print(json.dumps({'components': components, 'tools': dependencies, 'scope': 'Executable prerequisites only; configure still checks headers/libraries'}))
        return 0
    if args.work_dir is None:
        parser.error('--work-dir is required unless --check-deps is used')
    work = args.work_dir.absolute()
    if work.exists() or work.is_symlink():
        parser.error('--work-dir must be a new directory; existing builds are never overwritten')
    cache = (args.cache or work / 'downloads').absolute()
    work.mkdir(parents=True)
    (work / 'logs').mkdir()
    record = {'schema_version': 1, 'runtime_lock_sha256': sha256(lock_path), 'components': {},
              'prepared_only': args.prepare_only, 'tools': dependencies,
              'scope': 'Pinned upstream source plus local patches; host-dependent build, no app integration claim'}
    try:
        for name in components:
            spec = lock['components'][name]
            with (work / 'logs' / (name + '.log')).open('xb') as log:
                source = prepare_component(name, spec, work, cache, args.offline, log)
                stage = None if args.prepare_only else build_component(name, source, work, str(args.jobs), log)
            record['components'][name] = {'source_version': spec['version'], 'source_archive_sha256': spec['archive']['sha256'],
                                           'patches': spec.get('patches', []), 'stage': str(stage) if stage else None}
            print(name + ': ' + ('source and patches verified' if args.prepare_only else 'build staged'), flush=True)
        (work / 'build-result.json').write_text(json.dumps(record, indent=2) + '\n')
        return 0
    except (OSError, ValueError, subprocess.CalledProcessError, tarfile.TarError) as error:
        record['error_type'] = type(error).__name__
        record['error'] = str(error)
        (work / 'build-result.json').write_text(json.dumps(record, indent=2) + '\n')
        print('Build stopped: ' + str(error) + '\nSee ' + str(work / 'logs'), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
