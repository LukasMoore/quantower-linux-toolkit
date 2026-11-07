"""Validated machine-local display configuration and shell-free render commands."""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import stat
import subprocess
import sys
import time

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
    window_mode: str = 'nested'
    host_display: str | None = None
    wine_dpi: int | None = None

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
        validate_mode(value, 'gpu')
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


def validate_mode(config, rendering):
    """Validate the v1 display/profile matrix without touching the prefix."""
    if not isinstance(config.window_mode, str) or config.window_mode not in ('nested', 'integrated'):
        raise ValueError('window_mode must be nested or integrated.')
    if rendering not in ('gpu', 'compatible'):
        raise ValueError('Unknown rendering profile.')
    if config.host_display is not None and (not isinstance(config.host_display, str) or
            re.fullmatch(r':[0-9]+(?:\.[0-9]+)?', config.host_display) is None):
        raise ValueError('host_display must be null or a local X11 endpoint such as :0 or :0.0.')
    if config.wine_dpi is not None and (type(config.wine_dpi) is not int or not 96 <= config.wine_dpi <= 480):
        raise ValueError('wine_dpi must be null or an integer from 96 to 480.')
    if config.window_mode == 'nested' and (config.host_display is not None or config.wine_dpi is not None):
        raise ValueError('Nested mode requires host_display=null and wine_dpi=null.')
    if config.window_mode == 'integrated' and rendering != 'gpu':
        raise ValueError('Integrated windows require the GPU profile; compatible is unsupported in v1.')


validate_window_mode = validate_mode


def effective_wine_dpi(config, rendering):
    validate_mode(config, rendering)
    return profile_manager.effective_dpi(rendering, config.wine_dpi)


def integrated_display(base, config, environment=None):
    """Resolve a literal local host endpoint; never search for another display."""
    environment = os.environ if environment is None else environment
    validate_mode(config, 'gpu')
    if config.window_mode != 'integrated':
        raise ValueError('Integrated display checks require window_mode=integrated.')
    display = config.host_display if config.host_display is not None else environment.get('DISPLAY')
    if not isinstance(display, str) or re.fullmatch(r':[0-9]+(?:\.[0-9]+)?', display) is None:
        raise ValueError('Integrated windows require a local host DISPLAY such as :0; remote or missing endpoints are unsupported.')
    private = (Path(base).resolve() / 'runtime/gpu').resolve()
    nested = any(key.startswith('GAMESCOPE_') or key == 'WLR_XWAYLAND' for key in environment)
    for key in ('XDG_RUNTIME_DIR', 'WAYLAND_DISPLAY'):
        value = environment.get(key)
        if value and Path(value).is_absolute() and Path(value).resolve().is_relative_to(private):
            nested = True
    # Check overrides too: a clean caller environment must not let configuration
    # select the old compositor's private runtime or relative private socket.
    host_runtime = config.host_runtime_dir or environment.get('XDG_RUNTIME_DIR') or f'/run/user/{os.getuid()}'
    wayland = config.wayland_display or environment.get('WAYLAND_DISPLAY')
    for value in (host_runtime, wayland):
        if value and Path(value).is_absolute() and Path(value).resolve().is_relative_to(private):
            nested = True
    if host_runtime and wayland and not Path(wayland).is_absolute():
        if (Path(host_runtime) / wayland).resolve().is_relative_to(private):
            nested = True
    if nested:
        raise ValueError('Integrated windows must launch from the host desktop, outside a gamescope/private display environment.')
    if display.split('.')[0] in private_x11_endpoints(base):
        raise ValueError('The selected DISPLAY belongs to a dedicated private Xwayland; select the host desktop endpoint.')
    return display


def private_x11_endpoints(base):
    """Read known dedicated Xwayland argv endpoints without adopting processes.

    This is refusal evidence only: it grants no ownership or cleanup permission.
    Unreadable/unrelated host services are never targets of this function.
    """
    base = Path(base).resolve()
    expected = base / 'gamescope/files/bin/Xwayland'
    if not expected.exists():
        return set()
    expected = expected.resolve()
    prefix = os.fsencode('WINEPREFIX=' + str(base / 'prefix'))
    endpoints = set()
    for process in Path('/proc').iterdir():
        if not process.name.isdigit():
            continue
        try:
            if process.stat().st_uid != os.getuid() or (process / 'exe').resolve(strict=True) != expected:
                continue
            if prefix not in (process / 'environ').read_bytes().split(b'\0'):
                continue
            for argument in (process / 'cmdline').read_bytes().split(b'\0')[1:]:
                if re.fullmatch(rb':[0-9]+(?:\.[0-9]+)?', argument):
                    endpoints.add(argument.decode('ascii').split('.')[0])
        except (OSError, ValueError):
            continue
    return endpoints


