#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Original signed-tooltip matrix in an explicit disposable networkless container."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import select
import subprocess
import time

REQUESTS = ((200, 200), (400, 300), (0, 100), (-100, 100), (-500, 100),
            (100, -100), (100, -500), (-100, -100), (-500, -500))
EXECUTABLES = frozenset(('/runtime/wine/bin/wine', '/runtime/wine/lib/wine/x86_64-unix/wine',
                        '/runtime/wine/lib/wine/x86_64-unix/wine-preloader'))
SOURCE_INPUTS = {
    'monitor-workareas.c': 'cade4936676bd79c10cf81b3835da077ef09b50ded0f1cf4a5be24296c09e0fa',
    'tooltip-track-coordinates.c': '5bb1680be15f365a0535d2254489c74f17623946d485cd3e7a15508712e2c20a',
    'tooltip-v6.rc': '1a5925ca7c097d852c9e18000b1adcb3de69892033cf56078eb6b3c0c9b37de8',
    'tooltip-v6.manifest': '6df4ec061ed7d8a508c2f3bf2d17f71e56d1cb8731d611e633b459016f30a651',
}


def container_guard():
    if (not Path('/.dockerenv').is_file() or os.getuid() == 0 or
            os.getenv('QTK_SYNTHETIC_DATAONLY') != '1' or
            {path.name for path in Path('/sys/class/net').iterdir()} != {'lo'}):
        raise RuntimeError('Explicit non-root own-fixture container with --network none required.')


def verify_coordinate_evidence(report, expect, family, module_sha256):
    """Signed decoding/identity gate only; never a visible-tooltip assertion."""
    if expect not in ('baseline', 'candidate') or family not in ('v5', 'v6'):
        return False
    try:
        if (report['synthetic_only'] is not True or report['display'] != 'own-Xvfb-noWM' or
                report['family'] != family or report['fixtures_unchanged'] is not True or
                [case['dpi'] for case in report['cases']] != [96, 192] or
                not re.fullmatch(r'[0-9a-f]{64}', module_sha256)):
            return False
        expected_module = '/runtime/wine/lib/wine/x86_64-windows/' + ('comctl32.dll' if family == 'v5' else 'comctl32_v6.dll')
        for case in report['cases']:
            inventory = [row for row in case['rows'] if row.get('kind') == 'inventory']
            rows = [row for row in case['rows'] if row.get('kind') == 'case']
            if len(case['rows']) != 11 or len(inventory) != 1 or inventory[0]['dpi'] != case['dpi'] or len(rows) != len(REQUESTS):
                return False
            work = inventory[0]['work']
            if len(work) != 4 or work[2] <= work[0] or work[3] <= work[1]:
                return False
            if (case['exact_module_verified'] is not True or
                    case['mapped_comctl32'] != {expected_module: module_sha256} or not case['identities']):
                return False
            for identity in case['identities']:
                if (type(identity['pid']) is not int or identity['pid'] <= 0 or
                        type(identity['start_ticks']) is not int or identity['start_ticks'] <= 0 or
                        type(identity['uid']) is not int or identity['uid'] <= 0 or
                        identity['executable'] not in EXECUTABLES):
                    return False
            failures = 0
            for request, row in zip(REQUESTS, rows):
                if row['request'] != list(request):
                    return False
                expected_origin = [max(request[0], work[0]), request[1]]
                actual = row['actual_client_origin']
                rect = row['window_rect']
                if (not isinstance(actual, list) or len(actual) != 2 or any(type(v) is not int for v in actual) or
                        not isinstance(rect, list) or len(rect) != 4 or any(type(v) is not int for v in rect) or
                        rect[2] < rect[0] or rect[3] < rect[1]):
                    return False
                actual_matches = row['actual_client_origin'] == expected_origin
                expected_pass = expect == 'candidate' or (request[0] >= 0 and request[1] >= 0)
                if (row['expected_client_origin'] != expected_origin or type(row['passed']) is not bool or
                        row['passed'] != actual_matches or row['passed'] != expected_pass):
                    return False
                failures += not actual_matches
            result = case['result']
            if (result != case['rows'][-1] or result.get('kind') != 'result' or result['cases'] != 9 or
                    result['failures'] != failures or result['host_ui_test'] is not False or
                    failures != (0 if expect == 'candidate' else 6) or
                    case['exit_code'] != (0 if expect == 'candidate' else 1)):
                return False
    except (KeyError, TypeError, IndexError, ValueError):
        return False
    return True


