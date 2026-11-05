#!/usr/bin/env python3
"""Explicit synthetic Wine integration in a disposable Docker desktop fixture.

The default prints a read-only plan. --run never uses the installation's app
prefix: it creates a separate fixture prefix and Xvfb display. Run the harness
as the container's main command so exiting tears down remaining Wine helpers.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import select
import shutil
import struct
import subprocess
import sys
import time

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import profile_manager
import runtime_config


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def check_probe(path, bits):
    path = Path(path).expanduser().resolve(strict=True)
    with path.open('rb') as stream:
        if stream.read(2) != b'MZ':
            raise ValueError('Probe must be a compiled Windows PE executable.')
        stream.seek(60)
        offset = struct.unpack('<I', stream.read(4))[0]
        if offset > path.stat().st_size - 6:
            raise ValueError('Probe has an invalid PE header.')
        stream.seek(offset)
        signature, machine = struct.unpack('<4sH', stream.read(6))
        if signature != b'PE\0\0' or machine != {32: 0x014c, 64: 0x8664}[bits]:
            raise ValueError('Probe PE architecture does not match its --probe argument.')
    return path


def fixture_guard():
    if os.environ.get('QUANTOWER_ISOLATED_SMOKE') != '1' or not Path('/.dockerenv').is_file():
        raise ValueError('--run requires the explicit Docker fixture marker QUANTOWER_ISOLATED_SMOKE=1.')
    if os.getuid() == 0:
        raise ValueError('Run the disposable desktop fixture as a non-root user.')
    missing = [name for name in ('Xvfb', 'xwininfo') if not shutil.which(name)]
    if missing:
        raise ValueError('The reference fixture is missing: ' + ', '.join(missing))


def probe_report(log, bits, *, taskbar=False):
    text = Path(log).read_text(errors='replace')
    reports = []
    modules = {}
    for line in text.splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            item = None
        if isinstance(item, dict) and item.get('synthetic_only') is True:
            reports.append(item)
        match = re.search(r'Loaded L"([^"\n]+\.(?:dll|drv))".*?\b(builtin|native)\b', line, re.I)
        if match:
            name = match[1].replace('\\', '/').rsplit('/', 1)[-1].lower()
            modules[name] = match[2].lower()
    if len(reports) != 1 or reports[0].get('architecture_bits') != bits or reports[0].get('passed') is not True:
        raise RuntimeError('The synthetic probe did not report one successful result for its expected architecture.')
    if any(modules.get(name) != 'builtin' for name in ('ntdll.dll', 'gdiplus.dll', 'usp10.dll')):
        raise RuntimeError('Expected builtin Wine module evidence is missing; inspect the private raw probe log.')
    report = reports[0]
    if taskbar:
        tray = report.get('taskbar', {})
        if (not all(tray.get(key) is True for key in ('attempted', 'available', 'topmost', 'restored', 'bounds_preserved'))
                or tray.get('tray_hit_points') != 5):
            raise RuntimeError('Synthetic taskbar minimize/restore checks failed.')
    report['modules_observed'] = modules
    report['private_xlib_initialized'] = any('calling init:' in line and
                                             '/runtime-components/x11/libX11.so' in line
                                             for line in text.splitlines())
    return report


def plan(args):
    base = args.base.expanduser().resolve(strict=True)
    profile_manager.load(base)
    wine = base / 'runner-quantower/opt/wine-devel/bin/wine'
    if not wine.is_file() or not os.access(wine, os.X_OK):
        raise ValueError('The selected installation has no executable Wine loader.')
    probes = {32: check_probe(args.probe32, 32), 64: check_probe(args.probe64, 64)}
    output = args.output.expanduser().absolute()
    if output.exists() or output.is_symlink():
        raise FileExistsError('Smoke evidence output must be a new directory.')
    output = output.resolve()
    if output.is_relative_to(base) or base.is_relative_to(output):
        raise ValueError('The synthetic fixture output must be separate from the installation.')
    if not output.parent.is_dir():
        raise ValueError('Create the evidence parent directory before planning a smoke run.')
    return {'mode': 'plan', 'synthetic_only': True, 'writes_performed': False,
            'base': str(base), 'output': str(output), 'fresh_prefix': str(output / 'prefix'),
            'wine_sha256': sha256(wine),
            'probes': {str(bits): {'path': str(path), 'sha256': sha256(path)} for bits, path in probes.items()},
            'checks': ['new Xvfb display', 'fresh win64 prefix', '32-bit and 64-bit GDI+/Uniscribe',
                       'builtin DLL module evidence', 'named desktop taskbar minimize/restore'],
            'cleanup': 'No process-name or global Wine cleanup. Exit the disposable container after this harness returns.',
            'limits': ['No application, account, connection or market data is accessed.',
                       'Diagnostic tracing is enabled; these counters are not performance benchmarks.',
                       'Software Xvfb rendering does not validate a host GPU or gamescope rendering.',
                       'Zero GDI-resource counters cannot establish absence of GDI leaks.']}


def write_summary(output, report):
    profile_manager.atomic_bytes(output / 'summary.json', (json.dumps(report, indent=2, sort_keys=True) + '\n').encode())


def command(arguments, environment, log, timeout):
    with log.open('xb') as stream:
        child = subprocess.Popen(list(map(str, arguments)), env=environment, stdin=subprocess.DEVNULL,
                                 stdout=stream, stderr=subprocess.STDOUT)
        try:
            code = child.wait(timeout=timeout)
        except subprocess.TimeoutExpired as error:
            # Keep the process intact. Only the disposable container's teardown
            # owns fixture-wide termination; no unguarded Wine cleanup occurs.
            raise RuntimeError('Fixture command exceeded its deadline and was preserved. Exit the disposable container.') from error
    if code:
        raise RuntimeError('Fixture command failed (exit ' + str(code) + '); inspect private ' + log.name + '.')


def start_xvfb(output):
    read_fd, write_fd = os.pipe()
    try:
        with (output / 'xvfb.raw.log').open('xb') as stream:
            process = subprocess.Popen(['Xvfb', '-displayfd', str(write_fd), '-screen', '0', '1280x720x24',
                                        '-nolisten', 'tcp', '-noreset'], pass_fds=(write_fd,),
                                       stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT)
        os.close(write_fd)
        write_fd = None
        if not select.select([read_fd], [], [], 20)[0]:
            raise RuntimeError('New Xvfb display was not ready; exit the disposable container.')
        number = os.read(read_fd, 32).decode().strip()
        if not number.isdigit() or process.poll() is not None:
            raise RuntimeError('Xvfb did not return a usable new display.')
        return process, ':' + number
    finally:
        os.close(read_fd)
        if write_fd is not None:
            os.close(write_fd)


def wait_desktop(environment, process):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError('Synthetic named desktop exited before it became visible.')
        tree = subprocess.run(['xwininfo', '-root', '-tree'], env=environment, capture_output=True,
                              text=True, timeout=5, check=False)
        if tree.returncode == 0 and 'AMP-Quantower - Wine desktop' in tree.stdout:
            return
        time.sleep(.2)
    raise RuntimeError('Synthetic named desktop was not observed; exit the disposable container.')


def execute(args):
    report = plan(args)
    if not args.run:
        return report
    fixture_guard()
    base, output = Path(report['base']), Path(report['output'])
    output.mkdir(mode=0o700)
    report.update(mode='running', writes_performed=True, status='running', results={})
    write_summary(output, report)
    try:
        xvfb, display = start_xvfb(output)
        environment = os.environ.copy()
        for key in ('DOTNET_ROOT', 'DOTNET_ROOT_X64', 'DOTNET_STARTUP_HOOKS', 'QUANTOWER_DIAGNOSTIC_STARTUP_HOOKS',
                    'WINEDLLPATH', 'WINELOADER', 'WINESERVER', 'LD_PRELOAD', 'LD_LIBRARY_PATH',
                    'LD_DEBUG', 'WAYLAND_DISPLAY', 'QUANTOWER_X11_RUNTIME'):
            environment.pop(key, None)
        environment.update(WINEPREFIX=str(output / 'prefix'), WINEARCH='win64', DISPLAY=display,
                           WINEDEBUG='-all,+loaddll', WINEDLLOVERRIDES='winemenubuilder.exe=d', LD_DEBUG='libs')
        x11 = runtime_config.apply_wine_library_environment(base, environment)
        wine = base / 'runner-quantower/opt/wine-devel/bin/wine'
        command([wine, 'wineboot.exe', '--init'], environment, output / 'wineboot.raw.log', args.timeout)
        command([wine, 'reg.exe', 'add', r'HKCU\Software\Wine\Explorer\Desktops\AMP-Quantower',
                 '/v', 'EnableShell', '/t', 'REG_DWORD', '/d', '1', '/f'], environment,
                output / 'desktop-config.raw.log', args.timeout)
        for bits in (64, 32):
            log = output / ('probe-' + str(bits) + '.raw.log')
            command([wine, report['probes'][str(bits)]['path']], environment, log, args.timeout)
            result = probe_report(log, bits)
            if x11 == 'private' and not result['private_xlib_initialized']:
                raise RuntimeError('The selected private Xlib was not observed initializing in the probe log.')
            report['results'][str(bits)] = result
            write_summary(output, report)
        with (output / 'desktop.raw.log').open('xb') as stream:
            desktop = subprocess.Popen([str(wine), 'explorer.exe', '/desktop=AMP-Quantower,1280x720'],
                                       env=environment, stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT)
        wait_desktop({**os.environ, 'DISPLAY': display}, desktop)
        log = output / 'probe-taskbar-64.raw.log'
        command([wine, report['probes']['64']['path'], '--desktop', 'AMP-Quantower', '--taskbar'],
                environment, log, args.timeout)
        report['results']['taskbar_64'] = probe_report(log, 64, taskbar=True)
        if xvfb.poll() is not None:
            raise RuntimeError('Synthetic X server exited unexpectedly.')
        report.update(mode='finished', status='passed', x11_runtime=x11)
    except BaseException as error:
        report.update(mode='finished', status='failed', error=str(error))
        write_summary(output, report)
        raise
    write_summary(output, report)
    return report


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base', required=True, type=Path, help='Read-only reference installation; its app prefix is never used.')
    p.add_argument('--probe32', required=True, type=Path)
    p.add_argument('--probe64', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path, help='New private evidence directory outside the installation.')
    action = p.add_mutually_exclusive_group()
    action.add_argument('--plan', action='store_true', help='Print the read-only plan (default).')
    action.add_argument('--run', action='store_true', help='Explicitly execute only inside the disposable Docker fixture.')
    p.add_argument('--timeout', type=int, default=180, help='Per-command fixture deadline, in seconds (1-900).')
    return p


def main(argv=None):
    os.umask(0o077)
    p = parser()
    args = p.parse_args(argv)
    try:
        if not 1 <= args.timeout <= 900:
            raise ValueError('Fixture timeout must be between 1 and 900 seconds.')
        result = execute(args)
    except (OSError, ValueError, RuntimeError, KeyError, struct.error, subprocess.TimeoutExpired) as error:
        p.exit(1, 'Synthetic runtime smoke stopped: ' + str(error) + '\n')
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