def _registry_strings(text, key, names):
    """Read only selected REG_SZ values, refusing ambiguous Wine registry input.

    Wine writes doubled backslashes in section names. Unrelated registry bytes are
    never rewritten or interpreted as code. Case-insensitive duplicate sections or
    selected values are errors, including a shadowed preference.
    """
    header = '[' + key.replace('\\', '\\\\') + ']'
    sections = list(re.finditer(r'(?im)^' + re.escape(header) + r'[^\n]*(?:\n|$)', text))
    if len(sections) > 1:
        raise ValueError('Duplicate integrated-window preference section in Wine registry.')
    if not sections:
        return {}
    start = sections[0].end()
    following = re.search(r'(?m)^\[', text[start:])
    content = text[start:start + following.start()] if following else text[start:]
    result = {}
    for name in names:
        # Match even malformed assignment syntax so it cannot masquerade as absent.
        records = re.findall(r'(?im)^\s*"' + re.escape(name) + r'"[^\n]*', content)
        if len(records) > 1:
            raise ValueError('Duplicate integrated-window preference value in Wine registry.')
        if not records:
            continue
        match = re.fullmatch(r'\s*"' + re.escape(name) + r'"\s*=\s*("(?:[^"\\\x00-\x1f]|\\[\\"ntbrfa]|\\x[0-9a-fA-F]{4})*")\s*', records[0], flags=re.I)
        if not match:
            raise ValueError('Malformed or non-string integrated-window preference in Wine registry.')
        encoded = match.group(1)[1:-1]
        # Preferences accepted below are ASCII tokens; reject escape forms rather
        # than guessing at Wine's wider string grammar.
        if '\\' in encoded:
            raise ValueError('Ambiguous escaped integrated-window preference in Wine registry.')
        result[name.casefold()] = encoded
    return result


def validate_integrated_preferences(base):
    """Refuse preferences that would hide or unmanage the intended host windows."""
    registry = Path(base) / 'prefix/user.reg'
    text = registry.read_text(encoding='utf-8')
    if '\x00' in text or not text.startswith('WINE REGISTRY Version 2'):
        raise ValueError('Invalid Wine user registry for integrated-window preflight.')
    roots = (r'Software\Wine', r'Software\Wine\AppDefaults\Starter.exe')
    explorer = [_registry_strings(text, root + r'\Explorer', ('Desktop',)) for root in roots]
    effective = next((entry['desktop'] for entry in reversed(explorer) if 'desktop' in entry), None)
    if effective is not None:
        raise ValueError('An Explorer Desktop preference conflicts with integrated windows; remove it deliberately while stopped before retrying.')
    x11 = [_registry_strings(text, root + r'\X11 Driver', ('Managed',)) for root in roots]
    managed = next((entry['managed'] for entry in reversed(x11) if 'managed' in entry), None)
    if managed is not None and (not managed or managed[0] not in 'yYtT1'):
        raise ValueError('Integrated windows require Wine X11 Managed windows to be enabled.')
    # Explorer's graphics selection is global (Wine 11.20 desktop.c).
    drivers = _registry_strings(text, r'Software\Wine\Drivers', ('Graphics',))
    if 'graphics' in drivers:
        value = drivers['graphics']
        entries = value.split(',')
        if 'x11' not in entries or any(entry not in ('mac', 'x11', 'wayland') for entry in entries):
            raise ValueError('Wine Graphics preferences must include x11 and contain only mac,x11,wayland; null and unknown drivers are unsupported.')


