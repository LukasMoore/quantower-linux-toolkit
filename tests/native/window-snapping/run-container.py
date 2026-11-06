#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Run own-window regressions only in an explicit, networkless disposable container."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import select
import subprocess

EDGES = ('move', 'left', 'right', 'top', 'topleft', 'topright', 'bottom',
         'bottomleft', 'bottomright')
SUPPLEMENTAL_STICKY = {
    'child-full-pointer-bottomright-reverse', 'child-full-pointer-bottomright-snap',
    'child-full-pointer-move-reverse', 'child-full-pointer-move-snap',
    'child-full-pointer-right-reverse', 'child-full-pointer-right-snap',
    'child-outline-keyboard-border-snap', 'child-outline-pointer-bottomright-reverse',
    'child-outline-pointer-bottomright-snap', 'child-outline-pointer-move-reverse',
    'child-outline-pointer-move-snap', 'child-outline-pointer-right-reverse',
    'child-outline-pointer-right-snap', 'top-full-keyboard-border-snap',
}


def cases(suite):
    result = []
    if suite in ('main', 'snap-hold'):
        for edge in EDGES:
            matrix = [('snap', 1)] if suite == 'snap-hold' else [
                ('nosnap', 1), ('snap', 250), ('snap', 1), ('reverse', 1), ('cancel', 1)]
            if suite == 'main' and edge != 'move':
                matrix += [('max', 1), ('min', 1)]
            for scenario, step in matrix:
                result.append({'name': f'{edge}-{scenario}-{step}',
                               'exe': 'snap-hold.exe' if suite == 'snap-hold' else 'snap-eight.exe',
                               'args': [edge, scenario, str(step)],
                               'sticky': suite == 'main' and (scenario == 'reverse' or
                                                              (scenario == 'snap' and step == 1)),
                               'must_snap': suite == 'snap-hold' or
                                            (suite == 'main' and scenario in ('reverse', 'cancel')) or
                                            (suite == 'main' and scenario == 'snap' and step == 1)})
    elif suite == 'supplemental':
        matrix = [(scope, op, scenario)
                  for scope in ('child-full', 'child-outline')
                  for op in ('pointer-move', 'pointer-right', 'pointer-bottomright')
                  for scenario in ('nosnap', 'snap', 'reverse', 'cancel')]
        matrix += [(scope, 'keyboard-border', scenario)
                   for scope in ('top-full', 'child-outline')
                   for scenario in ('nosnap', 'snap', 'cancel')]
        matrix += [(scope, 'keyboard-right', scenario)
                   for scope in ('top-full', 'child-full', 'child-outline')
                   for scenario in ('nosnap', 'cancel')]
        for scope, op, scenario in matrix:
            name = '-'.join((scope, op, scenario))
            result.append({'name': name, 'exe': 'child-keyboard-v3.exe',
                           'args': [scope, op, scenario, '96'],
                           'sticky': name in SUPPLEMENTAL_STICKY, 'must_snap': False})
        # A bounded one-key path replaces unsupported multi-key child/full cases.
        for scenario in ('nosnap', 'cancel'):
            result.append({'name': 'child-full-keyboard-border-' + scenario,
                           'exe': 'child-keyboard-v4.exe',
                           'args': ['child-full', 'keyboard-border', scenario, '96'],
                           'sticky': False, 'must_snap': False})
    else:
        raise ValueError('Unknown suite.')
    return result


def valid_probe(probe, suite):
    if not isinstance(probe, dict):
        return False
    if suite == 'supplemental':
        return probe.get('injection_valid') is True
    return (probe.get('failures') == 0 and probe.get('samples', 0) > 0 and
            probe.get('enters') == probe.get('exits') == 1 and
            probe.get('capture_seen') is True)


def verify(report, suite, expected):
    """Reject invalid delivery separately from the expected baseline snap failures."""
    if expected not in ('baseline', 'candidate'):
        return False
    wanted = cases(suite)
    rows = report.get('cases', [])
    if len(rows) != len(wanted) or report.get('fixtures_unchanged') is not True:
        return False
    for row, case in zip(rows, wanted):
        probe = row.get('probe')
        if row.get('name') != case['name'] or row.get('exit_code') != 0 or not valid_probe(probe, suite):
            return False
        exact = probe.get('exact_expected_geometry') if suite == 'supplemental' else probe.get('passed')
        should_pass = expected == 'candidate' or not case['sticky']
        if exact is not should_pass or probe.get('passed') is not should_pass:
            return False
        if case['must_snap'] and probe.get('snapped_samples', 0) <= 0:
            return False
    return True


