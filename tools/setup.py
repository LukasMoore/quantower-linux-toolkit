#!/usr/bin/env python3
"""Verify a prebuilt runtime bundle, then plan, stage, or install a new target."""
from __future__ import annotations

import argparse
import ctypes.util
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import signal
import stat
import sys

sys.dont_write_bytecode = True
import runtime_bundle as bundle

ROOT = Path(__file__).resolve().parents[1]


def load_tool(root, name, module_name):
    path = root / 'tools' / name
    if not path.is_file():
        raise ValueError('The runtime bundle is missing setup code: ' + name)
    sys.path.insert(0, str(root))
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def os_release(path=Path('/etc/os-release')):
    result = {}
    try:
        for line in path.read_text().splitlines():
            if '=' not in line or line.startswith('#'):
                continue
            key, value = line.split('=', 1)
            result[key] = value.strip().strip('"').strip("'")
    except OSError:
        pass
    return result


def host_checks(*, environment=None, release=None, system=None, machine=None, libc=None):
    environment = os.environ if environment is None else environment
    release = os_release() if release is None else release
    system = platform.system() if system is None else system
    machine = platform.machine().lower() if machine is None else machine.lower()
    libc = platform.libc_ver() if libc is None else libc
    match = re.fullmatch(r'(\d+)\.(\d+)(?:\.\d+)?', libc[1])
    libc_okay = libc[0].lower() in ('glibc', 'gnu libc') and match and tuple(map(int, match.groups())) >= (2, 41)
    checks = [
        {'name': 'system', 'okay': system == 'Linux', 'detail': 'Linux is required.'},
        {'name': 'architecture', 'okay': machine in ('x86_64', 'amd64'), 'detail': 'The bundle contains x86-64 Linux binaries and i386/x86-64 Windows modules.'},
        {'name': 'distribution', 'okay': release.get('ID') == 'debian' and release.get('VERSION_ID', '').split('.')[0] == '13',
         'detail': 'The supported baseline is Debian 13; other distributions have not been validated.'},
        {'name': 'glibc', 'okay': bool(libc_okay), 'detail': 'glibc 2.41 or newer is required; no libc or GPU driver is installed by setup.'},
        {'name': 'python', 'okay': sys.version_info >= (3, 11), 'detail': 'Python 3.11 or newer is required.'},
        {'name': 'pidfd', 'okay': hasattr(os, 'pidfd_open') and hasattr(signal, 'pidfd_send_signal'),
         'detail': 'PID file-descriptor support is required for guarded cleanup.'},
    ]
    return checks


def choose_profile(requested, *, environment=None, find_library=ctypes.util.find_library):
    if requested not in ('auto', 'compatible', 'gpu'):
        raise ValueError('Unknown rendering profile.')
    if requested != 'auto':
        return requested
    environment = os.environ if environment is None else environment
    runtime = environment.get('XDG_RUNTIME_DIR') or '/run/user/' + str(os.getuid())
    display = environment.get('WAYLAND_DISPLAY')
    path = Path(display) if display and Path(display).is_absolute() else Path(runtime) / (display or 'wayland-0')
    try:
        wayland = path.exists() and stat.S_ISSOCK(path.stat().st_mode)
    except OSError:
        wayland = False
    # This selects an available launch path, not a graphics compatibility claim.
    return 'gpu' if wayland and find_library('vulkan') else 'compatible'


def installation_options(root, manifest, target, profile):
    components = manifest['components']
    args = ['--target', str(target), '--profile', profile,
            '--application-version', manifest['application_version'],
            '--wine-home', str(root / components['wine']),
            '--gamescope-home', str(root / components['gamescope']),
            '--x11-home', str(root / components['x11']),
            '--dxvk-d3d9', str(root / components['dxvk_d3d9'])]
    if components.get('assert_trace'):
        args += ['--assert-trace', str(root / components['assert_trace'])]
    return args