def classify_targets(report):
    """Classify requested points against actual EnumDisplayMonitors evidence."""
    result={'monitor_catalogue_verified':True,'all_rectangles_nonempty':True,
            'all_targets_inside_workareas':True,'valid_negative_monitor_targets':0,'outside_workarea_cases':[],
            'collapsed_rectangle_cases':[],'negative_monitor_acceptance':False}
    try:
        for case in report['cases']:
            catalogue=case['monitor_inventory']
            monitors=[row for row in catalogue if row.get('kind')=='active-monitor']
            summary=catalogue[-1]
            if (not monitors or summary.get('kind')!='monitor-result' or summary.get('failures')!=0 or
                    summary.get('count')!=len(monitors) or summary.get('dpi')!=case['dpi']):
                raise ValueError('Invalid actual monitor catalogue.')
            areas=[row['work'] for row in monitors]
            if any(len(area)!=4 or any(type(v) is not int for v in area) or
                   area[2]<=area[0] or area[3]<=area[1] for area in areas):
                raise ValueError('Invalid actual workarea extent.')
            for row in case['rows']:
                if row.get('kind')!='case':continue
                x,y=row['request'];rect=row['window_rect']
                inside=any(area[0]<=x<area[2] and area[1]<=y<area[3] for area in areas)
                nonempty=rect[2]>rect[0] and rect[3]>rect[1]
                if not inside:
                    result['all_targets_inside_workareas']=False
                    result['outside_workarea_cases'].append({'dpi':case['dpi'],'request':[x,y]})
                elif x<0 or y<0:result['valid_negative_monitor_targets']+=1
                if not nonempty:
                    result['all_rectangles_nonempty']=False
                    result['collapsed_rectangle_cases'].append({'dpi':case['dpi'],'request':[x,y],'window_rect':rect})
        # Geometry classification cannot establish composed visibility, physical
        # input or application popup behavior. A dedicated valid-target host
        # fixture and actual acceptance remain separate even on negative monitors.
    except (KeyError,IndexError,TypeError,ValueError):
        result['monitor_catalogue_verified']=False
    return result


def verify_report(report,expect,family,module_sha256):
    """Keep visible geometry/valid targets mandatory, separately from decoding."""
    geometry=classify_targets(report)
    return (verify_coordinate_evidence(report,expect,family,module_sha256) and
            geometry['monitor_catalogue_verified'] and geometry['all_rectangles_nonempty'] and
            geometry['all_targets_inside_workareas'])


