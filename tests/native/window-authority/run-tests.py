#!/usr/bin/env python3
"""Opt-in actual-Wine-source regression tests with inert Win32/X11 boundaries.

This command compiles and executes only generated mock-boundary test programs.
It never launches Wine, a display server, or an installed application.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parents[2]
PLACEMENT_FUNCTIONS = (
    'is_window_resizable', 'use_native_window_placement',
    'window_needs_net_wm_state_change_delay', 'window_set_net_wm_fullscreen_monitors',
    'update_net_wm_fullscreen_monitors', 'window_set_net_wm_state', 'update_net_wm_states',
    'window_update_client_state', 'window_update_client_config',
    'window_request_desired_state', 'handle_state_change', 'window_net_wm_state_notify',
)
SOURCE_FILES = ('dlls/user32/winproc.c', 'dlls/winex11.drv/window.c')


def extract_function(source, name, optional=False):
    """Extract one function while respecting braces in C strings/comments."""
    match = re.search(r'^(?:static )?(?:inline )?(?:BOOL|UINT|void|LRESULT|LONG|enum qtk_caption_action) '
                      + re.escape(name) + r'\([^;]*?\)\n\{', source, re.M)
    if not match:
        if optional:
            return ''
        raise ValueError('Missing actual source function: ' + name)
    index = source.index('{', match.start())
    depth, state = 0, 'code'
    while index < len(source):
        char, following = source[index], source[index + 1:index + 2]
        if state == 'line':
            if char == '\n':
                state = 'code'
        elif state == 'comment':
            if char == '*' and following == '/':
                state = 'code'
                index += 1
        elif state in ('string', 'char'):
            if char == '\\':
                index += 1
            elif char == ('"' if state == 'string' else "'"):
                state = 'code'
        elif char == '/' and following == '*':
            state = 'comment'
            index += 1
        elif char == '/' and following == '/':
            state = 'line'
            index += 1
        elif char == '"':
            state = 'string'
        elif char == "'":
            state = 'char'
        elif char == '{':
            depth += 1
        elif char == '}':
            depth -= 1
            if not depth:
                return source[match.start():index + 1] + '\n'
        index += 1
    raise ValueError('Unterminated actual source function: ' + name)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_paths(baseline, candidate, output):
    baseline, candidate, output = (Path(p).absolute() for p in (baseline, candidate, output))
    if baseline.resolve() == candidate.resolve():
        raise ValueError('Baseline and candidate must be separate source trees')
    for source in (baseline, candidate):
        if not source.is_dir():
            raise ValueError('Source root must be an existing Wine source directory')
        for relative in SOURCE_FILES:
            path = source / relative
            if path.is_symlink() or not path.is_file():
                raise ValueError('Source input must be a regular nonsymlink C file: ' + relative)
    if os.path.lexists(output):
        raise FileExistsError('Output must be a fresh nonexistent scratch directory')
    if not output.parent.is_dir():
        raise ValueError('Output parent must already exist')
    resolved = output.resolve()
    for source in (baseline.resolve(), candidate.resolve(), REPOSITORY):
        if resolved == source or source in resolved.parents:
            raise ValueError('Output must be outside source trees and this repository')
    return baseline.resolve(), candidate.resolve(), resolved


def source_bodies(baseline, candidate):
    user = (candidate / SOURCE_FILES[0]).read_text()
    driver = (candidate / SOURCE_FILES[1]).read_text()
    base_user = (baseline / SOURCE_FILES[0]).read_text()
    base_driver = (baseline / SOURCE_FILES[1]).read_text()
    caption_block = user[user.index('static const WCHAR qtk_caption_move_option'):
                         user.index('/* call a 32-bit window procedure */')]
    properties = driver[driver.index('static const WCHAR qtk_native_placement_prop'):
                        driver.index('static const WCHAR clip_window_prop')]
    bodies = {
        'user32': (user, 'user32', caption_block + '\n' + extract_function(user, 'call_window_proc')
                   + '\n' + extract_function(user, 'dispatch_win_proc_params')),
        'driver': (driver, 'driver', properties + '\n' + '\n'.join(
            extract_function(driver, name) for name in ('use_native_window_placement',
            'use_native_caption_move', 'set_native_caption_prop', 'sync_native_caption_props',
            'X11DRV_SysCommand'))),
        'baseline-user32': (base_user, 'user32', extract_function(base_user, 'call_window_proc')
                           + '\n' + extract_function(base_user, 'dispatch_win_proc_params')),
        'baseline-driver': (base_driver, 'driver', extract_function(base_driver, 'X11DRV_SysCommand')),
    }
    for name, source, is_candidate in (('placement', driver, True), ('baseline-placement', base_driver, False)):
        body = '#define CANDIDATE ' + str(int(is_candidate)) + '\n'
        body += '\n'.join(extract_function(source, function,
                         optional=not is_candidate and function == 'use_native_window_placement')
                          for function in PLACEMENT_FUNCTIONS)
        bodies[name] = (source, 'placement', body)
    return bodies, driver


def run(baseline, candidate, output, compiler='gcc'):
    baseline, candidate, output = validate_paths(baseline, candidate, output)
    cc = shutil.which(compiler)
    if not cc:
        raise ValueError('C compiler not found: ' + compiler)
    bodies, driver = source_bodies(baseline, candidate)
    output.mkdir(mode=0o700)
    report = {
        'schema_version': 1,
        'method': 'Actual source bodies compiled with original inert Win32/X11 mocks',
        'no_display_or_application_actions': True,
        'known_regression': 'Custom titlebar controls sharing NC161/HTCAPTION are intercepted; '
                            'passing tests reproduce this regression and do not establish button preservation.',
        'compiler': subprocess.check_output([cc, '--version'], text=True).splitlines()[0],
        'source_pins': {label: {relative: digest(root / relative) for relative in SOURCE_FILES}
                        for label, root in (('baseline', baseline), ('candidate', candidate))},
        'harness_pins': {p.name: digest(p) for p in sorted(HERE.iterdir()) if p.is_file()},
        'results': {},
    }
    for role, (source, prefix_role, body) in bodies.items():
        generated, binary = output / (role + '.c'), output / role
        notice = source[:source.index('*/') + 2]
        cases_name = 'placement-cases.c' if prefix_role == 'placement' else role + '-cases.c'
        # Upstream notice follows the candidate/baseline code into the generated artifact.
        generated.write_text(notice + '\n/* Actual extracted Wine bodies retain the notice above. */\n'
                             + (HERE / (prefix_role + '-prefix.h')).read_text() + '\n'
                             + body + '\n' + (HERE / cases_name).read_text())
        build = subprocess.run([cc, '-std=c11', '-D_POSIX_C_SOURCE=200809L', '-Wall', '-Wextra',
                                '-Werror', '-Wno-unused-parameter', '-Wno-unused-function',
                                '-Wno-unused-variable', '-O2', str(generated), '-o', str(binary)],
                               capture_output=True, text=True, timeout=60)
        (output / (role + '-build.log')).write_text(build.stdout + build.stderr)
        if build.returncode:
            raise RuntimeError('Mock-boundary compilation failed: ' + role)
        environment = {key: value for key, value in os.environ.items()
                       if key not in ('DISPLAY', 'WAYLAND_DISPLAY', 'WINEPREFIX', 'WINEDEBUG')}
        result = subprocess.run([str(binary)], capture_output=True, text=True,
                                timeout=10, env=environment)
        (output / (role + '-run.log')).write_text(result.stdout + result.stderr)
        if result.returncode:
            raise RuntimeError('Actual-function regression failed: ' + role)
        report['results'][role] = result.stdout.strip()
        print(result.stdout.strip())
    lifecycle = {}
    for name, token in (
        ('window_set_managed', 'data->managed = new_managed;'),
        ('make_window_embedded', 'data->embedded = TRUE;'),
        ('create_whole_window', 'NtUserSetProp( data->hwnd, whole_window_prop'),
        ('create_desktop_win_data', 'NtUserSetProp( data->hwnd, whole_window_prop'),
    ):
        body = extract_function(driver, name)
        if body.index('sync_native_caption_props( data );') <= body.index(token):
            raise AssertionError('Marker publication precedes lifecycle state: ' + name)
        lifecycle[name] = 'marker sync follows state/property publication'
    destroy = extract_function(driver, 'destroy_whole_window')
    for name in ('qtk_caption_move_prop', 'qtk_native_placement_prop'):
        if destroy.index('set_native_caption_prop( data->hwnd, ' + name + ', 0 );') >= destroy.index('if (!data->whole_window)'):
            raise AssertionError('Marker clear follows a destruction early return')
    lifecycle['destroy_whole_window'] = 'both markers clear before any early return'
    report['lifecycle_source_checks'] = lifecycle
    report['artifacts'] = {p.name: {'sha256': digest(p), 'bytes': p.stat().st_size}
                           for p in output.iterdir() if p.is_file()}
    (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    for path in output.iterdir():
        path.chmod(0o700 if path.name in bodies else 0o600)
    print('KNOWN REGRESSION: custom HTCAPTION titlebar controls remain broken; see README.md.')
    return report


def argument_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline-source', required=True, type=Path,
                        help='Separate Wine tree patched through 0009, before 0010/0011')
    parser.add_argument('--candidate-source', required=True, type=Path,
                        help='Wine tree prepared from the pinned source and all current patches')
    parser.add_argument('--output', required=True, type=Path,
                        help='Fresh nonexistent scratch directory outside source/repository trees')
    parser.add_argument('--cc', default='gcc', help='C compiler executable (default: gcc)')
    return parser


def main(argv=None):
    arguments = argument_parser().parse_args(argv)
    os.umask(0o077)
    return run(arguments.baseline_source, arguments.candidate_source,
               arguments.output, arguments.cc)


if __name__ == '__main__':
    main()
