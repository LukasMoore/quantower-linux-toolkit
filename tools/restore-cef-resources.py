#!/usr/bin/env python3
"""Restore missing, exactly matched CEF resources from a user-downloaded package.

No CEF engine replacement, vendor bytes or user data are distributed by this tool.
The default is a read-only plan. Apply requires the inactive installation's lock.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import application_runtime
import profile_manager

PACKAGE_SHA256 = '2046e4b438f372c0f22fd8b2eccfbad00e192921ce57a732cc04afb48ecb4bf4'
ENGINE_SHA256 = '529f6a285a151a337613878bb786dded80e99f8922c9a200605620b0acdbefce'
PACKAGE_VERSION = '127.3.5'


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def local_path(path, base):
    if not path.is_relative_to(base):
        raise ValueError('Resource destination escapes the installation.')
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise ValueError('Resource paths must not contain symlinks.')
        if parent == base:
            break
    return path


def plan(base, package):
    base = Path(base).resolve(strict=True)
    package = Path(package).resolve(strict=True)
    app = application_runtime.load(base)
    native = local_path(app.directory / 'bin/Native/Windows', base)
    engine = local_path(native / 'libcef.dll', base)
    if not engine.is_file() or digest(engine) != ENGINE_SHA256:
        raise ValueError('Installed CEF engine does not match this resource package; no files were changed.')
    changes = []
    # Verify and read through the same open description; never reopen by path.
    with package.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != PACKAGE_SHA256:
            raise ValueError('CEF package checksum does not match the pinned official package.')
        stream.seek(0)
        with zipfile.ZipFile(stream) as archive:
            engine_info = archive.getinfo('runtimes/win-x64/native/libcef.dll')
            with archive.open(engine_info) as engine_stream:
                if hashlib.file_digest(engine_stream, 'sha256').hexdigest() != ENGINE_SHA256:
                    raise ValueError('Package engine differs from the installed engine.')
            entries = [entry for entry in archive.infolist() if
                       re.fullmatch(r'CEF/win-x64/locales/[A-Za-z0-9_-]+\.pak', entry.filename) or
                       entry.filename in ('runtimes/win-x64/native/libEGL.dll', 'runtimes/win-x64/native/libGLESv2.dll')]
            if len({entry.filename for entry in entries}) != len(entries) or len(entries) != 57:
                raise ValueError('Pinned CEF package has an unexpected resource inventory.')
            for entry in entries:
                if entry.file_size > 32 * 1024 ** 2 or entry.is_dir():
                    raise ValueError('CEF resource exceeds its bounded size.')
                relative = Path('locales') / Path(entry.filename).name if '/locales/' in entry.filename else Path(entry.filename).name
                destination = local_path(native / relative, base)
                data = archive.read(entry)
                expected = hashlib.sha256(data).hexdigest()
                exists = destination.exists()
                if exists and (not destination.is_file() or digest(destination) != expected):
                    raise ValueError('Existing CEF resource differs; it was preserved: ' + str(relative))
                changes.append({'path': destination, 'data': data, 'sha256': expected, 'exists': exists})
        stream.seek(0)
        if hashlib.file_digest(stream, 'sha256').hexdigest() != PACKAGE_SHA256:
            raise ValueError('CEF package changed while reading; no files were changed.')
    return base, changes


def apply(base, package):
    base = Path(base).resolve(strict=True)
    profile_manager.no_running_prefix(base / 'prefix')
    lock_path = local_path(base / 'logs/launch.lock', base)
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'a') as lock:
        identity = os.fstat(lock.fileno())
        if not stat.S_ISREG(identity.st_mode) or identity.st_uid != os.getuid() or identity.st_mode & 0o077:
            raise RuntimeError('The launch lock ownership or permissions are unsafe.')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        profile_manager.no_running_prefix(base / 'prefix')
        profile_manager.load(base)
        base, changes = plan(base, package)
        manifest_path = local_path(base / 'runtime-components/manifest.json', base)
        manifest_before = manifest_path.read_bytes()
        manifest = json.loads(manifest_before)
        manifest['required_files'].update({str(item['path'].relative_to(base)): item['sha256'] for item in changes})
        created = []
        try:
            for item in changes:
                if item['exists']:
                    continue
                local_path(item['path'], base)
                item['path'].parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                with item['path'].open('xb') as stream:
                    created.append(item['path'])
                    stream.write(item['data'])
                    stream.flush()
                    os.fsync(stream.fileno())
                item['path'].chmod(0o600)
            profile_manager.atomic_bytes(manifest_path, (json.dumps(manifest, indent=2) + '\n').encode())
            profile_manager.load(base)
        except BaseException:
            for path in created:
                path.unlink(missing_ok=True)
            profile_manager.atomic_bytes(manifest_path, manifest_before)
            raise
    return {'mode': 'restored', 'package_version': PACKAGE_VERSION, 'resources_verified': len(changes),
            'resources_added': len(created), 'engine_replaced': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', required=True, type=Path)
    parser.add_argument('--package', required=True, type=Path)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    os.umask(0o077)
    try:
        if args.apply:
            result = apply(args.base, args.package)
        else:
            _, changes = plan(args.base, args.package)
            result = {'mode': 'plan', 'resources_verified': len(changes),
                      'resources_missing': sum(not item['exists'] for item in changes), 'writes_performed': False}
    except (OSError, ValueError, RuntimeError, KeyError, zipfile.BadZipFile) as error:
        parser.exit(1, 'CEF restoration stopped: ' + str(error) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