def resolve_xwininfo(base, environment=None):
    """Resolve the passive probe and its controlled environment without executing it."""
    original = os.environ if environment is None else environment
    base = Path(base).resolve()
    bundled = base / 'runner-quantower/opt/wine-devel/bin/xwininfo'
    probe_environment = {key: original[key] for key in ('HOME', 'XAUTHORITY', 'XAUTHLOCALHOSTNAME', 'XDG_RUNTIME_DIR') if key in original}
    probe_environment.update(LC_ALL='C', PATH=os.defpath)
    if bundled.exists():
        manifest = json.loads((base / 'runtime-components/manifest.json').read_text())
        relative = str(bundled.relative_to(base))
        expected = manifest.get('required_files', {}).get(relative)
        if not isinstance(expected, str) or re.fullmatch(r'[0-9a-f]{64}', expected) is None:
            raise ValueError('The bundled xwininfo probe must be pinned in the runtime manifest.')
        if not bundled.is_file() or not os.access(bundled, os.X_OK) or not bundled.resolve().is_relative_to(base):
            raise ValueError('The bundled xwininfo probe is unavailable or escapes the installation.')
        with bundled.open('rb') as source:
            if hashlib.file_digest(source, 'sha256').hexdigest() != expected:
                raise ValueError('The bundled xwininfo probe failed checksum verification.')
        paths = wine_library_directories(base, selected_x11(base, original))
        if paths:
            probe_environment['LD_LIBRARY_PATH'] = os.pathsep.join(map(str, paths))
        return str(bundled), probe_environment
    # Only absolute entries in the caller's trusted tool path qualify. Never
    # execute a current-directory helper because PATH omitted/contained an entry.
    tool_path = os.pathsep.join(entry for entry in original.get('PATH', os.defpath).split(os.pathsep) if Path(entry).is_absolute())
    executable = shutil.which('xwininfo', path=tool_path)
    if not executable or not Path(executable).is_absolute():
        raise RuntimeError('Integrated windows require an available xwininfo passive connection probe.')
    return str(Path(executable).resolve()), probe_environment


class _ProbeOutputLimit(RuntimeError):
    pass


def _run_xwininfo_probe(arguments, environment, *, timeout=3, max_output=65536):
    """Read bounded metadata and reap only the subprocess created by this call.

    A pipe is appropriate for this short-lived owned probe. Wine/application
    output continues to use the supervisor's append-only regular file.
    """
    deadline = time.monotonic() + timeout
    selection = selectors.DefaultSelector()
    process = None
    output = bytearray()
    try:
        process = subprocess.Popen(arguments, env=environment, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   bufsize=0, close_fds=True)
        os.set_blocking(process.stdout.fileno(), False)
        selection.register(process.stdout, selectors.EVENT_READ)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not selection.select(remaining):
                raise subprocess.TimeoutExpired(arguments, timeout)
            # Read at most one byte beyond the accepted limit so an output flood
            # cannot allocate an unbounded communicate() buffer before refusal.
            chunk = os.read(process.stdout.fileno(), min(8192, max_output + 1 - len(output)))
            if not chunk:
                break
            output.extend(chunk)
            if len(output) > max_output:
                raise _ProbeOutputLimit('The host X11 connection probe exceeded its metadata limit.')
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(arguments, timeout)
        status = process.wait(timeout=remaining)
        return subprocess.CompletedProcess(arguments, status, bytes(output))
    except BaseException:
        # This Popen handle belongs exclusively to the new metadata probe. Never
        # signal a process group, Wine process, display service or session ledger.
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.wait()
        raise
    finally:
        selection.close()
        if process is not None and process.stdout is not None:
            process.stdout.close()


def integrated_preflight(base, config, environment=None):
    """Explicit bounded connection check, called under launch.lock before prepare.

    No Wine process, session token or prefix environment is passed to the probe.
    """
    environment = os.environ if environment is None else environment
    display = integrated_display(base, config, environment)
    validate_integrated_preferences(base)
    executable, probe_environment = resolve_xwininfo(base, environment)
    probe_environment['DISPLAY'] = display
    try:
        result = _run_xwininfo_probe([executable, '-root', '-stats'], probe_environment)
    except subprocess.TimeoutExpired:
        raise RuntimeError('The host X11 connection probe timed out; the application was not launched.') from None
    except OSError:
        raise RuntimeError('The host X11 connection probe could not execute; the application was not launched.') from None
    except _ProbeOutputLimit:
        raise RuntimeError('The host X11 connection probe exceeded its metadata limit; the application was not launched.') from None
    output = result.stdout
    if (result.returncode != 0 or not isinstance(output, bytes) or len(output) > 65536 or
            re.search(rb'xwininfo: Window id: 0x[0-9a-fA-F]+', output) is None or
            any(re.search(rb'(?m)^\s*' + field + rb':\s*[1-9][0-9]*\s*$', output) is None for field in (b'Width', b'Height'))):
        raise RuntimeError('The host X11 connection/authentication probe failed; the application was not launched.')
    return display


