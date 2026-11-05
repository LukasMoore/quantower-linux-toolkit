"""Exercise the real shell environment boundary with fake graphics processes."""

import json
import hashlib
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest


APP = Path(__file__).resolve().parents[1]
CAPTURE_SCRIPT = """#!/usr/bin/python3
import json, os, sys
from pathlib import Path
fields = ('LD_LIBRARY_PATH', 'LD_PRELOAD', 'DISPLAY', 'WAYLAND_DISPLAY',
          'WLR_XWAYLAND', 'GAMESCOPE_SCRIPT_PATH', 'WINEPREFIX', 'PIPEWIRE_RUNTIME_DIR', 'XDG_RUNTIME_DIR', 'DXVK_FILTER_DEVICE_NAME')
record = dict(stage=STAGE, environment={key: os.environ.get(key) for key in fields},
              arguments=sys.argv[1:])
with Path(os.environ['TEST_CAPTURE']).open('a') as stream:
    stream.write(json.dumps(record) + '\\n')
if STAGE == 'gamescope':
    command = sys.argv[sys.argv.index('--') + 1:]
    os.execvpe(command[0], command, os.environ)
"""


class X11RuntimeScopeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        for name in ('render-launch.sh', 'runtime_config.py', 'application_runtime.py', 'profile_manager.py'):
            shutil.copyfile(APP / name, self.base / name)
        self.capture = self.base / 'captured.jsonl'
        self.host_runtime = self.base / 'host-runtime'
        self.host_runtime.mkdir()
        # The fake compositor never connects. A socket-type filesystem node
        # exercises the launcher's -S check without opening any real listener.
        os.mknod(self.host_runtime / 'wayland-test', stat.S_IFSOCK | 0o600)
        self.gamescope = self.base / 'gamescope/files'
        self.runner = self.base / 'runner-quantower/opt/wine-devel/bin'
        (self.gamescope / 'bin').mkdir(parents=True)
        (self.gamescope / 'lib').mkdir()
        self.runner.mkdir(parents=True)
        components = self.base / 'runtime-components'
        components.mkdir()
        (components / 'x11').mkdir()
        # The shell only verifies presence. The ELF loader ignores this empty
        # test preload; neither fake process loads or invokes Wine/X11 code.
        (components / 'libquantower_assert_trace.so').touch()
        for name in ('libX11.so.6', 'libX11-xcb.so.1'):
            (components / 'x11' / name).touch()
        (components / 'manifest.json').write_text(json.dumps({'required_files': {
            'runtime-components/libquantower_assert_trace.so': hashlib.sha256(b'').hexdigest(),
            'runtime-components/x11/libX11.so.6': hashlib.sha256(b'').hexdigest(),
            'runtime-components/x11/libX11-xcb.so.1': hashlib.sha256(b'').hexdigest()}}))
        (self.base / 'runtime-profile.json').write_text(json.dumps({'version': 1, 'mode': 'gpu'}))
        for stage, executable in (
            ('gamescope', self.gamescope / 'bin/gamescope-brokey'),
            ('wine', self.runner / 'wine'),
        ):
            executable.write_text(CAPTURE_SCRIPT.replace('STAGE', repr(stage)))
            executable.chmod(0o700)
        self.environment = os.environ.copy()
        for key in ('LD_PRELOAD', 'QUANTOWER_X11_RUNTIME', 'QUANTOWER_CRASH_TRACE_DIR',
                    'DXVK_LOG_PATH', 'DOTNET_STARTUP_HOOKS', 'QUANTOWER_APP_VERSION'):
            self.environment.pop(key, None)
        self.environment.update(
            TEST_CAPTURE=str(self.capture), XDG_RUNTIME_DIR=str(self.host_runtime),
            WAYLAND_DISPLAY='wayland-test', DISPLAY=':inherited-display',
            LD_LIBRARY_PATH='/inherited/library/path', WLR_XWAYLAND='/inherited/Xwayland',
            GAMESCOPE_SCRIPT_PATH='/inherited/scripts',
        )

    def launch(self, selection=None):
        environment = self.environment.copy()
        if selection is not None:
            environment['QUANTOWER_X11_RUNTIME'] = selection
        return subprocess.run(['bash', str(self.base / 'render-launch.sh')],
                              env=environment, capture_output=True, text=True, timeout=10)

    def records(self):
        return [json.loads(line) for line in self.capture.read_text().splitlines()]

    def test_private_library_is_scoped_to_wine_after_gamescope(self):
        for selection in (None, 'private'):
            with self.subTest(selection=selection):
                self.capture.unlink(missing_ok=True)
                result = self.launch(selection)
                self.assertEqual(result.returncode, 0, result.stderr)
                outer, inner = self.records()
                self.assertEqual((outer['stage'], inner['stage']), ('gamescope', 'wine'))
                self.assertEqual(outer['environment']['LD_LIBRARY_PATH'], str(self.gamescope / 'lib'))
                self.assertEqual(inner['environment']['LD_LIBRARY_PATH'], str(self.base / 'runtime-components/x11'))
                self.assertIsNone(outer['environment']['LD_PRELOAD'])
                self.assertEqual(inner['environment']['LD_PRELOAD'],
                                 str(self.base / 'runtime-components/libquantower_assert_trace.so'))
                self.assertEqual(outer['environment']['WLR_XWAYLAND'], str(self.gamescope / 'bin/Xwayland'))
                self.assertIsNone(inner['environment']['WLR_XWAYLAND'])
                self.assertIsNone(inner['environment']['GAMESCOPE_SCRIPT_PATH'])
                self.assertIsNone(outer['environment']['DISPLAY'])
                self.assertEqual(outer['environment']['WAYLAND_DISPLAY'], str(self.host_runtime / 'wayland-test'))
                self.assertEqual(inner['arguments'][0], 'explorer.exe')
                self.assertIn('/desktop=AMP-Quantower,1920x1080', inner['arguments'])
                self.assertEqual(inner['environment']['WINEPREFIX'], str(self.base / 'prefix'))
                self.assertEqual(outer['environment']['PIPEWIRE_RUNTIME_DIR'], str(self.host_runtime))
                self.assertEqual(outer['environment']['XDG_RUNTIME_DIR'], str(self.base / 'runtime/gpu'))

    def test_configured_version_reaches_the_actual_windows_launch_command(self):
        self.environment['QUANTOWER_APP_VERSION'] = '1.147.5'
        result = self.launch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.records()[1]['arguments'][-1],
                         r'C:\AMPQuantowerInstalled\TradingPlatform\v1.147.5\Starter.exe')

    def test_system_override_removes_library_path_from_wine_only(self):
        result = self.launch('system')
        self.assertEqual(result.returncode, 0, result.stderr)
        outer, inner = self.records()
        self.assertEqual(outer['environment']['LD_LIBRARY_PATH'], str(self.gamescope / 'lib'))
        self.assertIsNone(inner['environment']['LD_LIBRARY_PATH'])
        self.assertIsNone(inner['environment']['WLR_XWAYLAND'])
        self.assertIsNone(inner['environment']['GAMESCOPE_SCRIPT_PATH'])
        self.assertEqual(inner['environment']['LD_PRELOAD'],
                         str(self.base / 'runtime-components/libquantower_assert_trace.so'))

    def test_invalid_selection_fails_before_any_graphics_process_runs(self):
        result = self.launch('unreviewed')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Unknown Quantower X11 runtime selection.', result.stderr)
        self.assertFalse(self.capture.exists())

    def test_shell_metacharacters_in_gpu_filter_remain_literal(self):
        sentinel = self.base / 'injection sentinel'
        literal = 'GPU $(touch "' + str(sentinel) + '"); echo literal'
        (self.base / 'launcher-config.json').write_text(json.dumps({
            'schema_version': 1, 'gpu_device_filter': literal,
            'desktop_width': 1600, 'desktop_height': 900}))
        result = self.launch()
        self.assertEqual(result.returncode, 0, result.stderr)
        outer, inner = self.records()
        self.assertEqual(outer['environment']['DXVK_FILTER_DEVICE_NAME'], literal)
        self.assertEqual(inner['environment']['DXVK_FILTER_DEVICE_NAME'], literal)
        self.assertIn('/desktop=AMP-Quantower,1600x900', inner['arguments'])
        self.assertFalse(sentinel.exists())

    def test_modified_declared_native_file_blocks_process_execution(self):
        (self.base / 'runtime-components/libquantower_assert_trace.so').write_bytes(b'changed')
        result = self.launch()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('failed verification', result.stderr)
        self.assertFalse(self.capture.exists())


if __name__ == '__main__':
    unittest.main()
