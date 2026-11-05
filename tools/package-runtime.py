#!/usr/bin/env python3
"""Stage and seal a source-audited, relocatable Debian runtime bundle."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
from urllib.parse import urlsplit

sys.dont_write_bytecode = True
import runtime_bundle as bundle

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_NAMES = {'.git', 'prefix', 'logs', 'cache', 'config', 'runtime', 'runtime-components',
                 'runner', 'runner-quantower', 'gamescope', 'maintenance', 'installers', 'build',
                 'dist', 'downloads', 'local', 'private', 'AGENTS.local.md', 'launcher-config.json',
                 'runtime-profile.json', 'application-version.json', 'icon.png', 'amp-quantower-wine.desktop'}
GENERATED = {'__pycache__', '.pytest_cache'}
PAYLOAD_PRIVATE = {'drive_c', 'dosdevices', 'user.reg', 'system.reg', 'userdef.reg',
                   'AMPQuantowerInstalled', 'Starter.exe', 'Quantower.exe', 'WindowsDesktopRuntime.exe',
                   'cookie', '.env', 'id_rsa', 'id_ed25519'}
PAYLOAD_PRIVATE = {name.casefold() for name in PAYLOAD_PRIVATE}


def clean_source_entries(source):
    source = Path(source).resolve(strict=True)
    if not source.is_dir() or (source / bundle.MANIFEST).exists() or (source / 'payload').exists():
        raise ValueError('Toolkit input must be a clean source checkout, not a runtime bundle.')
    audit = source / 'tools/check-public-release.py'
    if audit.is_file():
        result = subprocess.run([sys.executable, '-B', str(audit), '--root', str(source), '--source-tree'],
                                capture_output=True, text=True)
        if result.returncode:
            raise ValueError('Toolkit source inventory audit failed: ' + result.stdout.strip())
    def visit(directory):
        for path in sorted(directory.iterdir()):
            relative = path.relative_to(source)
            if path.name in GENERATED or path.name == '.git':
                continue
            if relative.parts[0] in PRIVATE_NAMES:
                raise ValueError('Private installation state is present in toolkit source: ' + str(relative))
            if path.is_symlink():
                raise ValueError('Toolkit source must not contain symlinks.')
            if path.is_dir():
                yield relative, path
                yield from visit(path)
            elif path.is_file():
                yield relative, path
            else:
                raise ValueError('Toolkit source contains a special filesystem object.')
    return list(visit(source))


def copy_file(source, destination):
    if source.stat().st_mode & 0o7000:
        raise ValueError('Privileged file modes cannot be packaged.')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open('rb') as inp, destination.open('xb') as out:
        shutil.copyfileobj(inp, out)
    destination.chmod(0o755 if source.stat().st_mode & 0o111 else 0o644)


def copy_component(source, destination):
    source = Path(source).expanduser().resolve(strict=True)
    if not source.is_dir():
        raise ValueError('Component input must be a runtime install directory.')
    destination.mkdir(parents=True)
    destination.chmod(0o755)
    for name, path in bundle.walk(source):
        if any(part.casefold() in PAYLOAD_PRIVATE or part == '.git' for part in Path(name).parts):
            raise ValueError('Private/application state cannot be put in a runtime payload: ' + name)
        target = destination / name
        if path.is_symlink():
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(source):
                raise ValueError('Runtime input symlink escapes its component: ' + name)
            final = destination / resolved.relative_to(source)
            target.symlink_to(os.path.relpath(final, target.parent))
        elif path.is_dir():
            if path.stat().st_mode & 0o7000:
                raise ValueError('Privileged directory modes cannot be packaged.')
            target.mkdir()
            target.chmod(0o755)
        elif path.is_file():
            copy_file(path, target)
        else:
            raise ValueError('Special runtime input objects cannot be packaged.')


def validate_inputs(args):
    staging = args.staging.expanduser().absolute()
    if staging.exists() or staging.is_symlink():
        raise FileExistsError('Bundle staging directory must be new.')
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:-[a-zA-Z0-9.-]+)?', args.version):
        raise ValueError('Bundle version must be a semantic version.')
    if args.source_revision and not re.fullmatch(r'[0-9a-f]{40}(?:[0-9a-f]{24})?', args.source_revision):
        raise ValueError('Source revision must be a reviewed Git object identifier.')
    inputs = {}
    for key in ('wine_home', 'gamescope_home', 'x11_home'):
        path = getattr(args, key).expanduser().resolve(strict=True)
        if not path.is_dir() or staging.resolve().is_relative_to(path) or path.is_relative_to(staging.resolve()):
            raise ValueError('Component and staging paths must be separate directories.')
        inputs[key] = path
    if not all((inputs['wine_home'] / 'bin' / name).is_file() and os.access(inputs['wine_home'] / 'bin' / name, os.X_OK)
               for name in ('wine', 'wineserver')):
        raise ValueError('Wine input requires executable bin/wine and bin/wineserver.')
    if not any((inputs['gamescope_home'] / 'bin' / name).is_file() and os.access(inputs['gamescope_home'] / 'bin' / name, os.X_OK)
               for name in ('gamescope', 'gamescope-brokey')):
        raise ValueError('gamescope input requires executable bin/gamescope.')
    if not (inputs['gamescope_home'] / 'bin/Xwayland').is_file():
        raise ValueError('The runtime bundle must provide gamescope/bin/Xwayland.')
    x11_pair = ('libX11.so.6', 'libX11-xcb.so.1')
    if not any(all((directory / name).is_file() for name in x11_pair)
               for directory in (inputs['x11_home'], inputs['x11_home'] / 'lib')):
        raise ValueError('X11 input requires the matched libX11 and libX11-xcb pair.')
    for key in ('dxvk_d3d9', 'assert_trace'):
        value = getattr(args, key)
        path = value.expanduser().resolve(strict=True) if value else None
        if path is not None and not path.is_file():
            raise ValueError('Expected a component file: ' + key)
        inputs[key] = path
    return staging, inputs


def stage(args):
    staging, inputs = validate_inputs(args)
    source = args.toolkit_source.expanduser().resolve(strict=True)
    if staging.resolve().is_relative_to(source) or source.is_relative_to(staging.resolve()):
        raise ValueError('Toolkit source and staging directory must be separate.')
    entries = clean_source_entries(source)
    needed = ('tools/install.py', 'tools/fetch-installers.py', 'tools/setup.py',
              'tools/runtime_bundle.py', 'tools/restore-cef-resources.py',
              'tools/isolate-icon-fonts.py', 'installer-lock.json')
    if any(not (source / name).is_file() for name in needed):
        raise ValueError('Toolkit input is missing a setup source file.')
    staging.mkdir(parents=True, mode=0o700)
    try:
        for relative, path in entries:
            destination = staging / relative
            if path.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                destination.chmod(0o755)
            else:
                copy_file(path, destination)
        components = {}
        for key, relative in (('wine_home', 'payload/wine'), ('gamescope_home', 'payload/gamescope'),
                              ('x11_home', 'payload/x11')):
            copy_component(inputs[key], staging / relative)
            components[key.removesuffix('_home')] = relative
        for key, relative in (('dxvk_d3d9', 'payload/dxvk/d3d9.dll'),
                              ('assert_trace', 'payload/native/libquantower_assert_trace.so')):
            if inputs[key] is not None:
                copy_file(inputs[key], staging / relative)
                components[key] = relative
        # Parent directories created by copy_file also need deterministic modes.
        for name, path in bundle.walk(staging):
            if path.is_dir() and not path.is_symlink():
                path.chmod(0o755)
        script = staging / 'setup.sh'
        if script.exists():
            raise ValueError('Toolkit source may not override the generated bundle setup entry point.')
        script.write_text('#!/usr/bin/env bash\nset -euo pipefail\nbase="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"\nexec python3 -B "$base/tools/setup.py" "$@"\n')
        script.chmod(0o755)
        installer_lock = bundle.read_json(source / 'installer-lock.json')
        application = re.search(r'/versions/v([0-9]+\.[0-9]+\.[0-9]+)/', installer_lock['artifacts']['amp']['url'])
        if application is None:
            raise ValueError('Pinned AMP installer URL must identify an exact application version.')
        record = {'schema_version': 1, 'kind': 'quantower-linux-runtime', 'version': args.version,
                  'application_version': application[1],
                  'platform': args.platform, 'components': components,
                  'archive_root': 'quantower-linux-runtime-' + args.version + '-' + args.platform,
                  'license_manifest': 'licenses/manifest.json', 'source_offer': 'source-offer.json',
                  'host_requirements': {'system': 'Linux', 'architecture': 'x86_64', 'distribution': 'debian',
                                        'distribution_major': '13', 'glibc_minimum': '2.41',
                                        'graphics_drivers': 'Host graphics drivers are required; GPU compatibility must be tested individually.'},
                  'ai_generated': True, 'individual_demo_testing_required': True,
                  'toolkit_source_revision': args.source_revision,
                  'proprietary_application_included': False}
        bundle.write_json(staging / 'bundle-build.json', record)
        return {'mode': 'staged', 'staging': str(staging), 'version': args.version,
                'next_step': 'Generate licenses/manifest.json and source-offer.json, then run package-runtime.py seal.'}
    except BaseException:
        shutil.rmtree(staging)
        raise


def validate_licenses(staging, files, source_archive):
    licenses = bundle.read_json(staging / 'licenses/manifest.json')
    offer = bundle.read_json(staging / 'source-offer.json')
    if licenses.get('schema_version') != 1 or offer.get('schema_version') != 1:
        raise ValueError('Runtime license/source metadata is unsupported.')
    nodes = {name for name, item in files.items() if name.startswith('payload/') and item['type'] != 'directory'}
    owners = licenses.get('payload_owners')
    if not isinstance(owners, dict) or set(owners) != nodes or any(not isinstance(x, str) or not x for x in owners.values()):
        raise ValueError('Every runtime payload file/link must have exactly one declared license/source owner.')
    components = licenses.get('components')
    if not isinstance(components, list) or not components:
        raise ValueError('Runtime license components are required.')
    identifiers = set()
    for component in components:
        key = component.get('id') if isinstance(component, dict) else None
        if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:+-]{0,127}', key) or key in identifiers:
            raise ValueError('Runtime license component identities must be unique.')
        identifiers.add(key)
        notices = component.get('notices')
        if not isinstance(notices, list) or not notices:
            raise ValueError('Complete component notices must accompany the runtime.')
        for notice in notices:
            if not isinstance(notice, dict):
                raise ValueError('Component notice record must be an object.')
            name = 'licenses/components/' + key + '/' + str(notice.get('path', ''))
            bundle.safe_relative(name)
            entry = files.get(name, {})
            if entry.get('type') != 'file' or entry['sha256'] != notice.get('sha256') or entry['size'] != notice.get('bytes'):
                raise ValueError('Component notice is missing or changed: ' + name)
    if set(owners.values()) - identifiers:
        raise ValueError('Payload owner has no component notice record.')
    archive = Path(source_archive).expanduser().resolve(strict=True)
    if not archive.is_file() or archive.is_relative_to(staging):
        raise ValueError('Corresponding-source companion must be a separate archive file.')
    if (offer.get('archive') != archive.name or offer.get('bytes') != archive.stat().st_size or
            offer.get('sha256') != bundle.sha256(archive)):
        raise ValueError('Corresponding-source archive does not match the recorded source offer.')
    url = urlsplit(str(offer.get('url', '')))
    if url.scheme != 'https' or not url.netloc or url.username or url.password or url.fragment:
        raise ValueError('A public HTTPS URL for the matching source companion is required before sealing.')
    if not isinstance(offer.get('artifacts'), dict) or not offer['artifacts']:
        raise ValueError('Corresponding-source archive artifact inventory is required.')
    if any(not isinstance(item, dict) for item in offer['artifacts'].values()):
        raise ValueError('Source artifact records must be objects.')
    expected_artifacts = {name: item.get('sha256') for name, item in offer['artifacts'].items()}
    spec = importlib.util.spec_from_file_location('_bundle_notices_for_seal', ROOT / 'tools/bundle-notices.py')
    notices_tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(notices_tool)
    actual_artifacts, _ = notices_tool.source_members(archive, offer.get('archive_root', ''), expected_artifacts)
    if actual_artifacts != offer['artifacts']:
        raise ValueError('Corresponding-source member inventory has changed.')
    expected = {key: offer.get(key) for key in ('archive', 'sha256', 'bytes', 'url')}
    if licenses.get('source_archive') != expected:
        raise ValueError('License manifest and source offer identify different source archives.')
    return offer


def seal(args):
    staging = args.staging.expanduser().resolve(strict=True)
    if not staging.is_dir() or (staging / bundle.MANIFEST).exists():
        raise ValueError('Seal requires an unsealed staged bundle directory.')
    record = bundle.read_json(staging / 'bundle-build.json')
    for name, path in bundle.walk(staging):
        if not path.is_symlink():
            if path.stat().st_mode & 0o7000:
                raise ValueError('Privileged bundle modes cannot be sealed.')
            path.chmod(0o755 if path.is_dir() or path.stat().st_mode & 0o111 else 0o644)
    files = bundle.inventory(staging)
    offer = validate_licenses(staging, files, args.source_archive)
    record['files'] = files
    record['source_archive'] = {key: offer.get(key) for key in ('archive', 'sha256', 'bytes', 'url')}
    bundle.validate_manifest(record)
    bundle.write_json(staging / bundle.MANIFEST, record)
    try:
        output = bundle.archive_bundle(staging, args.output, epoch=args.source_date_epoch)
    except BaseException:
        (staging / bundle.MANIFEST).unlink(missing_ok=True)
        raise
    return {'mode': 'sealed', 'archive': str(output), 'sha256': bundle.sha256(output),
            'source_archive': offer['archive'], 'source_sha256': offer['sha256'],
            'entries': len(files), 'platform': record['platform']}


def verify(args):
    if args.sha256 and (not re.fullmatch(r'[0-9a-f]{64}', args.sha256) or bundle.sha256(args.archive) != args.sha256):
        raise ValueError('Runtime archive checksum mismatch.')
    result = bundle.verify_archive(args.archive)
    return {'mode': 'verified', 'version': result['version'], 'platform': result['platform'],
            'entries_verified': len(result['files']), 'writes_performed': False}


def extract(args):
    result = bundle.extract_bundle(args.archive, args.destination, expected_sha256=args.sha256)
    return {'mode': 'extracted', 'destination': str(args.destination.resolve()),
            'version': result['version'], 'entries_verified': len(result['files'])}


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    commands = p.add_subparsers(dest='command', required=True)
    s = commands.add_parser('stage', help='Copy source and runtime inputs into a new staging tree.')
    s.add_argument('--staging', required=True, type=Path)
    s.add_argument('--toolkit-source', type=Path, default=ROOT)
    s.add_argument('--wine-home', required=True, type=Path)
    s.add_argument('--gamescope-home', required=True, type=Path)
    s.add_argument('--x11-home', required=True, type=Path)
    s.add_argument('--dxvk-d3d9', required=True, type=Path)
    s.add_argument('--assert-trace', type=Path)
    s.add_argument('--version', required=True)
    s.add_argument('--platform', choices=('debian13-x86_64',), default='debian13-x86_64')
    s.add_argument('--source-revision', help='Optional reviewed toolkit Git commit; no host paths are recorded.')
    s.set_defaults(action=stage)
    s = commands.add_parser('seal', help='Verify licenses/source coverage and create an archive.')
    s.add_argument('--staging', required=True, type=Path)
    s.add_argument('--source-archive', required=True, type=Path)
    s.add_argument('--output', required=True, type=Path)
    s.add_argument('--source-date-epoch', type=int, default=0)
    s.set_defaults(action=seal)
    s = commands.add_parser('verify', help='Verify an archive without extracting or executing it.')
    s.add_argument('--archive', required=True, type=Path)
    s.add_argument('--sha256')
    s.set_defaults(action=verify)
    s = commands.add_parser('extract', help='Verify and extract into a new destination; never execute code.')
    s.add_argument('--archive', required=True, type=Path)
    s.add_argument('--destination', required=True, type=Path)
    s.add_argument('--sha256', required=True)
    s.set_defaults(action=extract)
    return p


def main(argv=None):
    p = parser()
    args = p.parse_args(argv)
    try:
        print(json.dumps(args.action(args), indent=2))
    except (OSError, ValueError, KeyError, TypeError) as error:
        p.exit(1, 'Runtime packaging stopped: ' + str(error) + '\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