def fixture_observation(proc, prefix, token, binary):
    """Read exact own UID/prefix/token/birth/role before and after mapped-module evidence."""
    try:
        uid = proc.stat().st_uid
        if uid != os.getuid():
            return None
        before = (proc / 'stat').read_text()
        fields = before[before.rfind(')') + 2:].split()
        if fields[0] in ('Z', 'X'):
            return None
        birth = int(fields[19])
        credentials = (proc / 'environ').read_bytes().split(b'\0')
        if (os.fsencode('QUANTOWER_LAUNCH_SESSION=' + token) not in credentials or
                os.fsencode('WINEPREFIX=' + prefix) not in credentials):
            return None
        arguments = (proc / 'cmdline').read_bytes().replace(b'\\', b'/').lower().split(b'\0')
        arguments = [argument[2:] if argument.startswith(b'z:') else argument for argument in arguments]
        if os.fsencode(binary.lower()) not in arguments:
            return None
        executable = str((proc / 'exe').readlink())
        if executable not in EXECUTABLES:
            return None
        maps = (proc / 'maps').read_text()
        after = (proc / 'stat').read_text()
        fields = after[after.rfind(')') + 2:].split()
        if (fields[0] in ('Z', 'X') or int(fields[19]) != birth or proc.stat().st_uid != uid or
                str((proc / 'exe').readlink()) != executable or
                (proc / 'environ').read_bytes().split(b'\0') != credentials):
            return None
        modules = {line.split(maxsplit=5)[-1] for line in maps.splitlines() if 'comctl32' in line and '.dll' in line}
        return {'pid': int(proc.name), 'start_ticks': birth, 'uid': uid, 'executable': executable}, modules
    except (OSError, ValueError, IndexError):
        return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--family', choices=('v5', 'v6'), required=True)
    parser.add_argument('--expect', choices=('baseline', 'candidate'), required=True)
    parser.add_argument('--expected-comctl32-sha256', required=True)
    args = parser.parse_args(argv)
    container_guard()
    if not re.fullmatch(r'[0-9a-f]{64}', args.expected_comctl32_sha256):
        raise ValueError('An exact selected comctl32 SHA256 is required.')
    root = Path('/test/results')
    if root.exists() or root.is_symlink():
        raise FileExistsError('Never reuse own fixture prefix/results.')
    build = json.loads(Path('/probe/build.json').read_text())
    binary = Path('/probe') / ('tooltip-track-coordinates.exe' if args.family == 'v5' else 'tooltip-track-coordinates-v6.exe')
    fixtures_unchanged = (build.get('compile_only') is True and build.get('source_inputs') == SOURCE_INPUTS and
        build.get('binaries', {}).get(args.family, {}).get('sha256') == hashlib.sha256(binary.read_bytes()).hexdigest() and
        build.get('binaries', {}).get('monitors', {}).get('sha256') == hashlib.sha256(Path('/probe/monitor-workareas.exe').read_bytes()).hexdigest())
    if not fixtures_unchanged:
        raise ValueError('Original fixture build inputs or binary changed.')
    os.umask(0o077)
    root.mkdir(mode=0o700)
    read, write = os.pipe()
    with (root / 'xvfb.log').open('xb') as output:
        subprocess.Popen(['Xvfb', '-displayfd', str(write), '-screen', '0', '1920x1080x24', '-nolisten', 'tcp', '-noreset'],
            pass_fds=(write,), stdout=output, stderr=subprocess.STDOUT)
    os.close(write)
    if not select.select([read], [], [], 20)[0]:
        raise RuntimeError('Own display not ready; no retry or fallback.')
    number = os.read(read, 32).decode().strip()
    os.close(read)
    if not number.isdecimal():
        raise RuntimeError('Invalid own display number.')
    env = {'PATH': '/runtime/wine/bin:' + os.defpath, 'DISPLAY': ':' + number, 'WINEPREFIX': str(root / 'prefix'),
        'WINEARCH': 'win64', 'WINEDEBUG': '-all,err+all,warn+all',
        'WINEDLLOVERRIDES': 'winemenubuilder.exe=d;mscoree,mshtml=d;winex11.drv=b;winewayland.drv=d',
        'LD_LIBRARY_PATH': '/runtime/x11/lib:/runtime/deps:/runtime/wine/lib', 'XLOCALEDIR': '/runtime/x11/share/X11/locale'}
    for key in ('HOME', 'USER', 'LOGNAME'):
        if key in os.environ:
            env[key] = os.environ[key]
    wine = '/runtime/wine/bin/wine'
    with (root / 'wineboot.log').open('xb') as output:
        subprocess.run([wine, 'wineboot.exe', '--init'], env=env, stdout=output, stderr=subprocess.STDOUT, timeout=180, check=True)
    report = {'synthetic_only': True, 'display': 'own-Xvfb-noWM', 'family': args.family,
        'fixtures_unchanged': fixtures_unchanged, 'expect': args.expect, 'cases': []}
    for dpi in (96, 192):
        with (root / 'setup.log').open('ab') as output:
            for key in (r'HKCU\Control Panel\Desktop', r'HKCU\Software\Wine\Fonts'):
                subprocess.run([wine, 'reg.exe', 'add', key, '/v', 'LogPixels', '/t', 'REG_DWORD', '/d', str(dpi), '/f'],
                    env=env, stdout=output, stderr=subprocess.STDOUT, timeout=30, check=True)
            subprocess.run(['/runtime/wine/bin/wineserver', '-w'], env=env, stdout=output, stderr=subprocess.STDOUT, timeout=90, check=True)
        env['QUANTOWER_LAUNCH_SESSION'] = os.urandom(16).hex()
        monitor_log=root/f'monitors-{dpi}.log'
        with monitor_log.open('xb') as output:
            inventory=subprocess.Popen([wine,'/probe/monitor-workareas.exe'],env=env,stdout=output,stderr=subprocess.STDOUT)
            if inventory.wait(timeout=20)!=0:
                raise RuntimeError('Own actual monitor inventory failed; no retry.')
        monitor_rows=[json.loads(line) for line in monitor_log.read_text(errors='replace').splitlines() if line.startswith('{')]
        env['QUANTOWER_LAUNCH_SESSION'] = os.urandom(16).hex()
        log = root / f'probe-{dpi}.log'
        identities, mapped = {}, set()
        with log.open('xb') as output:
            child = subprocess.Popen([wine, str(binary)], env=env, stdout=output, stderr=subprocess.STDOUT)
            deadline = time.monotonic() + 20
            while child.poll() is None and time.monotonic() < deadline:
                for proc in Path('/proc').iterdir():
                    if not proc.name.isdecimal():
                        continue
                    observation = fixture_observation(proc, env['WINEPREFIX'], env['QUANTOWER_LAUNCH_SESSION'], str(binary))
                    if observation:
                        identity, modules = observation
                        identities[(identity['pid'], identity['start_ticks'])] = identity
                        mapped.update(modules)
                time.sleep(.02)
            # Preserve the failed own fixture on timeout; no process-name/group
            # kill, backend fallback or automatic retry is part of this harness.
            code = child.wait(timeout=5)
        rows = [json.loads(line) for line in log.read_text(errors='replace').splitlines() if line.startswith('{')]
        hashes = {name: hashlib.sha256(Path(name).read_bytes()).hexdigest() for name in mapped if Path(name).is_file()}
        report['cases'].append({'dpi': dpi, 'exit_code': code, 'rows': rows, 'result': rows[-1] if rows else None,'monitor_inventory':monitor_rows,
            'identities': list(identities.values()), 'mapped_comctl32': hashes,
            'exact_module_verified': bool(hashes) and set(hashes.values()) == {args.expected_comctl32_sha256}})
        (root / 'summary.json').write_text(json.dumps(report, indent=2) + '\n')
        if code != (0 if args.expect == 'candidate' else 1):
            raise RuntimeError('Unexpected own fixture exit; evidence retained, no retry.')
    report['coordinate_evidence_passed']=verify_coordinate_evidence(report,args.expect,args.family,args.expected_comctl32_sha256)
    report['geometry']=classify_targets(report)
    report['passed'] = verify_report(report, args.expect, args.family, args.expected_comctl32_sha256)
    (root / 'summary.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'passed': report['passed'], 'family': args.family, 'expect': args.expect}))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