preflight_integrated = integrated_preflight


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
    validate_mode(config, 'gpu')
    if config.window_mode != 'nested':
        raise ValueError('The render helper supports nested mode only; use launch.sh for integrated windows.')
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


def build_integrated_launch(base, config, environment=None):
    """Build a direct host-X11 Wine launch, without connection or prefix writes."""
    base = Path(base).resolve()
    original = dict(os.environ if environment is None else environment)
    display = integrated_display(base, config, original)
    environment = dict(original)
    version = original.get('QUANTOWER_APP_VERSION') or application_runtime.load(base).version
    if not isinstance(version, str) or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', version):
        raise ValueError('Invalid AMP application version.')
    starter = rf'C:\AMPQuantowerInstalled\TradingPlatform\v{version}\Starter.exe'
    host_runtime = config.host_runtime_dir or original.get('XDG_RUNTIME_DIR') or f'/run/user/{os.getuid()}'
    if not Path(host_runtime).is_absolute():
        raise ValueError('The host runtime directory must be absolute.')
    dxvk_log = Path(original.get('DXVK_LOG_PATH', str(base / 'logs/gpu')))
    dxvk_cache = base / 'cache/gpu/dxvk'
    shader_cache = base / 'cache/gpu/shaders'
    cache = base / 'cache/gpu/xdg'
    user_config = base / 'config/gpu'
    trace = Path(original.get('QUANTOWER_CRASH_TRACE_DIR', str(base / 'logs/native-assert')))
    for key in list(environment):
        if key.startswith('GAMESCOPE_') or key in (
                'WLR_XWAYLAND', 'DOTNET_ROOT', 'DOTNET_ROOT_X64', 'DOTNET_STARTUP_HOOKS',
                'CONNECTION_OBSERVER_PROBE', 'WINEDLLPATH', 'WINELOADER', 'WINESERVER',
                'DXVK_CONFIG', 'DXVK_HUD', 'DXVK_FILTER_DEVICE_NAME', 'LD_PRELOAD'):
            environment.pop(key, None)
    environment.update(
        WINEPREFIX=str(base / 'prefix'), WINEARCH='win64', DISPLAY=display,
        WINEDEBUG=original.get('QUANTOWER_WINEDEBUG', '-all,err+all,warn+all'),
        WINEDLLOVERRIDES='winemenubuilder.exe=d;d3d9=n,b;winewayland.drv=d;winex11.drv=b',
        DXVK_ASYNC='0', DXVK_LOG_LEVEL='info', DXVK_LOG_PATH=str(dxvk_log),
        DXVK_SHADER_CACHE_PATH=str(dxvk_cache), DXVK_CONFIG_FILE=str(base / 'gpu-dxvk.conf'),
        PULSE_SERVER=config.pulse_server or original.get('PULSE_SERVER') or f'unix:{host_runtime}/pulse/native',
        PIPEWIRE_RUNTIME_DIR=config.pipewire_runtime_dir or original.get('PIPEWIRE_RUNTIME_DIR') or host_runtime,
        XDG_RUNTIME_DIR=host_runtime, XDG_CACHE_HOME=str(cache), XDG_CONFIG_HOME=str(user_config),
        __GL_SHADER_DISK_CACHE='1', __GL_SHADER_DISK_CACHE_PATH=str(shader_cache))
    if config.wayland_display is not None:
        environment['WAYLAND_DISPLAY'] = config.wayland_display
    if config.gpu_device_filter is not None:
        environment['DXVK_FILTER_DEVICE_NAME'] = config.gpu_device_filter
    hooks = original.get('QUANTOWER_DIAGNOSTIC_STARTUP_HOOKS')
    if hooks:
        environment['DOTNET_STARTUP_HOOKS'] = hooks
    apply_wine_library_environment(base, environment, selected_x11(base, original))
    apply_trace_environment(base, environment)
    directories = [dxvk_log, dxvk_cache, cache, user_config, shader_cache]
    if 'LD_PRELOAD' in environment:
        environment['QUANTOWER_CRASH_TRACE_DIR'] = str(trace)
        directories.append(trace)
    command = [str(base / 'runner-quantower/opt/wine-devel/bin/wine'), starter]
    return RenderLaunch(command, environment, directories, list(directories))


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
