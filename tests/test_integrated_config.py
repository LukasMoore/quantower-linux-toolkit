"""Host-window plans and explicit connection refusal, without a desktop or Wine."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import runtime_config as runtime


class IntegratedConfigurationTests(unittest.TestCase):
    def test_old_configuration_defaults_and_round_trip(self):
        old = runtime.RuntimeConfig.from_dict({'schema_version': 1, 'desktop_width': 2200})
        self.assertEqual(old.window_mode, 'nested')
        self.assertIsNone(old.host_display)
        self.assertIsNone(old.wine_dpi)
        self.assertEqual(runtime.effective_wine_dpi(old, 'gpu'), 96)
        self.assertEqual(runtime.effective_wine_dpi(old, 'compatible'), 192)
        self.assertEqual(runtime.RuntimeConfig.from_dict(old.to_dict()), old)

    def test_strict_new_types_matrix_and_nested_inactive_fields(self):
        bad = [dict(window_mode=v) for v in (None, True, 0, [], 'host', 'Integrated')]
        bad += [dict(window_mode='integrated', wine_dpi=v) for v in (True, False, 95, 481, 192.0, '192')]
        bad += [dict(window_mode='integrated', host_display=v) for v in ('', True, 1, 'host:0', 'localhost:10.0', ':0;true', ':0\n')]
        bad += [dict(host_display=':0'), dict(wine_dpi=96)]
        for values in bad:
            with self.subTest(values=values), self.assertRaises(ValueError):
                runtime.RuntimeConfig.from_dict({'schema_version': 1, **values})
        for dpi in (None, 96, 192, 480):
            config = runtime.RuntimeConfig.from_dict({'schema_version': 1, 'window_mode': 'integrated', 'wine_dpi': dpi})
            runtime.validate_mode(config, 'gpu')
            self.assertEqual(runtime.effective_wine_dpi(config, 'gpu'), dpi or 96)
            with self.assertRaisesRegex(ValueError, 'compatible'):
                runtime.validate_mode(config, 'compatible')


class IntegratedPlanTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='host window plan ')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        (self.base / 'runtime-components').mkdir()
        self.manifest = self.base / 'runtime-components/manifest.json'
        self.manifest.write_text(json.dumps({'required_files': {}}))
        (self.base / 'prefix').mkdir()
        self.registry = self.base / 'prefix/user.reg'
        self.registry.write_text('WINE REGISTRY Version 2\n\n')
        self.config = runtime.RuntimeConfig(window_mode='integrated')
        self.environment = dict(DISPLAY=':7.0', XAUTHORITY='/private/literal auth',
                                XDG_RUNTIME_DIR='/run/user/123', WAYLAND_DISPLAY='wayland-0',
                                QUANTOWER_APP_VERSION='1.147.5', PATH='/usr/bin',
                                QUANTOWER_LAUNCH_SESSION='fixture-token')

    def preference(self, key, name, value):
        self.registry.write_text('WINE REGISTRY Version 2\n\n[' + key.replace('\\', '\\\\') + ']\n"' + name + '"=' + value + '\n')

    def test_direct_version_resolved_plan_and_pure_build(self):
        original = dict(self.environment)
        with mock.patch.object(runtime.subprocess, 'Popen') as execute:
            launch = runtime.build_integrated_launch(self.base, self.config, self.environment)
        self.assertEqual(launch.arguments, [str(self.base / 'runner-quantower/opt/wine-devel/bin/wine'),
                                          r'C:\AMPQuantowerInstalled\TradingPlatform\v1.147.5\Starter.exe'])
        self.assertFalse(any('gamescope' in argument or '/desktop' in argument for argument in launch.arguments))
        self.assertEqual(self.environment, original)
        self.assertFalse((self.base / 'cache').exists())
        execute.assert_not_called()
        self.assertEqual(launch.environment['DISPLAY'], ':7.0')
        self.assertEqual(launch.environment['XAUTHORITY'], '/private/literal auth')
        self.assertEqual(launch.environment['QUANTOWER_LAUNCH_SESSION'], 'fixture-token')
        self.assertEqual(launch.environment['WINEPREFIX'], str(self.base / 'prefix'))
        self.assertEqual(launch.environment['XDG_RUNTIME_DIR'], '/run/user/123')
        self.assertEqual(launch.environment['WAYLAND_DISPLAY'], 'wayland-0')

    def test_explicit_display_and_gpu_audio_paths_preserved(self):
        config = runtime.RuntimeConfig(window_mode='integrated', host_display=':4', gpu_device_filter='GPU $literal',
                                       pulse_server='unix:/host/pulse', pipewire_runtime_dir='/host/pipewire')
        launch = runtime.build_integrated_launch(self.base, config, self.environment)
        self.assertEqual(launch.environment['DISPLAY'], ':4')
        self.assertEqual(self.environment['DISPLAY'], ':7.0')
        self.assertEqual(launch.environment['PULSE_SERVER'], 'unix:/host/pulse')
        self.assertEqual(launch.environment['PIPEWIRE_RUNTIME_DIR'], '/host/pipewire')
        self.assertEqual(launch.environment['DXVK_FILTER_DEVICE_NAME'], 'GPU $literal')
        self.assertEqual(launch.environment['DXVK_ASYNC'], '0')
        self.assertEqual(launch.environment['DXVK_LOG_LEVEL'], 'info')
        self.assertEqual(launch.environment['DXVK_CONFIG_FILE'], str(self.base / 'gpu-dxvk.conf'))
        self.assertIn('winewayland.drv=d', launch.environment['WINEDLLOVERRIDES'])
        self.assertIn('winex11.drv=b', launch.environment['WINEDLLOVERRIDES'])
        self.assertIn('d3d9=n,b', launch.environment['WINEDLLOVERRIDES'])

    def test_missing_remote_hostile_nested_and_known_private_endpoints_refuse(self):
        for display in (None, '', 'localhost:10', 'remote:0', ':1;touch /tmp/bad', ':1\n'):
            env = dict(self.environment)
            env.pop('DISPLAY')
            if display is not None:
                env['DISPLAY'] = display
            with self.subTest(display=display), self.assertRaises(ValueError):
                runtime.build_integrated_launch(self.base, self.config, env)
        for marker in ({'GAMESCOPE_WAYLAND_DISPLAY': 'gamescope-0'}, {'WLR_XWAYLAND': '/private/Xwayland'},
                       {'XDG_RUNTIME_DIR': str(self.base / 'runtime/gpu')},
                       {'WAYLAND_DISPLAY': str(self.base / 'runtime/gpu/gamescope-0')}):
            with self.subTest(marker=marker), self.assertRaisesRegex(ValueError, 'host desktop'):
                runtime.build_integrated_launch(self.base, self.config, dict(self.environment, **marker))
        with mock.patch.object(runtime, 'private_x11_endpoints', return_value={':7'}):
            with self.assertRaisesRegex(ValueError, 'private Xwayland'):
                runtime.integrated_display(self.base, self.config, self.environment)

    def test_private_effective_configuration_is_refused_before_connection(self):
        configs = [runtime.RuntimeConfig(window_mode='integrated', host_runtime_dir=str(self.base / 'runtime/gpu')),
                   runtime.RuntimeConfig(window_mode='integrated', wayland_display=str(self.base / 'runtime/gpu/gamescope-0')),
                   runtime.RuntimeConfig(window_mode='integrated', host_runtime_dir=str(self.base / 'runtime'), wayland_display='gpu/gamescope-0')]
        for config in configs:
            with self.subTest(config=config), mock.patch.object(runtime.subprocess, 'Popen') as execute:
                with self.assertRaisesRegex(ValueError, 'host desktop'):
                    runtime.integrated_preflight(self.base, config, self.environment)
                execute.assert_not_called()

    def test_wine_closure_locale_trace_and_explicit_hooks_only(self):
        names = ('runtime-components/x11/libX11.so.6', 'runtime-components/x11/libX11-xcb.so.1',
                 'runtime-components/libquantower_assert_trace.so')
        for name in names:
            path = self.base / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
        self.manifest.write_text(json.dumps({'required_files': {name: 'declared' for name in names}}))
        wine_lib = self.base / 'runner-quantower/opt/wine-devel/lib'
        wine_lib.mkdir(parents=True)
        locale = self.base / 'runtime-components/x11/share/X11/locale'
        locale.mkdir(parents=True)
        env = dict(self.environment, LD_LIBRARY_PATH='/bad/compositor/lib', LD_PRELOAD='/bad/observer',
                   DOTNET_STARTUP_HOOKS='inherited', XLOCALEDIR='/bad/locale',
                   DXVK_LOG_PATH='/session/gpu', QUANTOWER_CRASH_TRACE_DIR='/session/assert')
        launch = runtime.build_integrated_launch(self.base, self.config, env)
        self.assertEqual(launch.environment['LD_LIBRARY_PATH'], str(self.base / 'runtime-components/x11') + ':' + str(wine_lib))
        self.assertEqual(launch.environment['XLOCALEDIR'], str(locale))
        self.assertEqual(launch.environment['PATH'].split(':')[0], str(wine_lib.parent / 'bin'))
        self.assertEqual(launch.environment['LD_PRELOAD'], str(self.base / names[2]))
        self.assertEqual(launch.environment['DXVK_LOG_PATH'], '/session/gpu')
        self.assertEqual(launch.environment['QUANTOWER_CRASH_TRACE_DIR'], '/session/assert')
        self.assertNotIn('DOTNET_STARTUP_HOOKS', launch.environment)
        self.assertFalse(any(key.startswith('GAMESCOPE_') or key == 'WLR_XWAYLAND' for key in launch.environment))
        env['QUANTOWER_DIAGNOSTIC_STARTUP_HOOKS'] = r'C:\maintenance\explicit.dll'
        launch = runtime.build_integrated_launch(self.base, self.config, env)
        self.assertEqual(launch.environment['DOTNET_STARTUP_HOOKS'], r'C:\maintenance\explicit.dll')

    def test_internal_render_helper_refuses_integrated(self):
        with self.assertRaisesRegex(ValueError, 'launch.sh'):
            runtime.build_render_launch(self.base, self.config, self.environment)

    def test_preference_desktop_empty_default_and_per_app_refuse(self):
        for root in (r'Software\Wine', r'Software\Wine\AppDefaults\STARTER.EXE'):
            for value in ('""', '"Default"', '"Private"', 'dword:00000001', '"unterminated'):
                with self.subTest(root=root, value=value):
                    self.preference(root + r'\Explorer', 'dEsKtOp', value)
                    with self.assertRaises(ValueError):
                        runtime.validate_integrated_preferences(self.base)

    def test_managed_wine_truth_semantics_and_per_app_precedence(self):
        for value in ('Y', 'yes', 'T', 'true', '1'):
            self.preference(r'Software\Wine\X11 Driver', 'mAnAgEd', json.dumps(value))
            runtime.validate_integrated_preferences(self.base)
        for value in ('N', 'false', '0', '', 'enabled', ' Yes'):
            self.preference(r'Software\Wine\X11 Driver', 'Managed', json.dumps(value))
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'Managed'):
                runtime.validate_integrated_preferences(self.base)
        self.registry.write_text('WINE REGISTRY Version 2\n[Software\\\\Wine\\\\X11 Driver]\n"Managed"="N"\n'
                                 '[Software\\\\Wine\\\\AppDefaults\\\\Starter.exe\\\\X11 Driver]\n"Managed"="Y"\n')
        runtime.validate_integrated_preferences(self.base)
        self.registry.write_text(self.registry.read_text().replace('"Y"', '"N"').replace('"N"\n[', '"Y"\n['))
        with self.assertRaisesRegex(ValueError, 'Managed'):
            runtime.validate_integrated_preferences(self.base)

    def test_duplicate_malformed_and_graphics_exclusions_refuse(self):
        for value in ('"wayland"', '"null,x11"', '"x11,null"', '"x11,evil"', '"evil,x11"', '""', '"x11, wayland"', 'dword:00000001'):
            self.preference(r'Software\Wine\Drivers', 'Graphics', value)
            with self.subTest(value=value), self.assertRaises(ValueError):
                runtime.validate_integrated_preferences(self.base)
        for value in ('"x11"', '"mac,x11,wayland"'):
            self.preference(r'Software\Wine\Drivers', 'Graphics', value)
            runtime.validate_integrated_preferences(self.base)
        self.preference(r'Software\Wine\X11 Driver', 'Managed', '"Y"')
        self.registry.write_text(self.registry.read_text() + '"managed"="N"\n')
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            runtime.validate_integrated_preferences(self.base)
        self.preference(r'Software\Wine\X11 Driver', 'Managed', '"Y"')
        self.registry.write_text(self.registry.read_text() + '[software\\\\wine\\\\x11 driver]\n')
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            runtime.validate_integrated_preferences(self.base)

    def test_passive_probe_safe_environment_and_literal_argv(self):
        env = dict(self.environment, LD_PRELOAD='bad', DOTNET_STARTUP_HOOKS='bad', WINEPREFIX='bad')
        answer = subprocess.CompletedProcess([], 0, b'xwininfo: Window id: 0x123 (the root window)\n  Width: 123\n  Height: 456\n')
        with mock.patch.object(runtime.shutil, 'which', return_value='/usr/bin/xwininfo'), \
                mock.patch.object(runtime, '_run_xwininfo_probe', return_value=answer) as execute:
            self.assertEqual(runtime.integrated_preflight(self.base, self.config, env), ':7.0')
        args, kwargs = execute.call_args
        self.assertEqual(args[0], ['/usr/bin/xwininfo', '-root', '-stats'])
        self.assertEqual(args[1]['DISPLAY'], ':7.0')
        self.assertEqual(args[1]['XAUTHORITY'], '/private/literal auth')
        for key in ('WINEPREFIX', 'QUANTOWER_LAUNCH_SESSION', 'LD_PRELOAD', 'DOTNET_STARTUP_HOOKS'):
            self.assertNotIn(key, args[1])
        self.assertEqual(kwargs, {})
        self.assertEqual(self.registry.read_text(), 'WINE REGISTRY Version 2\n\n')

    def test_passive_probe_auth_failure_timeout_missing_and_invalid_response(self):
        answers = [subprocess.CompletedProcess([], 1, b''), subprocess.CompletedProcess([], 0, b'not root info'),
                   subprocess.CompletedProcess([], 0, b'xwininfo: Window id: 0x1\nWidth: 0\nHeight: 1\n')]
        with mock.patch.object(runtime.shutil, 'which', return_value='/usr/bin/xwininfo'):
            for answer in answers:
                with self.subTest(answer=answer), mock.patch.object(runtime, '_run_xwininfo_probe', return_value=answer), self.assertRaises(RuntimeError):
                    runtime.integrated_preflight(self.base, self.config, self.environment)
            with mock.patch.object(runtime, '_run_xwininfo_probe', side_effect=subprocess.TimeoutExpired(['probe'], 3)), self.assertRaisesRegex(RuntimeError, 'timed out'):
                runtime.integrated_preflight(self.base, self.config, self.environment)
        with mock.patch.object(runtime.shutil, 'which', return_value=None), mock.patch.object(runtime.subprocess, 'Popen') as execute:
            with self.assertRaisesRegex(RuntimeError, 'xwininfo'):
                runtime.integrated_preflight(self.base, self.config, self.environment)
            execute.assert_not_called()

    def test_bundled_probe_requires_pin_and_closure_without_starting(self):
        helper = self.base / 'runner-quantower/opt/wine-devel/bin/xwininfo'
        helper.parent.mkdir(parents=True)
        helper.write_bytes(b'fixture, never execute')
        helper.chmod(0o700)
        with self.assertRaisesRegex(ValueError, 'pinned'):
            runtime.resolve_xwininfo(self.base, self.environment)
        self.manifest.write_text(json.dumps({'required_files': {str(helper.relative_to(self.base)): hashlib.sha256(helper.read_bytes()).hexdigest()}}))
        (helper.parent.parent / 'lib').mkdir()
        with mock.patch.object(runtime.subprocess, 'Popen') as execute:
            name, env = runtime.resolve_xwininfo(self.base, self.environment)
        self.assertEqual(name, str(helper))
        self.assertEqual(env['LD_LIBRARY_PATH'], str(helper.parent.parent / 'lib'))
        self.assertNotIn('QUANTOWER_LAUNCH_SESSION', env)
        execute.assert_not_called()
        helper.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            runtime.resolve_xwininfo(self.base, self.environment)


class BoundedProbeReaderTests(unittest.TestCase):
    def fixture_probe(self, script, **options):
        # Original short-lived Python output fixtures only; no Wine or display.
        real_popen = subprocess.Popen
        created = []

        def record(*args, **kwargs):
            child = real_popen(*args, **kwargs)
            created.append(child)
            return child

        with mock.patch.object(runtime.subprocess, 'Popen', side_effect=record):
            try:
                return runtime._run_xwininfo_probe([sys.executable, '-c', script], {}, **options)
            finally:
                self.assertEqual(len(created), 1)
                self.assertIsNotNone(created[0].poll(), 'only owned fixture must be reaped on every path')

    def test_exact_limit_and_nonzero_status_are_bounded(self):
        answer = self.fixture_probe('import os;os.write(1,b"x"*65536)')
        self.assertEqual(answer.returncode, 0)
        self.assertEqual(len(answer.stdout), 65536)
        answer = self.fixture_probe('import sys;sys.stdout.write("failed");sys.exit(7)')
        self.assertEqual((answer.returncode, answer.stdout), (7, b'failed'))

    def test_flood_stops_at_limit_and_reaps_only_owned_probe(self):
        with self.assertRaises(runtime._ProbeOutputLimit):
            self.fixture_probe('import os\nwhile True: os.write(1,b"x"*8192)')

    def test_timeout_and_closed_stdout_wait_share_deadline(self):
        for script in ('import time;time.sleep(10)', 'import os,time;os.close(1);time.sleep(10)'):
            started = time.monotonic()
            with self.subTest(script=script), self.assertRaises(subprocess.TimeoutExpired):
                self.fixture_probe(script, timeout=0.2)
            self.assertLess(time.monotonic() - started, 3)


if __name__ == '__main__':
    unittest.main()
