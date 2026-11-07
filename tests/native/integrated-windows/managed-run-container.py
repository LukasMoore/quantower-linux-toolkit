#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Own WPF/CEF fixture in a new networkless disposable prefix/display only."""
import hashlib
import json
import os
from pathlib import Path
import select
import shutil
import subprocess
import sys
import time


def main():
    if (not Path('/.dockerenv').is_file() or os.getuid() == 0 or
            os.getenv('QTK_SYNTHETIC_DATAONLY') != '1' or
            {p.name for p in Path('/sys/class/net').iterdir()} != {'lo'}):
        raise RuntimeError('Explicit non-root networkless data-only container required.')
    root = Path('/test/results')
    if root.exists() or root.is_symlink():
        raise FileExistsError('Use a new results directory and prefix.')
    os.umask(0o077)
    root.mkdir(mode=0o700)
    read, write = os.pipe()
    with (root / 'xvfb.log').open('xb') as log:
        subprocess.Popen(['Xvfb', '-displayfd', str(write), '-screen', '0', '1920x1080x24',
                          '-nolisten', 'tcp', '-noreset'], pass_fds=(write,), stdout=log, stderr=subprocess.STDOUT)
    os.close(write)
    if not select.select([read], [], [], 20)[0]:
        raise RuntimeError('Own display startup failed.')
    number = os.read(read, 32).decode().strip()
    os.close(read)
    if not number.isdecimal():
        raise RuntimeError('Invalid own display number.')
    wine = '/runtime/wine/bin/wine'
    env = {'PATH': '/runtime/wine/bin:' + os.defpath, 'DISPLAY': ':' + number,
           'WINEPREFIX': str(root / 'prefix'), 'WINEARCH': 'win64', 'WINEDEBUG': '-all,err+all,warn+all',
           'WINEDLLOVERRIDES': 'winemenubuilder.exe=d;mscoree,mshtml=d;winex11.drv=b;winewayland.drv=d',
           'LD_LIBRARY_PATH': '/runtime/x11/lib:/runtime/deps:/runtime/wine/lib',
           'XLOCALEDIR': '/runtime/x11/share/X11/locale', 'QUANTOWER_LAUNCH_SESSION': os.urandom(16).hex()}
    for key in ('HOME', 'USER', 'LOGNAME'):
        if key in os.environ:
            env[key] = os.environ[key]
    with (root / 'wineboot.log').open('xb') as log:
        subprocess.run([wine, 'wineboot.exe', '--init'], env=env, stdout=log, stderr=subprocess.STDOUT,
                       check=True, timeout=180)
    shutil.copyfile('/runtime/d3d9.dll', root / 'prefix/drive_c/windows/system32/d3d9.dll')
    env.update(WINEDLLOVERRIDES='winemenubuilder.exe=d;d3d9=n,b;winex11.drv=b;winewayland.drv=d',
               DOTNET_ROOT=r'Z:\runtime\dotnet', DOTNET_ROOT_X64=r'Z:\runtime\dotnet',
               DXVK_LOG_PATH=str(root), DXVK_LOG_LEVEL='info', DXVK_ASYNC='0',
               DXVK_CONFIG_FILE='/runtime/gpu-dxvk.conf')
    # 96 baseline first. This disposable Xvfb test does not choose real-demo DPI.
    dpi = int(os.environ.get('QTK_FIXTURE_DPI', '96'))
    if dpi not in (96, 192):
        raise ValueError('Fixture DPI must be explicit 96 or 192.')
    for key in (r'HKCU\Control Panel\Desktop', r'HKCU\Software\Wine\Fonts'):
        with (root / 'dpi.log').open('ab') as log:
            subprocess.run([wine, 'reg.exe', 'add', key, '/v', 'LogPixels', '/t', 'REG_DWORD', '/d', str(dpi), '/f'],
                           env=env, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=30)
    with (root / 'wait.log').open('ab') as log:
        subprocess.run(['/runtime/wine/bin/wineserver', '-w'], env=env, stdout=log, stderr=subprocess.STDOUT,
                       check=True, timeout=90)
    report = {'synthetic_only': True, 'display_kind': 'own-Xvfb', 'host_wm_present': False, 'wine_dpi': dpi,
              'limits': 'Own WPF/CEF callbacks, DOM/canvas and geometry only. GPU devices, visible pixels, host physical input/taskbar and vendor behavior are not inferred.', 'cases': []}
    for awareness in ('unaware', 'system', 'pmv2'):
        env['QUANTOWER_LAUNCH_SESSION'] = os.urandom(16).hex()
        result = root / awareness
        path = root / (awareness + '.log')
        with path.open('xb') as log:
            child = subprocess.Popen([wine, r'Z:\runtime\dotnet\dotnet.exe', r'Z:\probe\WpfCefFixture.dll',
                                      r'Z:\runtime\cef', 'Z:' + str(result).replace('/', '\\'), awareness],
                                     env=env, stdout=log, stderr=subprocess.STDOUT)
            mapped = set()
            identities = set()
            deadline = time.monotonic() + 50
            while child.poll() is None and time.monotonic() < deadline:
                for proc in Path('/proc').iterdir():
                    if not proc.name.isdecimal():
                        continue
                    try:
                        if proc.stat().st_uid != os.getuid():
                            continue
                        entries = (proc / 'environ').read_bytes().split(b'\0')
                        if (os.fsencode('WINEPREFIX=' + env['WINEPREFIX']) not in entries or
                            os.fsencode('QUANTOWER_LAUNCH_SESSION=' + env['QUANTOWER_LAUNCH_SESSION']) not in entries):
                            continue
                        args = (proc / 'cmdline').read_bytes().lower().replace(b'\\', b'/')
                        if b'wpfceffixture.dll' not in args:
                            continue
                        raw = (proc / 'stat').read_text()
                        birth = raw[raw.rfind(')') + 2:].split()[19]
                        executable = str((proc / 'exe').readlink())
                        if executable not in ('/runtime/wine/bin/wine', '/runtime/wine/lib/wine/x86_64-unix/wine',
                                              '/runtime/wine/lib/wine/x86_64-unix/wine-preloader'):
                            continue
                        maps = (proc / 'maps').read_text()
                        after = (proc / 'stat').read_text()
                        if after[after.rfind(')') + 2:].split()[19] != birth:
                            continue
                        identities.add((int(proc.name), int(birth), executable))
                        mapped.update(line.rsplit(' ', 1)[-1] for line in maps.splitlines()
                                      if any(name in line for name in ('winex11.so', 'winewayland.so', 'd3d9.dll', 'libX11.so')))
                    except (OSError, ProcessLookupError):
                        pass
                time.sleep(.1)
            code = child.poll()
            if code is None:
                raise RuntimeError('Own managed fixture exceeded its bound; no force termination/restart was attempted.')
            child.wait()
        data = json.loads((result / 'result.json').read_text()) if (result / 'result.json').is_file() else None
        backend = any('winex11.so' in p for p in mapped) and not any('winewayland.so' in p for p in mapped)
        report['cases'].append({'awareness': awareness, 'exit_code': code, 'result': data,
                                'x11_mapped_only': backend, 'mapped_modules': sorted(mapped),
                                'exact_fixture_identities': sorted(identities)})
        (root / 'summary.json').write_text(json.dumps(report, indent=2) + '\n')
        if code != 0:
            break
    report['fixture_sha256'] = hashlib.sha256(Path('/probe/WpfCefFixture.dll').read_bytes()).hexdigest()
    report['passed'] = len(report['cases']) == 3 and all(c['exit_code'] == 0 and c['x11_mapped_only'] and
                    c['result'] and c['result'].get('completed_cef_reopen_cycles') == 3 for c in report['cases'])
    (root / 'summary.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'cases': len(report['cases']), 'passed': report['passed']}))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
