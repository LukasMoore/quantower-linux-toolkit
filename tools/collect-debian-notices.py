#!/usr/bin/env python3
"""Collect exact Debian package/source provenance for selected bundled files.

The caller supplies an exported build root and already-downloaded source packages.
No package is installed, no network access is used, and the build root is read-only.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tempfile

SPEC = importlib.util.spec_from_file_location('bundle_notices', Path(__file__).with_name('bundle-notices.py'))
notices = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(notices)


def rooted(root, system_path):
    """Resolve Unix absolute links inside the exported root, never the host root."""
    if (not isinstance(system_path, str) or not system_path.startswith('/') or '\\' in system_path or
            any(ord(c) < 32 for c in system_path)):
        raise ValueError('Expected an absolute Unix build-root path.')
    pending = list(PurePosixPath(system_path).parts[1:])
    actual, links = [], 0
    while pending:
        part = pending.pop(0)
        if part in ('', '.'):
            continue
        if part == '..':
            if not actual:
                raise ValueError('Build-root path escapes its root.')
            actual.pop()
            continue
        candidate = root.joinpath(*actual, part)
        if candidate.is_symlink():
            links += 1
            if links > 40:
                raise ValueError('Build-root symlink loop.')
            link = PurePosixPath(os.readlink(candidate))
            if link.is_absolute():
                actual = []
                pending = list(link.parts[1:]) + pending
            else:
                pending = list(link.parts) + pending
        else:
            actual.append(part)
    return root.joinpath(*actual)


def package_for_file(root, system_path, runner=subprocess.run):
    resolved = rooted(root, system_path)
    if not resolved.is_file():
        raise ValueError('Bundled dependency input is not a build-root file.')
    canonical = '/' + resolved.relative_to(root).as_posix()
    candidates = [system_path, canonical]
    for name in list(candidates):
        if name.startswith(('/usr/lib/', '/usr/bin/', '/usr/sbin/')):
            candidates.append(name[4:])
        elif name.startswith(('/lib/', '/bin/', '/sbin/')):
            candidates.append('/usr' + name)
    owners = set()
    for name in dict.fromkeys(candidates):
        if any(c in name for c in '*?[]'):
            raise ValueError('Package lookup paths cannot contain glob metacharacters.')
        result = runner(['dpkg-query', '--admindir=' + str(root / 'var/lib/dpkg'), '-S', name],
                        capture_output=True, text=True)
        if result.returncode:
            continue
        for line in result.stdout.splitlines():
            if ': ' not in line:
                continue
            package, path = line.rsplit(': ', 1)
            if path != name or not re.fullmatch(r'[a-z0-9][a-z0-9+.-]*(?::[a-z0-9-]+)?', package):
                continue
            owners.add(package)
    if len(owners) != 1:
        raise ValueError('Expected one exact Debian package owner for ' + system_path)
    package = owners.pop()
    result = runner(['dpkg-query', '--admindir=' + str(root / 'var/lib/dpkg'), '-W',
                     '-f=${binary:Package}\t${Version}\t${source:Package}\t${source:Version}\n', package],
                    capture_output=True, text=True, check=True)
    rows = result.stdout.strip().splitlines()
    if len(rows) != 1 or len(rows[0].split('\t')) != 4:
        raise ValueError('Malformed Debian package metadata response.')
    binary, version, source, source_version = rows[0].split('\t')
    if binary != package or not version or not source or not source_version:
        raise ValueError('Incomplete Debian binary/source package metadata.')
    return {'binary_package': binary, 'binary_version': version,
            'source_package': source, 'source_version': source_version}


def source_packages(directory):
    result = {}
    for path in sorted(directory.glob('*.dsc')):
        if path.is_symlink():
            raise ValueError('Debian source control input must not be a symlink.')
        if path.stat().st_size > 2 * 1024 * 1024:
            raise ValueError('Debian source control input is unexpectedly large.')
        fields = notices.debian_source_control(path.read_text())
        key = (fields.get('Source'), fields.get('Version'))
        if not all(key) or key in result:
            raise ValueError('Ambiguous Debian source package control metadata.')
        artifacts = [path]
        for line in fields.get('Checksums-Sha256', '').splitlines():
            if not line.strip():
                continue
            parts = line.split()
            if len(parts) != 3 or not parts[1].isdigit():
                raise ValueError('Malformed Debian source artifact checksum.')
            expected, size, name = parts
            if Path(name).name != name or name in ('.', '..'):
                raise ValueError('Debian source artifact filename must be a basename.')
            actual = directory / name
            if (actual.is_symlink() or not actual.is_file() or actual.stat().st_size != int(size) or
                    notices.sha256(actual) != notices.digest(expected)):
                raise ValueError('Missing or changed Debian source artifact: ' + name)
            artifacts.append(actual)
        if len(artifacts) == 1:
            raise ValueError('Debian source control file has no source artifacts.')
        result[key] = artifacts
    return result


def collect(rootfs, mappings, debian_sources, output, runner=subprocess.run):
    rootfs = Path(rootfs).resolve(strict=True)
    debian_sources = Path(debian_sources).resolve(strict=True)
    output = Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise FileExistsError('Notice collection output must be new.')
    if not isinstance(mappings, list) or not mappings:
        raise ValueError('A nonempty payload/system-path mapping list is required.')
    packages, payload_seen = {}, set()
    for mapping in mappings:
        payload = notices.relative(mapping['path'])
        if not payload.startswith('payload/') or payload in payload_seen:
            raise ValueError('Dependency payload paths must be unique and under payload/.')
        payload_seen.add(payload)
        metadata = package_for_file(rootfs, mapping['system_path'], runner)
        if 'sha256' in mapping and notices.sha256(rooted(rootfs, mapping['system_path'])) != notices.digest(mapping['sha256']):
            raise ValueError('Copied dependency hash differs from its Debian package input: ' + payload)
        key = metadata['binary_package']
        if key not in packages:
            packages[key] = {**metadata, 'covers': []}
        if any(packages[key][field] != metadata[field] for field in metadata):
            raise ValueError('Inconsistent binary package provenance.')
        packages[key]['covers'].append(payload)
    sources = source_packages(debian_sources)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix='.debian-notices-', dir=output.parent))
    components = []
    try:
        for key, package in sorted(packages.items()):
            source_key = (package['source_package'], package['source_version'])
            if source_key not in sources:
                raise ValueError('Exact Debian source package is unavailable: ' + ' '.join(source_key))
            original = rooted(rootfs, '/usr/share/doc/' + key.split(':')[0] + '/copyright')
            if not original.is_file():
                raise ValueError('Package copyright notice is missing: ' + key)
            relative = 'debian/' + key + '/copyright'
            target = temporary / relative
            target.parent.mkdir(parents=True)
            shutil.copyfile(original, target)
            notice_items = [{'path': relative, 'sha256': notices.sha256(target)}]
            text = original.read_text(errors='replace')
            common = set(re.findall(r'/usr/share/common-licenses/([A-Za-z0-9][A-Za-z0-9_.+-]*)', text))
            for name in sorted(common):
                referenced_name = name
                source = rooted(rootfs, '/usr/share/common-licenses/' + name)
                if not source.is_file():
                    name = name.rstrip('.')
                    source = rooted(rootfs, '/usr/share/common-licenses/' + name)
                # Debian copyright files sometimes use SPDX's ".0" spelling
                # although base-files names those same full texts GPL-3 etc.
                # Normalize only these known version-equivalent spellings.
                if not source.is_file() and name in {'GPL-1.0', 'GPL-2.0', 'GPL-3.0', 'LGPL-2.0', 'LGPL-3.0'}:
                    name = name[:-2]
                    source = rooted(rootfs, '/usr/share/common-licenses/' + name)
                if not source.is_file():
                    raise ValueError('A copyright notice references an unavailable common license: ' + name)
                relative = 'debian/common-licenses/' + name
                target = temporary / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists() and target.read_bytes() != source.read_bytes():
                    raise ValueError('Conflicting common-license bytes.')
                shutil.copyfile(source, target)
                item = {'path': relative, 'sha256': notices.sha256(target)}
                if referenced_name.rstrip('.') != name:
                    item['copyright_reference'] = '/usr/share/common-licenses/' + referenced_name.rstrip('.')
                notice_items.append(item)
            artifacts = [{'path': 'debian/' + path.name, 'sha256': notices.sha256(path)}
                         for path in sources[source_key]]
            dsc = next(path.name for path in sources[source_key] if path.suffix == '.dsc')
            component = {'id': 'debian-' + key, 'name': key, 'version': package['binary_version'],
                         'license': 'LicenseRef-Debian-copyright', 'covers': sorted(package['covers']),
                         'notices': notice_items, 'sources': artifacts,
                         'recipes': [item for item in artifacts if not item['path'].endswith('.dsc')],
                         'debian_source': {'package': source_key[0], 'version': source_key[1], 'dsc': 'debian/' + dsc},
                         'package_provenance': {field: package[field] for field in
                                                ('binary_package', 'binary_version', 'source_package', 'source_version')},
                         'declared_license_labels': sorted(set(re.findall(r'(?mi)^License:\s*(.+)$', text))),
                         'review_note': 'The complete copied copyright notice is authoritative; maintainers must review all license terms before distribution.'}
            components.append(component)
        (temporary / 'components.json').write_text(json.dumps({'schema_version': 1, 'components': components}, indent=2) + '\n')
        output.mkdir(mode=0o700)
        try:
            os.rename(temporary, output)
        except BaseException:
            output.rmdir()
            raise
        temporary = None
        return components
    finally:
        if temporary is not None:
            shutil.rmtree(temporary)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rootfs', type=Path, required=True)
    parser.add_argument('--files-json', type=Path, required=True,
                        help='JSON mapping list, or collect-runtime-libs.py report with a files list.')
    parser.add_argument('--debian-sources', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        mappings = json.loads(args.files_json.read_text())
        if isinstance(mappings, dict):
            mappings = mappings.get('files')
        components = collect(args.rootfs, mappings, args.debian_sources, args.output)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        parser.exit(1, 'Debian notice collection stopped: ' + str(error) + '\n')
    print(json.dumps({'result': 'PASS', 'binary_packages': len(components),
                      'source_packages': len({(c['debian_source']['package'], c['debian_source']['version']) for c in components})}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
