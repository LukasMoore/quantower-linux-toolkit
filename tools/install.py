#!/usr/bin/env python3
"""Assemble a new isolated installation from user-supplied runtime components."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import profile_manager
import runtime_config
import supervisor

SOURCE_FILES = ('launch.sh', 'render-launch.sh', 'application_runtime.py', 'diagnostics.py',
                'profile_manager.py', 'runtime_config.py', 'supervisor.py', 'support-bundle.py',
                'select-profile.py', 'doctor.py', 'gpu-dxvk.conf', 'AGENTS.md', 'README.md',
                'SECURITY.md', 'CONTRIBUTING.md', 'launcher-config.example.json', 'LICENSE', 'THIRD_PARTY.md')

# Fresh-prefix compatibility mappings use unmodified open fonts, not Microsoft
# font bytes. Only enable a family when all four bundled styles are available.
FONT_FALLBACKS = (
    ('DejaVu Sans', ('DejaVuSans.ttf', 'DejaVuSans-Bold.ttf', 'DejaVuSans-Oblique.ttf', 'DejaVuSans-BoldOblique.ttf'),
     ('Verdana', 'Arial', 'Microsoft Sans Serif', 'Segoe UI')),
    ('DejaVu Serif', ('DejaVuSerif.ttf', 'DejaVuSerif-Bold.ttf', 'DejaVuSerif-Italic.ttf', 'DejaVuSerif-BoldItalic.ttf'),
     ('Times New Roman',)),
    ('DejaVu Sans Mono', ('DejaVuSansMono.ttf', 'DejaVuSansMono-Bold.ttf', 'DejaVuSansMono-Oblique.ttf', 'DejaVuSansMono-BoldOblique.ttf'),
     ('Courier New', 'Consolas')),
)


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def validate_directory(path):
    path = Path(path).expanduser().resolve(strict=True)
    if not path.is_dir():
        raise ValueError('Expected a runtime directory.')
    for entry in path.rglob('*'):
        if entry.is_symlink() and not entry.resolve(strict=True).is_relative_to(path):
            raise ValueError('Runtime contains a symlink outside its supplied directory: ' + str(entry.relative_to(path)))
    return path


def copy_tree(source, destination):
    """Keep relocatable in-tree links; reject external links and rewrite absolute ones."""
    source = validate_directory(source)
    shutil.copytree(source, destination, symlinks=True)
    for entry in destination.rglob('*'):
        if entry.is_symlink() and Path(os.readlink(entry)).is_absolute():
            original = source / entry.relative_to(destination)
            relative = original.resolve(strict=True).relative_to(source)
            entry.unlink()
            entry.symlink_to(os.path.relpath(destination / relative, entry.parent))


def find_x11(source, name):
    candidates = [source / name, source / 'lib' / name, source / 'usr/lib' / name,
                  source / 'lib64' / name, source / 'usr/lib64' / name]
    for item in candidates:
        if item.is_file() and item.resolve().is_relative_to(source):
            return item
    raise ValueError('The matched X11 runtime is missing ' + name)


def installer_pin(path, expected):
    if path is None:
        if expected is not None:
            raise ValueError('An installer digest requires its installer path.')
        return None
    path = Path(path).expanduser().resolve(strict=True)
    if not path.is_file() or not expected or not re.fullmatch(r'[a-fA-F0-9]{64}', expected):
        raise ValueError('Each supplied installer requires its expected --...-sha256 digest.')
    if sha(path) != expected.lower():
        raise ValueError('Supplied installer checksum does not match its expected digest.')
    return path


def validate(args):
    if args.target.expanduser().is_symlink():
        raise ValueError('Installation target must be a new path, not a symlink.')
    # Canonicalize parent symlinks now; process ownership later compares the
    # exact WINEPREFIX string with this path and must not inherit an alias.
    target = args.target.expanduser().resolve()
    if target.exists() or target.is_symlink():
        raise ValueError('Installation target must be a new path; existing installations are never modified.')
    if ROOT.is_relative_to(target.resolve()):
        raise ValueError('Installation target cannot contain this source checkout.')
    if any((parent / 'user.reg').is_file() and (parent / 'drive_c').is_dir() for parent in target.parents):
        raise ValueError('Installation target must be outside existing Wine prefixes.')
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', args.application_version):
        raise ValueError('Application version must have three numeric components.')
    wine = validate_directory(args.wine_home)
    for name in ('wine', 'wineserver'):
        file = wine / 'bin' / name
        if not file.is_file() or not os.access(file, os.X_OK):
            raise ValueError('Wine home must contain executable bin/wine and bin/wineserver.')
    if args.profile == 'gpu' and (not args.gamescope_home or not args.dxvk_d3d9):
        raise ValueError('GPU profile requires both --gamescope-home and --dxvk-d3d9.')
    if args.profile == 'gpu':
        runtime_config.validate_gpu_runtime_path(target)
    if args.run_installers and not args.initialize_prefix:
        raise ValueError('--run-installers requires --initialize-prefix for this new installation.')
    if args.extract_app and not args.run_installers:
        raise ValueError('--extract-app requires --run-installers.')
    if args.extract_app and not (wine / 'bin/innoextract').is_file():
        raise ValueError('--extract-app requires bundled bin/innoextract.')
    app = installer_pin(args.app_installer, args.app_installer_sha256)
    desktop = installer_pin(args.desktop_runtime_installer, args.desktop_runtime_sha256)
    if args.run_installers and (app is None or desktop is None):
        raise ValueError('--run-installers requires both pinned user-supplied installers.')
    extras = {}
    for key in ('gamescope_home', 'x11_home'):
        path = getattr(args, key)
        extras[key] = validate_directory(path) if path else None
    for key in ('dxvk_d3d9', 'assert_trace'):
        path = getattr(args, key)
        if path:
            path = path.expanduser().resolve(strict=True)
            if not path.is_file():
                raise ValueError('Expected a regular component file: ' + key)
        extras[key] = path
    for source in (wine, extras['gamescope_home'], extras['x11_home']):
        if source and (target.resolve().is_relative_to(source) or source.is_relative_to(target.resolve())):
            raise ValueError('Installation target and runtime source directories must be separate.')
    if extras['x11_home']:
        for name in ('libX11.so.6', 'libX11-xcb.so.1'):
            find_x11(extras['x11_home'], name)
    if extras['gamescope_home']:
        home = extras['gamescope_home']
        if (home / 'usr/bin/gamescope').is_file():
            home = home / 'usr'
        if not any(p.is_file() and os.access(p, os.X_OK) for p in
                   (home / 'bin/gamescope', home / 'bin/gamescope-brokey')):
            raise ValueError('Gamescope home must contain executable bin/gamescope.')
        if not (home / 'bin/Xwayland').is_file() and not shutil.which('Xwayland'):
            raise ValueError('Install Xwayland first, or supply it in gamescope home bin/.')
    return target, wine, app, desktop, extras


def assemble(args):
    target, wine, app, desktop, extras = validate(args)
    if args.plan:
        return {'mode': 'plan_only', 'target': str(target), 'profile': args.profile,
                'initialize_prefix': args.initialize_prefix, 'run_installers': args.run_installers,
                'application_version': args.application_version, 'writes_performed': False}
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix='.quantower-install-', dir=target.parent))
    setup_lock = None
    try:
        for name in SOURCE_FILES:
            shutil.copy2(ROOT / name, temporary / name)
        helpers = temporary / 'tools'
        helpers.mkdir(mode=0o700)
        shutil.copy2(ROOT / 'tools/isolate-icon-fonts.py', helpers / 'isolate-icon-fonts.py')
        shutil.copy2(ROOT / 'tools/restore-cef-resources.py', helpers / 'restore-cef-resources.py')
        if (ROOT / 'third_party').is_dir():
            copy_tree(ROOT / 'third_party', temporary / 'third_party')
        for name in ('launch.sh', 'render-launch.sh'):
            os.chmod(temporary / name, 0o700)
        bundle = temporary / 'runner-quantower/opt/wine-devel'
        bundle.parent.mkdir(parents=True)
        copy_tree(wine, bundle)
        components = temporary / 'runtime-components'
        components.mkdir(mode=0o700)
        required = {}

        def pin(path):
            required[str(path.relative_to(temporary))] = sha(path)

        # Check launch-critical executables/modules each time; the full inventory is
        # retained for an explicit audit rather than rehashing a whole Wine tree at startup.
        for relative in ('bin/wine', 'bin/wineserver', 'lib/wine/x86_64-unix/wine',
                         'lib/wine/x86_64-unix/wine-preloader', 'lib/wine/x86_64-windows/gdiplus.dll',
                         'lib/wine/x86_64-windows/gdi32.dll', 'lib/wine/x86_64-windows/explorer.exe'):
            if (bundle / relative).is_file():
                pin(bundle / relative)
        # Runtime bundles include dlopen dependencies and native setup helpers.
        # Check all their ELF bytes at startup as well as the selected PE modules.
        for file in bundle.rglob('*'):
            if file.is_file():
                with file.open('rb') as stream:
                    if stream.read(4) == b'\x7fELF':
                        pin(file)
        for _, files, _ in FONT_FALLBACKS:
            for name in files:
                file = bundle / 'share/wine/fonts' / name
                if file.is_file():
                    pin(file)
        if extras['x11_home']:
            directory = components / 'x11'
            directory.mkdir(mode=0o700)
            for name in ('libX11.so.6', 'libX11-xcb.so.1'):
                shutil.copy2(find_x11(extras['x11_home'], name), directory / name)
                pin(directory / name)
            # Include the private X11 ELF closure, not just its public entry points.
            for file in (extras['x11_home'] / 'lib').glob('*'):
                if file.is_file():
                    with file.open('rb') as stream:
                        if stream.read(4) != b'\x7fELF':
                            continue
                    installed = directory / file.name
                    if installed.exists() and sha(installed) != sha(file):
                        raise ValueError('Conflicting private X11 library: ' + file.name)
                    shutil.copy2(file, installed)
                    pin(installed)
            locale_data = extras['x11_home'] / 'share/X11'
            if locale_data.is_dir():
                copy_tree(locale_data, directory / 'share/X11')
        if extras['gamescope_home']:
            home = extras['gamescope_home']
            # Accept standard --prefix=/usr staged builds or a ready runtime prefix.
            if (home / 'usr/bin/gamescope').is_file():
                home = home / 'usr'
            compositor = home / 'bin/gamescope'
            alternate = home / 'bin/gamescope-brokey'
            if not compositor.is_file():
                compositor = alternate
            if not compositor.is_file() or not os.access(compositor, os.X_OK):
                raise ValueError('Gamescope home must contain executable bin/gamescope.')
            directory = temporary / 'gamescope/files'
            directory.parent.mkdir()
            copy_tree(home, directory)
            installed = directory / 'bin/gamescope-brokey'
            if installed.is_symlink():
                installed.unlink()
            shutil.copy2(compositor, installed)
            xwayland = directory / 'bin/Xwayland'
            if not xwayland.is_file():
                found = shutil.which('Xwayland')
                if not found:
                    raise ValueError('Install Xwayland first, or supply it in gamescope home bin/.')
                shutil.copy2(found, xwayland)
            pin(installed)
            pin(xwayland)
            for file in directory.rglob('*'):
                if file.is_file():
                    with file.open('rb') as stream:
                        if stream.read(4) == b'\x7fELF':
                            pin(file)
        for source_key, name in (('dxvk_d3d9', 'd3d9.dll'), ('assert_trace', 'libquantower_assert_trace.so')):
            if extras[source_key]:
                shutil.copy2(extras[source_key], components / name)
                pin(components / name)
        (temporary / 'prefix').mkdir(mode=0o700)
        (temporary / 'logs').mkdir(mode=0o700)
        if args.initialize_prefix:
            # Acquire before publishing the target, so a new front-door launch
            # cannot begin between publication and setup's ownership record.
            setup_lock = (temporary / 'logs/launch.lock').open('a')
            fcntl.flock(setup_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        selection = {'schema_version': 1, 'application_version': args.application_version,
                     'channel': 'AMP user installation', 'application_bundled': False}
        profile_manager.atomic_bytes(temporary / 'application-version.json', (json.dumps(selection, indent=2) + '\n').encode())
        profile_manager.atomic_bytes(temporary / 'runtime-profile.json',
                                     (json.dumps({'version': 1, 'mode': args.profile}) + '\n').encode())
        profile_manager.atomic_bytes(temporary / 'launcher-config.json',
                                     (json.dumps(runtime_config.RuntimeConfig().to_dict(), indent=2) + '\n').encode())
        manifest = {'version': 1, 'name': 'User-assembled isolated runtime', 'required_files': required,
                    'source': 'User-supplied components; checksums record integrity, not publisher authenticity.',
                    'application_bundled': False, 'native_trace_enabled': bool(extras['assert_trace'])}
        profile_manager.atomic_bytes(components / 'manifest.json', (json.dumps(manifest, indent=2) + '\n').encode())
        if app or desktop:
            cache = temporary / 'installers'
            cache.mkdir(mode=0o700)
            for source, name in ((app, 'Quantower.exe'), (desktop, 'WindowsDesktopRuntime.exe')):
                if source:
                    shutil.copy2(source, cache / name)
                    os.chmod(cache / name, 0o600)
        inventory = {str(p.relative_to(temporary)): sha(p) for p in sorted(temporary.rglob('*')) if p.is_file()}
        profile_manager.atomic_bytes(components / 'install-inventory.json', (json.dumps(inventory, indent=2) + '\n').encode())
        profile_manager.load(temporary)
        # A no-clobber reservation prevents a concurrent invocation replacing a target.
        target.mkdir(mode=0o700)
        try:
            os.rename(temporary, target)
        except BaseException:
            target.rmdir()
            raise
        temporary = None
    finally:
        if temporary is not None:
            shutil.rmtree(temporary)
            if setup_lock is not None:
                setup_lock.close()
    result = {'mode': 'installed_source_and_runtime', 'target': str(target), 'profile': args.profile,
              'application_bundled': False, 'prefix_initialized': False,
              'installers_executed': False, 'runtime_hash_verification': 'PASS'}
    try:
        if args.initialize_prefix:
            options = {'extract_app': True} if args.extract_app else {}
            result.update(initialize(target, args.run_installers, setup_lock=setup_lock, **options))
        return result
    finally:
        if setup_lock is not None:
            setup_lock.close()


def extract_application(target, env, stream, application):
    """Extract only installer app payload, without executing Inno's autorun hooks."""
    destination = target / 'prefix/drive_c/AMPQuantowerInstalled'
    if destination.exists() or destination.is_symlink():
        raise RuntimeError('Application extraction destination must be new.')
    extractor = target / 'runner-quantower/opt/wine-devel/bin/innoextract'
    with tempfile.TemporaryDirectory(prefix='.app-extract-', dir=target) as directory:
        output = Path(directory)
        result = subprocess.run([str(extractor), '--extract', '--silent', '--output-dir', str(output),
                                 str(target / 'installers/Quantower.exe')], env=env, cwd=target,
                                stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT,
                                timeout=900, check=False)
        if result.returncode:
            raise RuntimeError('Native application extraction failed; inspect logs/setup.raw.log.')
        app = output / 'app'
        if not app.is_dir() or app.is_symlink():
            raise RuntimeError('Installer did not contain the expected app payload.')
        # Installer inputs are pinned, but still reject extracted links/special files.
        for entry in app.rglob('*'):
            if entry.is_symlink() or not (entry.is_dir() or entry.is_file()):
                raise RuntimeError('Application payload contains a link or special file.')
        if not (app / 'TradingPlatform' / ('v' + application) / 'Starter.exe').is_file():
            raise RuntimeError('Installer payload does not match the selected application version.')
        shutil.copytree(app, destination)
    return {'step': 'native application extraction', 'exit_code': result.returncode}