def execute(args, *, installer=None, fetcher=None, cef_helper=None, font_helper=None, environment=None, checks=None):
    environment = dict(os.environ if environment is None else environment)
    root = args.bundle_root.expanduser().resolve(strict=True)
    manifest = bundle.verify_bundle(root)
    checks = host_checks(environment=environment) if checks is None else checks
    if args.verify:
        return {'mode': 'verified', 'version': manifest['version'], 'platform': manifest['platform'],
                'entries_verified': len(manifest['files']), 'host_checks': checks, 'writes_performed': False}
    target = args.target.expanduser().resolve()
    if target.is_relative_to(root) or root.is_relative_to(target):
        raise ValueError('Installation target must be outside the downloaded runtime bundle.')
    profile = choose_profile(args.profile, environment=environment)
    if args.repair_icons and args.stage:
        raise ValueError('--repair-icons requires a completed application installation; use it with --install.')
    installer = installer or load_tool(root, 'install.py', '_runtime_bundle_installer')
    options = installation_options(root, manifest, target, profile)
    # Validate new-target/layout prerequisites before downloading anything.
    validated = installer.parser().parse_args([*options, '--plan'])
    plan = installer.assemble(validated)
    lock = bundle.read_json(root / 'installer-lock.json')
    if lock.get('schema_version') != 1 or not isinstance(lock.get('artifacts'), dict):
        raise ValueError('The runtime bundle has no valid pinned installer lock.')
    if not {'amp', 'desktop', 'cef'}.issubset(lock['artifacts']):
        raise ValueError('The runtime bundle installer lock must pin AMP, .NET Desktop Runtime and matching CEF resources.')
    info = {'version': manifest['version'], 'platform': manifest['platform'], 'profile': profile,
            'target': str(target), 'host_checks': checks,
            'downloads': [{'artifact': key, 'url': value['url'], 'algorithm': value['algorithm'], 'digest': value['digest']}
                          for key, value in lock['artifacts'].items()],
            'source_archive': manifest['source_archive'],
            'repair_icons_requested': args.repair_icons,
            'ai_generated': True, 'individual_demo_testing_required': True}
    if not args.install and not args.stage:
        return {**info, 'mode': 'plan', 'writes_performed': False,
                'next_step': 'Review this plan. Use --stage for offline runtime staging, or --install for pinned installer downloads and interactive setup.'}
    if args.stage:
        staged = installer.assemble(installer.parser().parse_args(options))
        return {**info, 'mode': 'staged', 'writes_performed': True, 'installation': staged,
                'next_step': 'Runtime staged without downloads or Wine execution. Manual application setup remains.'}
    failures = [entry['name'] for entry in checks if not entry['okay']]
    if failures and not args.allow_unsupported_host:
        raise ValueError('Host baseline checks failed: ' + ', '.join(failures) + '; no installers were downloaded or run.')
    if not environment.get('DISPLAY'):
        raise ValueError('Windows installer UI requires a reachable DISPLAY, including on the GPU profile. Use a desktop Xwayland session or an explicit test X server.')
    extractor = root / manifest['components']['wine'] / 'bin/innoextract'
    if not extractor.is_file() or not os.access(extractor, os.X_OK):
        raise ValueError('The runtime bundle is missing its executable bin/innoextract setup helper.')
    fetcher = fetcher or load_tool(root, 'fetch-installers.py', '_runtime_bundle_fetcher')
    cache = args.cache.expanduser().resolve()
    if cache.is_relative_to(root):
        raise ValueError('Installer cache must be outside the downloaded runtime bundle.')

    def offline(request, timeout):
        raise FileNotFoundError('A pinned installer is absent from the offline cache.')

    pinned = {}
    for key in ('amp', 'desktop', 'cef'):
        if key not in lock['artifacts']:
            raise ValueError('Pinned installer is missing from the lock: ' + key)
        item = lock['artifacts'][key]
        path = fetcher.fetch(item, cache, opener=offline) if args.offline else fetcher.fetch(item, cache)
        pinned[key] = (path, fetcher.digest(path, 'sha256'))
    options += ['--app-installer', str(pinned['amp'][0]), '--app-installer-sha256', pinned['amp'][1],
                '--desktop-runtime-installer', str(pinned['desktop'][0]), '--desktop-runtime-sha256', pinned['desktop'][1],
                '--initialize-prefix', '--run-installers', '--extract-app']
    result = installer.assemble(installer.parser().parse_args(options))
    if result.get('setup_cleanup_complete') is not True:
        raise RuntimeError('Application setup completed without confirmed guarded cleanup. Its processes were preserved; close this installation before post-install repairs or launch.')
    cef_helper = cef_helper or load_tool(root, 'restore-cef-resources.py', '_runtime_bundle_cef_resources')
    result['cef_resource_restore'] = cef_helper.apply(target, pinned['cef'][0])
    if args.repair_icons:
        font_helper = font_helper or load_tool(root, 'isolate-icon-fonts.py', '_runtime_bundle_icon_fonts')
        result['icon_font_repair'] = font_helper.apply(target)
    return {**info, 'mode': 'installed', 'writes_performed': True, 'installation': result,
            'next_step': 'Run doctor.py and launch.sh inside the new target. Test your own demo installation before relying on it.'}


def parser():
    default_data = Path(os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local/share')))
    default_cache = Path(os.environ.get('XDG_CACHE_HOME', str(Path.home() / '.cache')))
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bundle-root', type=Path, default=ROOT, help='An extracted, sealed runtime bundle root.')
    p.add_argument('--target', type=Path, default=default_data / 'quantower-linux')
    p.add_argument('--cache', type=Path, default=default_cache / 'quantower-linux/installers')
    p.add_argument('--profile', choices=('auto', 'compatible', 'gpu'), default='auto')
    modes = p.add_mutually_exclusive_group()
    modes.add_argument('--plan', action='store_true', help='Verify and print the setup plan without writes; this is the default.')
    modes.add_argument('--verify', action='store_true', help='Verify bundle integrity and report host checks only.')
    modes.add_argument('--stage', action='store_true', help='Stage the runtime into a new target offline; do not execute Wine.')
    modes.add_argument('--install', action='store_true', help='Download pinned publisher installers and run explicit new-prefix setup.')
    p.add_argument('--offline', action='store_true', help='During --install use only verified pre-existing installer cache entries.')
    p.add_argument('--repair-icons', action='store_true', help='After clean --install, explicitly opt in to local icon-font identity repair; use only assets you may modify.')
    p.add_argument('--allow-unsupported-host', action='store_true', help='Explicit experimental opt-in when the documented Debian baseline check fails.')
    return p


def main(argv=None):
    os.umask(0o077)
    p = parser()
    try:
        result = execute(p.parse_args(argv))
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as error:
        p.exit(1, 'Runtime setup stopped: ' + str(error) + '\n')
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
