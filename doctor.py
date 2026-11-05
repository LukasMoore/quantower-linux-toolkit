#!/usr/bin/env python3
"""Read-only dependency checks. Never launches Wine, the app, or a GPU workload."""
from __future__ import annotations

import argparse
import ctypes.util
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import stat
import sys

import application_runtime
import profile_manager
import runtime_config


def check_environment(base, *, source_only=False, environment=None):
    base = Path(base).resolve()
    environment = os.environ if environment is None else environment
    checks = []

    def add(name, okay, detail, *, required=True):
        checks.append({'name': name, 'status': 'pass' if okay else ('fail' if required else 'warning'),
                       'detail': detail})

    add('linux', sys.platform == 'linux', 'Linux is required for /proc, fcntl and UID/pidfd ownership guards.')
    add('python', sys.version_info >= (3, 11), 'Python 3.11 or newer is required.')
    add('architecture', platform.machine().lower() in ('x86_64', 'amd64'), 'The runtime build targets Linux x86-64.')
    add('procfs', Path('/proc/self/stat').is_file() and Path('/proc/self/environ').is_file(),
        'Process identity checks require readable procfs entries for owned Wine processes.')
    pidfd_available = hasattr(os, 'pidfd_open') and hasattr(signal, 'pidfd_send_signal')
    pidfd_detail = 'Safe cleanup requires os.pidfd_open and signal.pidfd_send_signal.'
    if pidfd_available:
        try:
            descriptor = os.pidfd_open(os.getpid())
            os.close(descriptor)
        except OSError as error:
            pidfd_available = False
            pidfd_detail += ' The kernel self-handle probe failed: ' + str(error)
    add('pidfd', pidfd_available, pidfd_detail)
    config = None
    try:
        config = runtime_config.load(base, environment)
        add('launcher_config', True, 'Machine-local launcher configuration is valid, or portable defaults apply.')
    except (OSError, ValueError, TypeError) as error:
        add('launcher_config', False, str(error))
    if source_only:
        return {'schema_version': 1, 'scope': 'source prerequisites only',
                'okay': not any(c['status'] == 'fail' for c in checks), 'checks': checks}

    mode = None
    try:
        mode = profile_manager.load(base)
        add('runtime_manifest', True, 'All declared installed runtime hashes match; rendering profile: ' + mode)
    except (OSError, ValueError, RuntimeError, TypeError, KeyError) as error:
        add('runtime_manifest', False, str(error))
    runner = base / 'runner-quantower/opt/wine-devel/bin'
    for name in ('wine', 'wineserver'):
        path = runner / name
        add(name, path.is_file() and os.access(path, os.X_OK), 'Dedicated runtime executable: ' + str(path))
    try:
        application = application_runtime.load(base)
        add('application', (application.directory / 'Starter.exe').is_file(),
            'Selected AMP application executable must be installed by the user: ' + str(application.directory / 'Starter.exe'))
    except (OSError, ValueError, TypeError) as error:
        add('application', False, str(error))
    add('prefix', (base / 'prefix/user.reg').is_file(), 'A dedicated initialized 64-bit Wine prefix is required.')
    if mode == 'compatible':
        add('x11_display', bool(environment.get('DISPLAY')), 'Compatible profile requires a working DISPLAY connection.')
        helper = base / 'runner-quantower/opt/wine-devel/bin/xwininfo'
        add('xwininfo', (helper.is_file() and os.access(helper, os.X_OK)) or shutil.which('xwininfo') is not None,
            'Compatible-profile visibility checks use bundled or host xwininfo.')
    else:
        try:
            runtime_config.validate_gpu_runtime_path(base)
            add('wayland_socket_path', True, 'Installation path fits private Wayland socket limits.')
        except ValueError as error:
            add('wayland_socket_path', False, str(error))
        gamescope = base / 'gamescope/files/bin'
        add('xkb_compiler', Path('/usr/bin/xkbcomp').is_file(),
            'Debian Xwayland invokes the host /usr/bin/xkbcomp (x11-xkb-utils).')
        add('xkb_data', Path('/usr/share/X11/xkb/rules').is_dir(),
            'Debian Xwayland requires host keyboard data (xkb-data).')
        for name in ('gamescope-brokey', 'Xwayland'):
            path = gamescope / name
            add(name, path.is_file() and os.access(path, os.X_OK), 'Dedicated compositor executable: ' + str(path))
        add('vulkan_loader', ctypes.util.find_library('vulkan') is not None,
            'Vulkan loader is discoverable. This does not verify driver, extensions or device suitability.')
        if config is not None:
            try:
                host_runtime, socket = runtime_config.host_connections(config, environment)
                path = Path(socket)
                add('wayland_socket', path.exists() and stat.S_ISSOCK(path.stat().st_mode),
                    'Nested gamescope requires the configured host Wayland socket: ' + socket)
                pulse = config.pulse_server or environment.get('PULSE_SERVER') or 'unix:' + host_runtime + '/pulse/native'
                if pulse.startswith('unix:'):
                    endpoint = Path(pulse[5:])
                    add('pulse_audio', endpoint.exists() and stat.S_ISSOCK(endpoint.stat().st_mode),
                        'PulseAudio-compatible audio endpoint; optional for silent operation.', required=False)
                pipewire_dir = config.pipewire_runtime_dir or environment.get('PIPEWIRE_RUNTIME_DIR') or host_runtime
                add('pipewire_runtime', Path(pipewire_dir).is_dir(),
                    'Host PipeWire runtime directory; screen capture availability is not verified.', required=False)
            except (OSError, ValueError) as error:
                add('host_display', False, str(error))
    return {'schema_version': 1, 'scope': 'read-only installed-runtime checks', 'profile': mode,
            'okay': not any(c['status'] == 'fail' for c in checks), 'checks': checks,
            'limits': 'Does not start Wine or the application, connect to displays, enumerate Vulkan devices, install dependencies, or test trading/data connections.'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--source-only', action='store_true', help='Check source/tooling prerequisites without requiring an installed runtime or graphical session.')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    result = check_environment(args.base, source_only=args.source_only)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        for check in result['checks']:
            print(f"{check['status'].upper():7} {check['name']}: {check['detail']}")
        print('Result: ' + ('ready for the checked prerequisites' if result['okay'] else 'missing requirements; see failures above'))
    return 0 if result['okay'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