def container_guard():
    if not Path('/.dockerenv').is_file() or os.getuid() == 0 or os.getenv('QTK_SYNTHETIC_DATAONLY') != '1':
        raise RuntimeError('Use an explicit non-root disposable data-only container.')
    if {p.name for p in Path('/sys/class/net').iterdir()} != {'lo'}:
        raise RuntimeError('Use --network none; external network interfaces are refused.')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite', choices=('main', 'snap-hold', 'supplemental'), required=True)
    parser.add_argument('--expect', choices=('baseline', 'candidate'), required=True)
    parser.add_argument('--label', required=True)
    parser.add_argument('--legacy-wine64', action='store_true',
                        help='Use lib/wine/x86_64-unix/wine for an older dual-Unix-runtime tree.')
    args = parser.parse_args()
    container_guard()
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,59}', args.label):
        raise ValueError('Use a simple label of at most sixty characters.')
    output = Path('/test') / args.label
    if output.exists() or output.is_symlink():
        raise FileExistsError('Use a new result directory; old prefixes are never reused.')
    os.umask(0o077)
    output.mkdir(mode=0o700)
    wanted = cases(args.suite)
    executables = {row['exe'] for row in wanted}
    fixture_hashes = {name: sha(Path('/probe') / name) for name in executables}
    read, write = os.pipe()
    with (output / 'xvfb.log').open('xb') as log:
        subprocess.Popen(['Xvfb', '-displayfd', str(write), '-screen', '0', '4096x2560x24',
                          '-nolisten', 'tcp', '-noreset'], pass_fds=(write,),
                         stdout=log, stderr=subprocess.STDOUT)
    os.close(write)
    if not select.select([read], [], [], 20)[0]:
        raise RuntimeError('The new own Xvfb did not start.')
    number = os.read(read, 32).decode().strip()
    os.close(read)
    if not number.isdecimal():
        raise RuntimeError('Unexpected display identifier.')
    wine = '/runtime/wine/lib/wine/x86_64-unix/wine' if args.legacy_wine64 else '/runtime/wine/bin/wine'
    env = {'PATH': '/runtime/wine/bin:' + os.defpath, 'DISPLAY': ':' + number,
           'WINEPREFIX': str(output / 'prefix'), 'WINEARCH': 'win64', 'WINEDEBUG': '-all',
           'WINEDLLOVERRIDES': 'winemenubuilder.exe=d;mscoree,mshtml=d',
           'LD_LIBRARY_PATH': '/runtime/x11/lib:/runtime/wine/lib',
           'XLOCALEDIR': '/runtime/x11/share/X11/locale', 'QTK_SYNTHETIC_DATAONLY': '1',
           'LANG': 'C', 'LC_ALL': 'C'}
    # Preserve the container's login identity; Wine requires its existing HOME.
    for key in ('HOME', 'USER', 'LOGNAME'):
        if key in os.environ:
            env[key] = os.environ[key]
    with (output / 'wineboot.log').open('xb') as log:
        subprocess.run([wine, 'wineboot.exe', '--init'], env=env, stdout=log,
                       stderr=subprocess.STDOUT, timeout=180, check=True)
    report = {'synthetic_only': True, 'suite': args.suite, 'expected': args.expect,
              'network': 'none', 'fixture_hashes': fixture_hashes,
              'win32u_so_sha256': sha(Path('/runtime/wine/lib/wine/x86_64-unix/win32u.so')),
              'wine_version': subprocess.check_output([wine, '--version'], env=env, text=True).strip(),
              'cases': []}
    for case in wanted:
        path = output / (case['name'] + '.log')
        with path.open('xb') as log:
            result = subprocess.run([wine, '/probe/' + case['exe'], *case['args']], env=env,
                                    stdout=log, stderr=subprocess.STDOUT, timeout=25)
        records = [json.loads(line) for line in path.read_text(errors='replace').splitlines()
                   if line.startswith('{')]
        report['cases'].append({'name': case['name'], 'exit_code': result.returncode,
                                'probe': records[-1] if records else None})
        (output / 'summary.json').write_text(json.dumps(report, indent=2) + '\n')
    report['fixtures_unchanged'] = all(sha(Path('/probe') / name) == digest
                                       for name, digest in fixture_hashes.items())
    report['passed'] = verify(report, args.suite, args.expect)
    (output / 'summary.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'suite': args.suite, 'expected': args.expect, 'cases': len(wanted),
                      'passed': report['passed'], 'fixtures_unchanged': report['fixtures_unchanged']}))
    raise SystemExit(0 if report['passed'] else 1)


if __name__ == '__main__':
    main()