def initialize(target, run_installers=False, *, setup_lock=None, extract_app=False):
    """Run only explicitly requested setup in the new prefix, retaining ownership guards."""
    if setup_lock is None:
        with (target / 'logs/launch.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return initialize(target, run_installers, setup_lock=lock, extract_app=extract_app)
    expected = (target / 'logs/launch.lock').stat()
    actual = os.fstat(setup_lock.fileno())
    if (expected.st_dev, expected.st_ino) != (actual.st_dev, actual.st_ino):
        raise RuntimeError('Setup lock does not match the installation launch lock.')
    fcntl.flock(setup_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    if supervisor.prefix_processes(target / 'prefix') != set():
        raise RuntimeError('Cannot initialize a prefix with an active or unverifiable Starter.')
    profile_manager.no_running_prefix(target / 'prefix')
    session = supervisor.SessionProcesses(target / 'prefix', target / 'logs/session-processes.json')
    session.begin()
    env = os.environ.copy()
    for name in ('DOTNET_ROOT', 'DOTNET_ROOT_X64', 'DOTNET_STARTUP_HOOKS', 'QUANTOWER_DIAGNOSTIC_STARTUP_HOOKS',
                 'WINEDLLPATH', 'WINELOADER', 'WINESERVER', 'LD_PRELOAD', 'LD_LIBRARY_PATH'):
        env.pop(name, None)
    env.update(WINEPREFIX=str(target / 'prefix'), WINEARCH='win64', WINEDEBUG='-all,err+all,warn+all',
               WINEDLLOVERRIDES='winemenubuilder.exe=d;mscoree,mshtml=d', QUANTOWER_LAUNCH_SESSION=session.token)
    runtime_config.apply_wine_library_environment(target, env)
    wine = target / 'runner-quantower/opt/wine-devel/bin/wine'
    records = []
    with (target / 'logs/setup.raw.log').open('xb') as stream:
        def execute(arguments, label, timeout, accepted=(0,)):
            child = subprocess.Popen([str(wine), *arguments], env=env, cwd=target,
                                     stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT)
            session.remember_child(child.pid)
            deadline = time.monotonic() + timeout
            while child.poll() is None:
                session.observe()
                if time.monotonic() > deadline:
                    # No name-only or unguarded termination. Preserve the installer
                    # and return an actionable failure with its private log intact.
                    raise RuntimeError('Setup step exceeded its wait: ' + label + '; its process was preserved.')
                time.sleep(.25)
            if child.returncode not in accepted:
                raise RuntimeError('Setup step failed: ' + label + '; inspect logs/setup.raw.log.')
            records.append({'step': label, 'exit_code': child.returncode})
        execute(['wineboot.exe', '--init'], 'prefix initialization', 180)
        # These optional first-run installers are unnecessary for our separately
        # installed Microsoft .NET/CEF. Do not disable their DLLs for later setup.
        env['WINEDLLOVERRIDES'] = 'winemenubuilder.exe=d'
        # Native taskbar stays scoped to this named desktop, with no host settings.
        execute(['reg.exe', 'add', r'HKCU\Software\Wine\Explorer\Desktops\AMP-Quantower',
                 '/v', 'EnableShell', '/t', 'REG_DWORD', '/d', '1', '/f'], 'desktop shell configuration', 30)
        fonts = target / 'runner-quantower/opt/wine-devel/share/wine/fonts'
        for family, files, aliases in FONT_FALLBACKS:
            if all((fonts / name).is_file() for name in files):
                for alias in aliases:
                    execute(['reg.exe', 'add', r'HKLM\Software\Microsoft\Windows NT\CurrentVersion\FontSubstitutes',
                             '/v', alias, '/t', 'REG_SZ', '/d', family, '/f'],
                            'open font fallback: ' + alias, 30)
                    execute(['reg.exe', 'add', r'HKCU\Software\Wine\Fonts\Replacements',
                             '/v', alias, '/t', 'REG_SZ', '/d', family, '/f'],
                            'DirectWrite font fallback: ' + alias, 30)
        if run_installers:
            execute(['Z:' + str(target / 'installers/WindowsDesktopRuntime.exe').replace('/', '\\'),
                     '/install', '/quiet', '/norestart'], '.NET Desktop Runtime', 900,
                    accepted=(0, 3010 & 0xff))
            # Unix exposes an eight-bit status, so Windows reboot-request 3010
            # appears as 194. Confirm actual x64 runtime installation as well.
            dotnet = target / 'prefix/drive_c/Program Files/dotnet'
            if (not (dotnet / 'dotnet.exe').is_file() or
                    not any((dotnet / 'shared/Microsoft.WindowsDesktop.App').glob('*/PresentationFramework.dll')) or
                    not any((dotnet / 'shared/Microsoft.NETCore.App').glob('*/System.Private.CoreLib.dll'))):
                raise RuntimeError('The expected x64 .NET Desktop Runtime was not installed; inspect logs/setup.raw.log.')
            application = json.loads((target / 'application-version.json').read_text())['application_version']
            if extract_app:
                records.append(extract_application(target, env, stream, application))
            else:
                print('In the Quantower extractor choose C:\\AMPQuantowerInstalled. Exit the new app when finished, then use launch.sh.', file=sys.stderr)
                execute(['Z:' + str(target / 'installers/Quantower.exe').replace('/', '\\')], 'Quantower extractor', 3600)
            executable = target / 'prefix/drive_c/AMPQuantowerInstalled/TradingPlatform' / ('v' + application) / 'Starter.exe'
            if not executable.is_file():
                raise RuntimeError('Selected Starter.exe is absent. Check the extraction folder/version before launching.')
        session.observe()
        if supervisor.prefix_processes(session.prefix) == set():
            def note(message, **fields):
                stream.write((json.dumps({'message': message, **fields}, default=str) + '\n').encode())
                stream.flush()
            cleaned = supervisor.cleanup_session(session, wine.parent, env, stream, note)
        else:
            cleaned = False
        # An auto-started Starter prevents all cleanup, exactly as normal supervision.
    return {'prefix_initialized': True, 'installers_executed': run_installers,
            'setup_steps': records, 'setup_cleanup_complete': cleaned,
            'next_step': 'Exit any extractor-started app, then run doctor.py and launch.sh.'}


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--target', required=True, type=Path)
    p.add_argument('--wine-home', required=True, type=Path, help='Relocatable Wine prefix containing bin/wine and bin/wineserver.')
    p.add_argument('--gamescope-home', type=Path)
    p.add_argument('--x11-home', type=Path)
    p.add_argument('--dxvk-d3d9', type=Path)
    p.add_argument('--assert-trace', type=Path)
    p.add_argument('--application-version', default='1.147.5')
    p.add_argument('--profile', choices=('compatible', 'gpu'), default='compatible')
    p.add_argument('--app-installer', type=Path)
    p.add_argument('--app-installer-sha256')
    p.add_argument('--desktop-runtime-installer', type=Path)
    p.add_argument('--desktop-runtime-sha256')
    p.add_argument('--initialize-prefix', action='store_true')
    p.add_argument('--run-installers', action='store_true')
    p.add_argument('--extract-app', action='store_true', help='Use bundled innoextract for the pinned app payload without installer autorun hooks.')
    p.add_argument('--plan', action='store_true', help='Validate inputs and print the installation plan without writes or execution.')
    return p


def main(argv=None):
    os.umask(0o077)
    p = parser()
    try:
        result = assemble(p.parse_args(argv))
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        p.exit(1, 'Installation stopped: ' + str(error) + '\n')
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
