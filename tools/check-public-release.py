#!/usr/bin/env python3
"""Audit indexed Git blobs or an exported source tree; never scan private state."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys

PRIVATE_ROOTS = frozenset({
    'prefix', 'logs', 'config', 'cache', 'runtime', 'runtime-components',
    'runner', 'gamescope', 'maintenance', 'installers', 'build', 'dist',
    'downloads', 'local', 'private',
})
FORBIDDEN_SUFFIXES = (
    '.dll', '.exe', '.so', '.ttf', '.woff', '.woff2', '.nettrace', '.slog',
    '.raw.log', '.tar.gz', '.tar.xz', '.zip', '.png', '.jpg', '.jpeg', '.webp',
    '.bmp', '.xwd', '.reg', '.pfx', '.p12', '.key', '.pyc',
)
GENERATED_DIRECTORIES = frozenset({'.git', '__pycache__', '.pytest_cache'})
MAX_SOURCE_BYTES = 4 * 1024 * 1024
PATTERNS = (
    ('personal_home_path', re.compile(rb'/(?:home|Users)/[A-Za-z0-9_.-]+(?:/|\\)')),
    ('private_key', re.compile(rb'-----BEGIN (?:[A-Z0-9]+ )?PRIVATE KEY-----')),
    ('github_credential', re.compile(rb'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b')),
    ('api_credential', re.compile(rb'\bsk-[A-Za-z0-9_-]{32,}\b')),
    ('jwt', re.compile(rb'\beyJ[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\b')),
    ('vendor_html_excerpt', re.compile(rb'<(?:connectionsHolder|icon)\s+id=["\'](?:connection_manager|connections_holder)')),
    ('private_application_report', re.compile(rb'"(?:vendor_serilog_error_records|conservative_reduction_percent|native_call_performance)"\s*:')),
)


@dataclass(frozen=True)
class Source:
    name: str
    data: bytes
    mode: str = '100644'


def path_issues(name):
    path = PurePosixPath(name)
    issues = []
    if path.is_absolute() or '..' in path.parts or not path.parts:
        return ['unsafe_source_path']
    if path.parts[0] in PRIVATE_ROOTS or path.parts[0].startswith('runner-'):
        issues.append('installation_or_private_directory')
    if any(part == 'rollback' for part in path.parts):
        issues.append('rollback_directory')
    lower = path.name.lower()
    if lower in ('launcher-config.json', 'agents.local.md', 'runtime-profile.json', 'application-version.json', 'icon.png', 'amp-quantower-wine.desktop',
                 '.env', 'cookie', 'id_rsa', 'id_ed25519', 'credentials') or lower.startswith('.env.'):
        issues.append('private_or_vendor_file')
    if lower.endswith(FORBIDDEN_SUFFIXES) or '.so.' in lower:
        issues.append('payload_or_generated_file')
    return issues


def inspect(source):
    issues = path_issues(source.name)
    if source.mode not in ('100644', '100755'):
        issues.append('symlink_or_nonregular_source')
    if len(source.data) > MAX_SOURCE_BYTES:
        issues.append('oversized_source')
    elif b'\0' in source.data:
        issues.append('binary_source')
    else:
        issues.extend(label for label, expression in PATTERNS if expression.search(source.data))
    return sorted(set(issues))


def indexed_sources(root):
    result = subprocess.run(['git', '-C', str(root), 'rev-parse', '--show-toplevel'],
                            capture_output=True, text=True)
    if result.returncode or Path(result.stdout.strip()).resolve() != root.resolve():
        raise ValueError('Use this command at a Git repository root, or select --source-tree for an export.')
    result = subprocess.run(['git', '-C', str(root), 'ls-files', '--stage', '-z'],
                            capture_output=True, check=True)
    for entry in result.stdout.split(b'\0'):
        if not entry:
            continue
        metadata, raw_name = entry.split(b'\t', 1)
        mode, oid, stage = metadata.decode('ascii').split()
        name = raw_name.decode('utf-8', errors='strict')
        if stage != '0':
            raise ValueError('Resolve unmerged index entries before a public release.')
        if not re.fullmatch(r'[0-9a-f]{40}(?:[0-9a-f]{24})?', oid):
            raise ValueError('Unexpected Git object identifier.')
        data = b''
        if mode in ('100644', '100755'):
            size = int(subprocess.run(['git', '-C', str(root), 'cat-file', '-s', oid],
                                      capture_output=True, check=True, text=True).stdout)
            if size > MAX_SOURCE_BYTES:
                data = b'X' * (MAX_SOURCE_BYTES + 1)
            else:
                data = subprocess.run(['git', '-C', str(root), 'cat-file', 'blob', oid],
                                      capture_output=True, check=True).stdout
        yield Source(name, data, mode)


def tree_sources(root):
    def visit(directory):
        for path in sorted(directory.iterdir()):
            if path.name in GENERATED_DIRECTORIES:
                continue
            name = path.relative_to(root).as_posix()
            if path.is_symlink():
                yield Source(name, b'', '120000')
            elif path.is_dir():
                yield from visit(path)
            elif path.is_file():
                size = path.stat().st_size
                yield Source(name, path.read_bytes() if size <= MAX_SOURCE_BYTES else b'X' * (MAX_SOURCE_BYTES + 1))
            else:
                yield Source(name, b'', '000000')
    yield from visit(root)


def audit(sources):
    count = 0
    findings = []
    for source in sources:
        count += 1
        issues = inspect(source)
        if issues:
            findings.append({'path': source.name, 'issues': issues})
    if not count:
        findings.append({'path': '.', 'issues': ['empty_source_inventory']})
    return {'result': 'FAIL' if findings else 'PASS', 'files_checked': count,
            'findings': findings,
            'limit': 'Pattern and inventory checks complement human review; they do not prove absence of all secrets or publication restrictions.'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--source-tree', action='store_true', help='audit all regular files of an exported source tree instead of indexed Git blobs')
    args = parser.parse_args(argv)
    try:
        root = args.root.resolve(strict=True)
        if not root.is_dir():
            raise ValueError('Source root must be a directory.')
        result = audit(tree_sources(root) if args.source_tree else indexed_sources(root))
    except (OSError, ValueError, subprocess.CalledProcessError, UnicodeError) as error:
        print(json.dumps({'result': 'FAIL', 'error': str(error)}, indent=2))
        return 2
    print(json.dumps(result, indent=2))
    return 0 if result['result'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
