"""Validated machine-local display configuration and shell-free render commands."""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re
import stat
import sys

import application_runtime
import profile_manager


@dataclass(frozen=True)
class RuntimeConfig:
    desktop_width: int = 1920
    desktop_height: int = 1080
    output_width: int | None = None
    output_height: int | None = None
    refresh_hz: int = 60
    idle_refresh_hz: int = 60
    fullscreen: bool = True
    gpu_device_filter: str | None = None
    wayland_display: str | None = None
    host_runtime_dir: str | None = None
    pulse_server: str | None = None
    pipewire_runtime_dir: str | None = None

    @classmethod
    def from_dict(cls, record):
        if not isinstance(record, dict) or type(record.get('schema_version')) is not int or record.get('schema_version') != 1:
            raise ValueError('Launcher configuration must have schema_version 1.')
        values = dict(record)
        values.pop('schema_version')
        unknown = set(values) - set(cls.__dataclass_fields__)
        if unknown:
            raise ValueError('Unknown launcher configuration field: ' + sorted(unknown)[0])
        value = cls(**values)
        for field, minimum in (('desktop_width', 320), ('desktop_height', 200),
                               ('output_width', 320), ('output_height', 200)):
            item = getattr(value, field)
            if item is None and field.startswith('output_'):
                continue
            if type(item) is not int or not minimum <= item <= 16384:
                raise ValueError(f'{field} must be an integer from {minimum} to 16384.')
        if (value.output_width is None) != (value.output_height is None):
            raise ValueError('Configure both output dimensions, or leave both null for automatic output sizing.')
        for field in ('refresh_hz', 'idle_refresh_hz'):
            item = getattr(value, field)
            if type(item) is not int or not 1 <= item <= 1000:
                raise ValueError(f'{field} must be an integer from 1 to 1000.')
        if value.idle_refresh_hz > value.refresh_hz:
            raise ValueError('idle_refresh_hz cannot exceed refresh_hz.')
        if type(value.fullscreen) is not bool:
            raise ValueError('fullscreen must be true or false.')
        for field in ('gpu_device_filter', 'wayland_display', 'host_runtime_dir',
                      'pulse_server', 'pipewire_runtime_dir'):
            item = getattr(value, field)
            if item is not None and (not isinstance(item, str) or not item or
                                     len(item) > 4096 or any(ord(c) < 32 for c in item)):
                raise ValueError(f'{field} must be null or a nonempty string without control characters.')
        for field in ('host_runtime_dir', 'pipewire_runtime_dir'):
            item = getattr(value, field)
            if item is not None and not Path(item).is_absolute():
                raise ValueError(f'{field} must be an absolute path.')
        return value

    def to_dict(self):
        return {'schema_version': 1, **asdict(self)}

    @property
    def wine_desktop(self):
        return f'/desktop=AMP-Quantower,{self.desktop_width}x{self.desktop_height}'


def load(base, environment=None):
    environment = os.environ if environment is None else environment
    explicit = environment.get('QUANTOWER_LAUNCHER_CONFIG')
    path = Path(explicit) if explicit else Path(base) / 'launcher-config.json'
    if explicit and not path.is_absolute():
        raise ValueError('QUANTOWER_LAUNCHER_CONFIG must be an absolute JSON path.')
    if not path.exists() and not explicit:
        return RuntimeConfig()
    if path.stat().st_size > 65536:
        raise ValueError('Launcher configuration exceeds 64 KiB.')
    return RuntimeConfig.from_dict(json.loads(path.read_text(encoding='utf-8')))


def host_connections(config, environment):
    runtime = config.host_runtime_dir or environment.get('XDG_RUNTIME_DIR') or f'/run/user/{os.getuid()}'
    if not Path(runtime).is_absolute():
        raise ValueError('The host runtime directory must be absolute.')
    display = config.wayland_display or environment.get('WAYLAND_DISPLAY') or 'wayland-0'
    socket = Path(display) if Path(display).is_absolute() else Path(runtime) / display
    return str(runtime), str(socket)


def declared_trace_library(base):
    """Trace injection is optional, but a manifest-declared file cannot disappear."""
    base = Path(base)
    manifest = json.loads((base / 'runtime-components/manifest.json').read_text())
    if not isinstance(manifest, dict) or not isinstance(manifest.get('required_files'), dict):
        raise ValueError('Invalid runtime component manifest.')
    relative = 'runtime-components/libquantower_assert_trace.so'
    if relative not in manifest.get('required_files', {}):
        return None
    path = base / relative
    if not path.is_file():
        raise RuntimeError('The manifest-declared crash diagnostic is missing.')
    return path


def apply_trace_environment(base, environment):
    environment.pop('LD_PRELOAD', None)
    path = declared_trace_library(base)
    if path is not None:
        environment['LD_PRELOAD'] = str(path)


