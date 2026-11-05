#!/usr/bin/env python3
"""Collect a bounded, local diagnostic archive without controlling Quantower."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import stat
import sys
import tarfile


MIB = 1024 * 1024
APP_LOGS = Path('prefix/drive_c/AMPQuantowerInstalled/Logs')
# Explicit provenance only: do not recursively walk runtime payloads or backups.
PROVENANCE_FILES = (
    'application-version.json',
    'runtime-components/application-upgrade.json',
    'runtime-components/cef-locales.json',
    'runtime-components/cef-angle.json',
    'runtime-components/account-template-migration.json',
    'runtime-components/x11/source.json',
    'runtime-components/x11/provenance.json',
    'runtime-components/x11/validation.json',
    'runtime-components/x11/locking-xid-after-sequence-sync.patch',
    'runtime-components/gamescope/provenance.json',
    'runtime-components/gamescope/ordered-vulkan-shutdown.patch',
    'runtime-components/gamescope/black-buffer-fix.patch',
    'runtime-components/native-maintenance-20261105/deployment.json',
    'runtime-components/native-maintenance-20261105/font-maintenance-summary.json',
    'runtime-components/native-maintenance-20261105/font-official-sources.json',
    'runtime-components/native-maintenance-20261105/font-artifact-manifest.json',
    'runtime-components/native-maintenance-20261105/gdiplus/isolated-review-provenance.json',
    'runtime-components/native-maintenance-20261105/gdiplus/abi-validation.json',
    'runtime-components/native-maintenance-20261105/gdiplus/text-call-resources-on-installed.patch',
    'runtime-components/native-maintenance-20261105/gdi32/isolated-review-provenance.json',
    'runtime-components/native-maintenance-20261105/gdi32/abi-validation.json',
    'runtime-components/native-maintenance-20261105/gdi32/script-cache-thread-ownership.patch',
    'runtime-components/native-maintenance-20261105/explorer/provenance/build-manifest.json',
    'runtime-components/native-maintenance-20261105/explorer/provenance/abi-validation.json',
    'runtime-components/native-maintenance-20261105/explorer/source/explorer-respect-appwindow.patch',
    'runtime-components/icon-fonts-1.147.5/install-plan.json',
    'runtime-components/icon-fonts-1.147.5/manifest.json',
    'runtime-components/icon-fonts-1.147.5/native-validation.json',
    'runtime-components/icon-fonts-1.147.5/deployment.json',
    'runtime-components/icon-fonts-1.147.5/compact-native-validation.json',
    'runtime-components/icon-fonts-1.147.5/e900-proof.json',
    'runtime-components/icon-fonts-1.147.5/supplemental-proof-manifest.json',
    'runtime-components/icon-fonts-1.147.5/name-only-invariants-summary.json',
    'runtime-components/icon-fonts-1.147.5/metadata-pipeline-summary.json',
    'runtime-components/icon-fonts-1.147.5/official-sources.json',
    'runtime-components/icon-fonts-1.147.5/source-review.txt',
    'runtime-components/native-maintenance-20261105/explorer-topmost/deployment.json',
    'runtime-components/native-maintenance-20261105/explorer-topmost/live-validation.json',
    'runtime-components/native-maintenance-20261105/explorer-topmost/official-sources.json',
    'runtime-components/native-maintenance-20261105/explorer-topmost/provenance/build-manifest.json',
    'runtime-components/native-maintenance-20261105/explorer-topmost/provenance/abi-validation.json',
    'runtime-components/native-maintenance-20261105/explorer-topmost/tests/synthetic-fixture-results.json',
)


def positive_mib(value):
    try:
        amount = float(value)
        if not 0 < amount <= 1024:
            raise ValueError
        result = int(amount * MIB)
        if result < 1:
            raise ValueError
        return result
    except (ValueError, OverflowError):
        raise argparse.ArgumentTypeError('use a positive size, at most 1024 MiB') from None


def no_symlink_path(path):
    """Reject symlink ancestors, including on the requested output location."""
    absolute = path.absolute()
    for parent in reversed((absolute, *absolute.parents)):
        if parent.is_symlink():
            raise ValueError(f'Symlinks are not allowed: {parent}')
    return absolute


def open_source(root_fd, relative):
    """Walk beneath base with directory handles and O_NOFOLLOW on every step."""
    relative = Path(relative)
    if relative.is_absolute() or any(p in ('', '.', '..') for p in relative.parts):
        raise ValueError('Invalid source path')
    descriptor = os.dup(root_fd)
    try:
        for part in relative.parts[:-1]:
            following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=descriptor)
            os.close(descriptor)
            descriptor = following
        result = os.open(relative.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                         dir_fd=descriptor)
        if not stat.S_ISREG(os.fstat(result).st_mode):
            os.close(result)
            raise ValueError('Source is not a regular file')
        return result
    finally:
        os.close(descriptor)


def recent(base, relative, *, suffix=None, limit=20, directories=False):
    """List one allowlisted directory only; never recurse through arbitrary logs."""
    folder = base / relative
    try:
        no_symlink_path(folder)
        candidates = []
        with os.scandir(folder) as entries:
            for entry in entries:
                if entry.is_symlink():
                    continue
                matches = (entry.is_dir(follow_symlinks=False) if directories else
                           entry.is_file(follow_symlinks=False))
                if matches and (suffix is None or entry.name.endswith(suffix)):
                    candidates.append((entry.stat(follow_symlinks=False).st_mtime_ns,
                                       Path(relative) / entry.name))
        return [p for _, p in sorted(candidates, reverse=True)[:limit]]
    except (OSError, ValueError):
        return []


def sources(base):
    paths = [Path('runtime-profile.json'), Path('runtime-components/manifest.json'),
             Path('logs/latest-session.json')]
    paths += [Path(relative) for relative in PROVENANCE_FILES]
    for name in ('launch.log', 'events.jsonl'):
        paths.append(Path('logs') / name)
    for session in recent(base, Path('logs/sessions'), limit=3, directories=True):
        paths.append(session / 'summary.json')
        paths.append(session / 'python-fault.log')
        paths.append(session / 'runtime.raw.log')
        for subdir in ('gpu', 'native-assert'):
            paths += recent(base, session / subdir, limit=20)
    for subdir in ('gpu', 'native-assert'):
        paths += recent(base, Path('logs') / subdir, limit=20)
    paths += recent(base, APP_LOGS / 'Serilog', suffix='.slog', limit=3)
    # Current installation uses Cef/; retain compatibility with other layouts.
    paths += [APP_LOGS / 'Cef/CefLogs.log', APP_LOGS / 'CefLogs.log',
              APP_LOGS / 'Serilog/CefLogs.log']
    # Older rolling files have lower priority if the total budget is exhausted.
    for suffix in ('.1', '.2', '.3', '.4'):
        for name in ('launch.log', 'events.jsonl'):
            paths.append(Path('logs') / (name + suffix))
    return list(dict.fromkeys(paths))


def prepare_output(destination):
    destination = no_symlink_path(destination)
    missing = []
    parent = destination.parent
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    for folder in reversed(missing):
        folder.mkdir(mode=0o700)
    no_symlink_path(destination.parent)
    info = destination.parent.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise ValueError('Output directory must be owned by you and private (mode 700); '
                         'choose a private directory or use the default output')
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600)
    return destination, descriptor


def add_bytes(archive, name, data, mtime):
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mtime = int(mtime)
    info.mode = 0o600
    archive.addfile(info, io.BytesIO(data))


def collect(base, destination, max_file, max_total):
    base = no_symlink_path(base)
    root_fd = os.open(base, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    archive_path = None
    created = False
    try:
        archive_path, output_fd = prepare_output(destination)
        created = True
        manifest = {'version': 1, 'created_utc': datetime.now(timezone.utc).isoformat(),
                    'max_file_bytes': max_file, 'max_total_bytes': max_total,
                    'files': [], 'skipped': [], 'payload_bytes': 0,
                    'warning': 'May contain account and trading data. Review before sharing. '
                               'No upload was performed. Missing source logs are listed below.'}
        with os.fdopen(output_fd, 'wb') as output, tarfile.open(fileobj=output, mode='w:gz') as archive:
            for relative in sources(base):
                remaining = max_total - manifest['payload_bytes']
                if remaining <= 0:
                    manifest['skipped'].append({'path': str(relative), 'reason': 'total size limit'})
                    continue
                try:
                    with os.fdopen(open_source(root_fd, relative), 'rb') as source:
                        info = os.fstat(source.fileno())
                        # Do not grow the read when a live process appends more output.
                        count = min(info.st_size, max_file, remaining)
                        offset = max(0, info.st_size - count)
                        source.seek(offset)
                        data = source.read(count)
                    add_bytes(archive, 'diagnostics/' + relative.as_posix(), data, info.st_mtime)
                    manifest['files'].append({'path': str(relative), 'source_size': info.st_size,
                                              'included_bytes': len(data), 'tail_offset': offset,
                                              'truncated': offset > 0, 'mtime_ns': info.st_mtime_ns})
                    manifest['payload_bytes'] += len(data)
                except (OSError, ValueError) as error:
                    manifest['skipped'].append({'path': str(relative),
                                                'reason': f'{type(error).__name__}: {error}'})
            add_bytes(archive, 'manifest.json',
                      (json.dumps(manifest, indent=2) + '\n').encode(),
                      datetime.now(timezone.utc).timestamp())
        return archive_path, manifest
    except BaseException:
        if created:
            archive_path.unlink(missing_ok=True)
        raise
    finally:
        os.close(root_fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, default=Path(__file__).absolute().parent)
    parser.add_argument('--output', type=Path, help='new .tar.gz file in a private directory')
    parser.add_argument('--max-file-mib', type=positive_mib, default=5 * MIB)
    parser.add_argument('--max-total-mib', type=positive_mib, default=40 * MIB)
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    destination = args.output or args.base / 'logs/support' / f'quantower-support-{stamp}.tar.gz'
    try:
        archive, manifest = collect(args.base, destination,
                                    args.max_file_mib, args.max_total_mib)
    except (OSError, ValueError, tarfile.TarError) as error:
        print(f'Cannot create support bundle: {error}', file=sys.stderr)
        return 1
    print(f'Created {archive} ({len(manifest["files"])} files, '
          f'{manifest["payload_bytes"]} source bytes).')
    print('WARNING: Archive may contain account/trading data. Review before sharing. No upload performed.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
