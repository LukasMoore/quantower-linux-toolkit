"""Validate portable configuration and argv/environment boundaries without Wine."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
import doctor
import runtime_config


class ConfigurationTests(unittest.TestCase):
    def test_defaults_select_no_specific_gpu_or_physical_output(self):
        with tempfile.TemporaryDirectory() as directory:
            config = runtime_config.load(directory, {})
            self.assertIsNone(config.gpu_device_filter)
            self.assertIsNone(config.output_width)
            self.assertEqual(config.wine_desktop, '/desktop=AMP-Quantower,1920x1080')

    def test_invalid_machine_configuration_is_rejected(self):
        bad = [[], {'schema_version': True}, {'schema_version': 2},
               {'schema_version': 1, 'desktop_width': True},
               {'schema_version': 1, 'desktop_height': -1},
               {'schema_version': 1, 'desktop_width': 999999},
               {'schema_version': 1, 'output_width': 3840},
               {'schema_version': 1, 'refresh_hz': 0},
               {'schema_version': 1, 'refresh_hz': 30, 'idle_refresh_hz': 60},
               {'schema_version': 1, 'fullscreen': 'yes'},
               {'schema_version': 1, 'gpu_device_filter': 'GPU\nKEY=value'},
               {'schema_version': 1, 'host_runtime_dir': 'relative'},
               {'schema_version': 1, 'pipewire_runtime_dir': 'relative'},
               {'schema_version': 1, 'extra_typo': 12}]
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ValueError):
                runtime_config.RuntimeConfig.from_dict(value)

    def test_local_config_and_explicit_absolute_override(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            path = base / 'my machine.json'
            path.write_text(json.dumps({'schema_version': 1, 'desktop_width': 2560, 'desktop_height': 1440}))
            loaded = runtime_config.load(base, {'QUANTOWER_LAUNCHER_CONFIG': str(path)})
            self.assertEqual((loaded.desktop_width, loaded.desktop_height), (2560, 1440))
            with self.assertRaises(ValueError):
                runtime_config.load(base, {'QUANTOWER_LAUNCHER_CONFIG': 'relative.json'})
            with self.assertRaises(FileNotFoundError):
                runtime_config.load(base, {'QUANTOWER_LAUNCHER_CONFIG': str(base / 'missing.json')})

    def test_oversized_and_malformed_json_fail_clearly(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'launcher-config.json'
            path.write_text(' ' * 65537)
            with self.assertRaisesRegex(ValueError, '64 KiB'):
                runtime_config.load(directory, {})
            path.write_text('{bad')
            with self.assertRaises(ValueError):
                runtime_config.load(directory, {})


class RenderCommandTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='portable runtime with spaces ')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        components = self.base / 'runtime-components'
        components.mkdir()
        (components / 'manifest.json').write_text(json.dumps({'required_files': {}}))
        self.environment = {'QUANTOWER_APP_VERSION': '1.147.5',
                            'XDG_RUNTIME_DIR': '/run/user/123', 'WAYLAND_DISPLAY': 'wayland-7'}

    def test_default_command_preserves_hardware_autodetection(self):
        environment = dict(self.environment, DXVK_FILTER_DEVICE_NAME='old machine', LD_PRELOAD='old preload', DISPLAY=':old')
        launch = runtime_config.build_render_launch(self.base, runtime_config.RuntimeConfig(), environment)
        self.assertNotIn('-W', launch.arguments)
        self.assertNotIn('-H', launch.arguments)
        self.assertNotIn('DXVK_FILTER_DEVICE_NAME', launch.environment)
        self.assertNotIn('LD_PRELOAD', launch.environment)
        self.assertNotIn('DISPLAY', launch.environment)
        self.assertFalse(any(a.startswith('LD_LIBRARY_PATH=') for a in launch.arguments))
        self.assertEqual(launch.environment['WAYLAND_DISPLAY'], '/run/user/123/wayland-7')
        self.assertFalse((self.base / 'runtime').exists(), 'building argv must not create files')

    def test_machine_overrides_are_literal_arguments_and_environment(self):
        sentinel = self.base / 'must never be created'
        hostile = 'GPU $(touch ' + str(sentinel) + '); "literal"'
        config = runtime_config.RuntimeConfig.from_dict({'schema_version': 1,
            'desktop_width': 2192, 'desktop_height': 1233, 'output_width': 3840,
            'output_height': 2160, 'refresh_hz': 144, 'idle_refresh_hz': 60,
            'fullscreen': False, 'gpu_device_filter': hostile,
            'pulse_server': 'unix:/custom/audio socket', 'pipewire_runtime_dir': '/custom/pipewire'})
        launch = runtime_config.build_render_launch(self.base, config, self.environment)
        self.assertEqual(launch.environment['DXVK_FILTER_DEVICE_NAME'], hostile)
        self.assertEqual(launch.arguments[launch.arguments.index('-W') + 1], '3840')
        self.assertEqual(launch.arguments[launch.arguments.index('-r') + 1], '144')
        self.assertNotIn('--fullscreen', launch.arguments)
        self.assertEqual(launch.environment['PULSE_SERVER'], 'unix:/custom/audio socket')
        self.assertEqual(launch.environment['PIPEWIRE_RUNTIME_DIR'], '/custom/pipewire')
        self.assertIn('/desktop=AMP-Quantower,2192x1233', launch.arguments)
        self.assertFalse(sentinel.exists())

    def test_explicit_hooks_only_and_system_library_override(self):
        environment = dict(self.environment, DOTNET_STARTUP_HOOKS='inherited-hook', QUANTOWER_X11_RUNTIME='system')
        launch = runtime_config.build_render_launch(self.base, runtime_config.RuntimeConfig(), environment)
        self.assertNotIn('DOTNET_STARTUP_HOOKS', launch.environment)
        self.assertFalse(any(a.startswith('LD_LIBRARY_PATH=') for a in launch.arguments))
        environment['QUANTOWER_DIAGNOSTIC_STARTUP_HOOKS'] = r'C:\maintenance\observer.dll'
        launch = runtime_config.build_render_launch(self.base, runtime_config.RuntimeConfig(), environment)
        self.assertEqual(launch.environment['DOTNET_STARTUP_HOOKS'], r'C:\maintenance\observer.dll')

    def test_optional_trace_never_loads_an_undeclared_file(self):
        trace = self.base / 'runtime-components/libquantower_assert_trace.so'
        trace.touch()
        launch = runtime_config.build_render_launch(self.base, runtime_config.RuntimeConfig(), self.environment)
        self.assertFalse(any(a.startswith('LD_PRELOAD=') for a in launch.arguments))
        (self.base / 'runtime-components/manifest.json').write_text(json.dumps({'required_files': {
            'runtime-components/libquantower_assert_trace.so': 'test-only'}}))
        launch = runtime_config.build_render_launch(self.base, runtime_config.RuntimeConfig(), self.environment)
        self.assertIn('LD_PRELOAD=' + str(trace), launch.arguments)
        trace.unlink()
        with self.assertRaisesRegex(RuntimeError, 'manifest-declared'):
            runtime_config.build_render_launch(self.base, runtime_config.RuntimeConfig(), self.environment)

    def test_invalid_version_and_library_selection_fail_before_launch(self):
        for key, value in [('QUANTOWER_APP_VERSION', '1.2;command'), ('QUANTOWER_X11_RUNTIME', 'invalid')]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                runtime_config.build_render_launch(self.base, runtime_config.RuntimeConfig(), dict(self.environment, **{key: value}))

    def test_private_xlib_requires_a_complete_declared_pair(self):
        self.assertEqual(runtime_config.selected_x11(self.base, {}), 'system')
        with self.assertRaisesRegex(ValueError, 'unavailable'):
            runtime_config.selected_x11(self.base, {'QUANTOWER_X11_RUNTIME': 'private'})
        manifest = self.base / 'runtime-components/manifest.json'
        first = 'runtime-components/x11/libX11.so.6'
        second = 'runtime-components/x11/libX11-xcb.so.1'
        manifest.write_text(json.dumps({'required_files': {first: 'test'}}))
        with self.assertRaisesRegex(ValueError, 'both'):
            runtime_config.selected_x11(self.base, {})
        manifest.write_text(json.dumps({'required_files': {first: 'test', second: 'test'}}))
        with self.assertRaisesRegex(ValueError, 'missing'):
            runtime_config.selected_x11(self.base, {})
        for name in (first, second):
            path = self.base / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
        self.assertEqual(runtime_config.selected_x11(self.base, {}), 'private')
        self.assertEqual(runtime_config.selected_x11(self.base, {'QUANTOWER_X11_RUNTIME': 'system'}), 'system')

    def test_host_audio_override_is_preserved_without_copying_credentials(self):
        environment = dict(self.environment, PULSE_SERVER='unix:/host/custom pulse', PIPEWIRE_RUNTIME_DIR='/host/pw')
        launch = runtime_config.build_render_launch(self.base, runtime_config.RuntimeConfig(), environment)
        self.assertEqual(launch.environment['PULSE_SERVER'], 'unix:/host/custom pulse')
        self.assertEqual(launch.environment['PIPEWIRE_RUNTIME_DIR'], '/host/pw')
        self.assertEqual(launch.environment['XDG_RUNTIME_DIR'], str(self.base / 'runtime/gpu'))


class DoctorTests(unittest.TestCase):
    def test_source_check_does_not_require_or_launch_runtime(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(doctor.profile_manager, 'load') as runtime:
            result = doctor.check_environment(directory, source_only=True, environment={})
            self.assertTrue(result['okay'])
            runtime.assert_not_called()
            self.assertFalse((Path(directory) / 'prefix').exists())

    def test_uninstalled_clone_reports_failures_without_creating_files(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(doctor.ctypes.util, 'find_library', return_value=None):
            result = doctor.check_environment(directory, environment={'XDG_RUNTIME_DIR': str(Path(directory) / 'missing-host-socket')})
            self.assertFalse(result['okay'])
            failed = {c['name'] for c in result['checks'] if c['status'] == 'fail'}
            self.assertTrue({'runtime_manifest', 'wine', 'wineserver', 'application', 'prefix', 'vulkan_loader', 'wayland_socket'} <= failed)
            self.assertEqual(list(Path(directory).iterdir()), [])


if __name__ == '__main__':
    unittest.main()