def selected_x11(base, environment=None):
    """Select private Xlib only when its complete pair is declared and present."""
    environment = os.environ if environment is None else environment
    explicit = environment.get('QUANTOWER_X11_RUNTIME')
    if explicit not in (None, 'private', 'system'):
        raise ValueError('Unknown Quantower X11 runtime selection.')
    manifest_path = Path(base) / 'runtime-components/manifest.json'
    required = json.loads(manifest_path.read_text()).get('required_files', {}) if manifest_path.is_file() else {}
    names = ('runtime-components/x11/libX11.so.6', 'runtime-components/x11/libX11-xcb.so.1')
    declared = [name in required for name in names]
    if any(declared) and not all(declared):
        raise ValueError('The private X11 runtime must declare both matched libraries.')
    available = all(declared) and all((Path(base) / name).is_file() for name in names)
    if all(declared) and not available:
        raise ValueError('The manifest-declared private X11 runtime is missing a matched library.')
    if explicit == 'private' and not available:
        raise ValueError('The requested private X11 runtime is unavailable; install and pin both libraries first.')
    return explicit or ('private' if available else 'system')


def wine_library_directories(base, x11):
    """Keep Wine's bundled userspace closure separate from the compositor's."""
    base = Path(base).resolve()
    paths = []
    if x11 == 'private':
        paths.append(base / 'runtime-components/x11')
    directory = base / 'runner-quantower/opt/wine-devel/lib'
    if directory.is_dir():
        paths.append(directory)
    if any(os.pathsep in str(path) or any(ord(c) < 32 for c in str(path)) for path in paths):
        raise ValueError('Runtime paths cannot contain loader separators or control characters.')
    return paths


def validate_gpu_runtime_path(base):
    """Wayland uses Linux sockaddr_un: at most 107 path bytes plus NUL."""
    socket = Path(base).resolve() / 'runtime/gpu/gamescope-0'
    if len(os.fsencode(socket)) > 107:
        raise ValueError('Installation path is too long for private Wayland sockets; choose a shorter target directory.')


def apply_wine_library_environment(base, environment, x11=None):
    if x11 is None:
        x11 = selected_x11(base, environment)
    paths = wine_library_directories(base, x11)
    environment.pop('LD_LIBRARY_PATH', None)
    if paths:
        environment['LD_LIBRARY_PATH'] = os.pathsep.join(map(str, paths))
    environment.pop('XLOCALEDIR', None)
    locale_data = Path(base) / 'runtime-components/x11/share/X11/locale'
    if x11 == 'private' and locale_data.is_dir():
        environment['XLOCALEDIR'] = str(locale_data)
    helpers = Path(base).resolve() / 'runner-quantower/opt/wine-devel/bin'
    environment['PATH'] = str(helpers) + os.pathsep + environment.get('PATH', os.defpath)
    return x11


@dataclass
class RenderLaunch:
    arguments: list[str]
    environment: dict[str, str]
    directories: list[Path]
    private_directories: list[Path]

    def prepare(self):
        for directory in self.directories:
            directory.mkdir(parents=True, exist_ok=True)
        for directory in self.private_directories:
            directory.chmod(0o700)


