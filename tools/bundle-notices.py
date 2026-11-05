#!/usr/bin/env python3
"""Check binary payload provenance and its accompanying source archive offline.

This validates declared inventory coverage, notice bytes and source availability;
it is not an automatic legal interpretation of every file's license.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tarfile
import tempfile
from urllib.parse import urlsplit


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def relative(value):
    if (not isinstance(value, str) or not value or '\\' in value or '\0' in value or
            PurePosixPath(value).is_absolute() or any(p in ('', '.', '..') for p in value.split('/'))):
        raise ValueError('Unsafe inventory path.')
    return value


def digest(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{64}', value):
        raise ValueError('Expected a pinned lowercase SHA256.')
    return value


def contained_file(root, name):
    path = root / relative(name)
    if not path.is_file() or not path.resolve().is_relative_to(root):
        raise ValueError('Notice/source input is missing or escapes its supplied root: ' + name)
    return path


def payload_nodes(root):
    payload = root / 'payload'
    if not payload.is_dir() or payload.is_symlink():
        raise ValueError('A staged payload/ directory is required.')
    found = []
    for path in sorted(payload.rglob('*')):
        if path.is_symlink():
            link = os.readlink(path)
            if Path(link).is_absolute() or not path.exists() or not path.resolve().is_relative_to(root):
                raise ValueError('Payload contains a nonrelocatable or escaping symlink.')
        elif path.is_dir():
            continue
        elif not path.is_file():
            raise ValueError('Payload contains a nonregular file.')
        found.append(path.relative_to(root).as_posix())
    if not found:
        raise ValueError('The binary payload is empty.')
    return found


def source_members(archive, prefix, expected):
    """Hash required member bytes without extracting the companion archive."""
    prefix = relative(prefix).rstrip('/') if prefix else ''
    result, small_text = {}, {}
    with tarfile.open(archive, 'r:*') as source:
        members = source.getmembers()
        if len(members) > 200000 or sum(max(0, m.size) for m in members) > 12 * 1024 ** 3:
            raise ValueError('Source companion exceeds its bounded inventory size.')
        seen = set()
        for member in members:
            name = member.name.rstrip('/')
            relative(name)
            if not member.isfile() and not member.isdir():
                raise ValueError('Source companion contains a link or special member.')
            if name in seen:
                raise ValueError('Duplicate source companion member.')
            seen.add(name)
            if prefix:
                if name == prefix and member.isdir():
                    continue
                if not name.startswith(prefix + '/'):
                    raise ValueError('Source companion member is outside its declared root.')
                name = name[len(prefix) + 1:]
            if name not in expected:
                continue
            if not member.isfile():
                raise ValueError('Required source artifact is not a regular archive member.')
            with source.extractfile(member) as stream:
                if name.endswith('.dsc'):
                    if member.size > 2 * 1024 * 1024:
                        raise ValueError('Debian source control file is unexpectedly large.')
                    # Read small controls once: reopening a prior member in a
                    # gzip tar can repeatedly decompress all preceding sources.
                    data = stream.read()
                    actual = hashlib.sha256(data).hexdigest()
                    small_text[name] = data.decode('utf-8')
                else:
                    actual = hashlib.file_digest(stream, 'sha256').hexdigest()
            if actual != expected[name]:
                raise ValueError('Source companion checksum mismatch: ' + name)
            result[name] = {'path': name, 'sha256': actual, 'bytes': member.size}
    missing = set(expected) - set(result)
    if missing:
        raise ValueError('Source companion omits a required artifact: ' + sorted(missing)[0])
    return result, small_text


def debian_source_control(text):
    """Read the control body of plain or clear-signed Debian .dsc text."""
    text = text.replace('\r\n', '\n')
    if text.startswith('-----BEGIN PGP SIGNED MESSAGE-----'):
        sections = text.split('\n\n', 1)
        if len(sections) != 2:
            raise ValueError('Malformed clear-signed Debian source control file.')
        text = sections[1].split('-----BEGIN PGP SIGNATURE-----', 1)[0]
        text = '\n'.join(line[2:] if line.startswith('- ') else line for line in text.splitlines())
    fields, current = {}, None
    for line in text.splitlines():
        if line.startswith((' ', '\t')) and current:
            fields[current] += '\n' + line.strip()
        elif ':' in line:
            current, value = line.split(':', 1)
            if current in fields:
                raise ValueError('Duplicate Debian source control field.')
            fields[current] = value.strip()
        elif not line.strip():
            current = None
    return fields


def validate_inventory(root, inventory, notice_root, source_archive):
    if not isinstance(inventory, dict) or inventory.get('schema_version') != 1:
        raise ValueError('Unsupported notices inventory schema.')
    components = inventory.get('components')
    if not isinstance(components, list) or not components:
        raise ValueError('A nonempty component inventory is required.')
    required_sources, prepared, ids = {}, [], set()
    for component in components:
        if not isinstance(component, dict):
            raise ValueError('Every component record must be an object.')
        key = component.get('id')
        if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:+-]{0,127}', key) or key in ids:
            raise ValueError('Component IDs must be unique safe names.')
        ids.add(key)
        for field in ('name', 'version', 'license'):
            value = component.get(field)
            if not isinstance(value, str) or not value.strip() or value.upper() in ('UNKNOWN', 'NOASSERTION'):
                raise ValueError('Each component requires reviewed name, version and license metadata.')
        covers = component.get('covers')
        if not isinstance(covers, list) or not covers:
            raise ValueError('Each component must cover explicit payload paths.')
        for cover in covers:
            if not isinstance(cover, str):
                raise ValueError('Component covers must be relative path strings.')
            relative(cover[:-1] if cover.endswith('/') else cover)
            if not cover.startswith('payload/'):
                raise ValueError('Component coverage must stay under payload/.')
        notices = component.get('notices')
        if not isinstance(notices, list) or not notices:
            raise ValueError('Each component requires at least one complete notice file.')
        notice_records = []
        for item in notices:
            name, expected = relative(item['path']), digest(item['sha256'])
            original = contained_file(notice_root, name)
            if sha256(original) != expected:
                raise ValueError('Notice checksum mismatch: ' + name)
            notice_records.append({**item, 'path': name, 'sha256': expected, 'bytes': original.stat().st_size})
        for field in ('sources', 'recipes'):
            artifacts = component.get(field)
            if not isinstance(artifacts, list) or not artifacts:
                raise ValueError('Each component requires source artifacts and build/installation recipes.')
            for artifact in artifacts:
                name, expected = relative(artifact['path']), digest(artifact['sha256'])
                if name in required_sources and required_sources[name] != expected:
                    raise ValueError('Conflicting source artifact pin.')
                required_sources[name] = expected
        prepared.append({**component, 'notices': notice_records})
    owners = {}
    nodes = payload_nodes(root)
    for name in nodes:
        matches = []
        for component in prepared:
            scores = [len(cover) for cover in component['covers']
                      if name == cover or (cover.endswith('/') and name.startswith(cover))]
            if scores:
                matches.append((max(scores), component['id']))
        if not matches:
            raise ValueError('A payload file has no source/license component: ' + name)
        best = max(score for score, key in matches)
        winners = {key for score, key in matches if score == best}
        if len(winners) != 1:
            raise ValueError('A payload file has ambiguous component ownership: ' + name)
        owners[name] = winners.pop()
    unused = ids - set(owners.values())
    if unused:
        raise ValueError('A component owns no staged payload files: ' + sorted(unused)[0])
    sources, dsc_text = source_members(source_archive, inventory.get('source_archive_root', ''), required_sources)
    for component in prepared:
        records = component.get('incorporated_debian_sources', [])
        if not isinstance(records, list):
            raise ValueError('Incorporated Debian sources must be a list.')
        if component.get('debian_source'):
            records = [component['debian_source'], *records]
        for debian in records:
            if not isinstance(debian, dict):
                raise ValueError('Debian source identity must be an object.')
            name = relative(debian['dsc'])
            if name not in dsc_text:
                raise ValueError('Debian component requires its actual .dsc source control artifact.')
            fields = debian_source_control(dsc_text[name])
            if fields.get('Source') != debian['package'] or fields.get('Version') != debian['version']:
                raise ValueError('Debian source package/version does not match the inventory.')
            checksums = fields.get('Checksums-Sha256', '').splitlines()
            if not checksums:
                raise ValueError('Debian .dsc lacks its SHA256 source inventory.')
            for line in checksums:
                parts = line.split()
                if not parts:
                    continue
                if len(parts) != 3 or not parts[1].isdigit():
                    raise ValueError('Malformed Debian source checksum entry.')
                expected, size, filename = parts
                relative(filename)
                artifact = (PurePosixPath(name).parent / filename).as_posix()
                if artifact not in sources or sources[artifact]['sha256'] != digest(expected) or sources[artifact]['bytes'] != int(size):
                    raise ValueError('Debian source companion omits or changes a .dsc source member: ' + artifact)
    return prepared, owners, sources


def collect(root, inventory, notice_root, source_archive, output, source_url=None):
    root = Path(root).resolve(strict=True)
    notice_root = Path(notice_root).resolve(strict=True)
    source_archive = Path(source_archive).resolve(strict=True)
    output = Path(output).absolute()
    if not output.resolve().is_relative_to(root) or output.is_symlink() or output.exists():
        raise ValueError('Notices output must be a new directory inside the staged bundle.')
    if source_archive.is_relative_to(root):
        raise ValueError('The source companion must stay outside the binary bundle.')
    offer = root / 'source-offer.json'
    if offer.exists() or offer.is_symlink():
        raise FileExistsError('Existing source companion metadata will not be overwritten.')
    if source_url:
        url = urlsplit(source_url)
        if url.scheme != 'https' or not url.hostname or url.username or url.password:
            raise ValueError('Source companion URL must use credential-free HTTPS.')
    components, owners, sources = validate_inventory(root, inventory, notice_root, source_archive)
    offer_record = {'schema_version': 1, 'archive': source_archive.name,
                    'sha256': sha256(source_archive), 'bytes': source_archive.stat().st_size,
                    'url': source_url, 'archive_root': inventory.get('source_archive_root', ''),
                    'artifacts': dict(sorted(sources.items())),
                    'distribution': 'Accompanying source archive, not a substitute written offer or an automatic legal certification.'}
    manifest = {'schema_version': 1, 'components': components, 'payload_owners': dict(sorted(owners.items())),
                'source_archive': {key: offer_record[key] for key in ('archive', 'sha256', 'bytes', 'url')},
                'validation': 'Payload ownership, pinned notices and actual corresponding-source archive checked offline.',
                'limit': 'Maintainers must review per-file license rights, static/vendor inclusions, source completeness and build instructions.'}
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix='.notices-', dir=output.parent))
    output_created = False
    offer_created = False
    try:
        for component in components:
            for notice in component['notices']:
                target = temporary / 'components' / component['id'] / notice['path']
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(contained_file(notice_root, notice['path']), target)
                notice['bundle_path'] = (output.relative_to(root) / 'components' / component['id'] / notice['path']).as_posix()
        (temporary / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        output.mkdir(mode=0o700)
        output_created = True
        try:
            os.rename(temporary, output)
        except BaseException:
            output.rmdir()
            raise
        with offer.open('x') as stream:
            offer_created = True
            stream.write(json.dumps(offer_record, indent=2) + '\n')
        return manifest, offer_record
    except BaseException:
        if output_created and output.exists():
            shutil.rmtree(output)
        if offer_created:
            offer.unlink(missing_ok=True)
        raise
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path, help='Existing staged binary bundle with payload/.')
    parser.add_argument('--inventory', required=True, type=Path)
    parser.add_argument('--notice-root', required=True, type=Path)
    parser.add_argument('--source-archive', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--source-url')
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        manifest, offer = collect(args.root, json.loads(args.inventory.read_text()), args.notice_root,
                                  args.source_archive, args.output, args.source_url)
    except (OSError, ValueError, KeyError, TypeError, tarfile.TarError) as error:
        parser.exit(1, 'Notices/source check stopped: ' + str(error) + '\n')
    print(json.dumps({'result': 'PASS', 'components': len(manifest['components']),
                      'payload_files_covered': len(manifest['payload_owners']),
                      'source_archive': offer['archive'], 'source_archive_sha256': offer['sha256']}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