def build_render_launch(base, config, environment=None):
    """Return argv and environment without executing processes or creating files."""
    base = Path(base).resolve()
    original = dict(os.environ if environment is None else environment)
    environment = dict(original)
    host_runtime, wayland_socket = host_connections(config, original)
    version = original.get('QUANTOWER_APP_VERSION') or application_runtime.load(base).version
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', version):
        raise ValueError('Invalid AMP application version.')
    starter = rf'C:\AMPQuantowerInstalled\TradingPlatform\v{version}\Starter.exe'
    x11 = selected_x11(base, original)
    gamescope = base / 'gamescope/files'
    runner = base / 'runner-quantower/opt/wine-devel/bin'
    gpu_runtime = base / 'runtime/gpu'
    validate_gpu_runtime_path(base)
    dxvk_log = Path(original.get('DXVK_LOG_PATH', str(base / 'logs/gpu')))
    dxvk_cache = base / 'cache/gpu/dxvk'
    shader_cache = base / 'cache/gpu/shaders'
    cache = base / 'cache/gpu/xdg'
    user_config = base / 'config/gpu'
    trace = Path(original.get('QUANTOWER_CRASH_TRACE_DIR', str(base / 'logs/native-assert')))
    for key in ('DISPLAY', 'DOTNET_ROOT', 'DOTNET_ROOT_X64', 'DOTNET_STARTUP_HOOKS',
                'CONNECTION_OBSERVER_PROBE', 'WINEDLLPATH', 'WINELOADER', 'WINESERVER',
                'DXVK_CONFIG', 'DXVK_HUD', 'DXVK_FILTER_DEVICE_NAME', 'LD_PRELOAD'):
        environment.pop(key, None)
    environment.update(
        WINEPREFIX=str(base / 'prefix'), WINEARCH='win64',
        WINEDEBUG=original.get('QUANTOWER_WINEDEBUG', '-all,err+all,warn+all'),
        WINEDLLOVERRIDES='winemenubuilder.exe=d;d3d9=n,b', DXVK_ASYNC='0', DXVK_LOG_LEVEL='info',
        DXVK_LOG_PATH=str(dxvk_log), DXVK_SHADER_CACHE_PATH=str(dxvk_cache),
        DXVK_CONFIG_FILE=str(base / 'gpu-dxvk.conf'),
        PULSE_SERVER=config.pulse_server or original.get('PULSE_SERVER') or f'unix:{host_runtime}/pulse/native',
        PIPEWIRE_RUNTIME_DIR=config.pipewire_runtime_dir or original.get('PIPEWIRE_RUNTIME_DIR') or host_runtime,
        XDG_RUNTIME_DIR=str(gpu_runtime), XDG_CACHE_HOME=str(cache), XDG_CONFIG_HOME=str(user_config),
        __GL_SHADER_DISK_CACHE='1', __GL_SHADER_DISK_CACHE_PATH=str(shader_cache),
        WAYLAND_DISPLAY=wayland_socket, LD_LIBRARY_PATH=str(gamescope / 'lib'),
        PATH=str(gamescope / 'bin') + os.pathsep + original.get('PATH', os.defpath),
        WLR_XWAYLAND=str(gamescope / 'bin/Xwayland'),
        GAMESCOPE_SCRIPT_PATH=str(gamescope / 'share/gamescope/scripts'))
    if config.gpu_device_filter is not None:
        environment['DXVK_FILTER_DEVICE_NAME'] = config.gpu_device_filter
    hooks = original.get('QUANTOWER_DIAGNOSTIC_STARTUP_HOOKS')
    if hooks:
        environment['DOTNET_STARTUP_HOOKS'] = hooks
    command = [str(gamescope / 'bin/gamescope-brokey'), '--backend', 'wayland']
    if config.fullscreen:
        command.append('--fullscreen')
    command.extend(['-w', str(config.desktop_width), '-h', str(config.desktop_height)])
    if config.output_width is not None:
        command.extend(['-W', str(config.output_width), '-H', str(config.output_height)])
    command.extend(['-r', str(config.refresh_hz), '-o', str(config.idle_refresh_hz), '-S', 'fit', '-F', 'linear'])
    command.extend(['--', '/usr/bin/env', '-u', 'LD_LIBRARY_PATH', '-u', 'WLR_XWAYLAND', '-u', 'GAMESCOPE_SCRIPT_PATH'])
    wine_libraries = wine_library_directories(base, x11)
    if wine_libraries:
        command.append('LD_LIBRARY_PATH=' + os.pathsep.join(map(str, wine_libraries)))
    locale_data = base / 'runtime-components/x11/share/X11/locale'
    if x11 == 'private' and locale_data.is_dir():
        command.append('XLOCALEDIR=' + str(locale_data))
    command.append('PATH=' + str(runner) + os.pathsep + original.get('PATH', os.defpath))
    native_trace = declared_trace_library(base)
    directories = [dxvk_log, dxvk_cache, gpu_runtime, cache, user_config, shader_cache]
    private_directories = [gpu_runtime]
    if native_trace is not None:
        command.extend(['LD_PRELOAD=' + str(native_trace), 'QUANTOWER_CRASH_TRACE_DIR=' + str(trace)])
        directories.append(trace)
        private_directories.append(trace)
    command.extend([str(runner / 'wine'), 'explorer.exe', config.wine_desktop, starter])
    return RenderLaunch(command, environment, directories, private_directories)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('render', 'show'))
    parser.add_argument('--base', type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args(argv)
    try:
        config = load(args.base)
        if args.operation == 'show':
            print(json.dumps(config.to_dict(), indent=2))
            return 0
        if profile_manager.load(args.base) != 'gpu':
            raise RuntimeError('The render helper requires the GPU profile; use launch.sh for normal startup.')
        launch = build_render_launch(args.base, config)
        socket = Path(launch.environment['WAYLAND_DISPLAY'])
        if not socket.exists() or not stat.S_ISSOCK(socket.stat().st_mode):
            raise RuntimeError('The configured desktop Wayland socket is unavailable.')
        for executable in (launch.arguments[0], str(args.base / 'runner-quantower/opt/wine-devel/bin/wine')):
            if not os.access(executable, os.X_OK):
                raise RuntimeError('A required dedicated graphics component is missing or not executable.')
        os.umask(0o077)
        launch.prepare()
        os.execvpe(launch.arguments[0], launch.arguments, launch.environment)
    except (OSError, ValueError, RuntimeError, TypeError) as error:
        print(f'Cannot launch renderer: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
