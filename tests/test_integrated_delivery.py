"""Mode-aware doctor checks stay read-only and never connect to displays."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import doctor
import runtime_config


class IntegratedDoctorTests(unittest.TestCase):
    def test_integrated_prerequisites_do_not_require_nested_display_or_connect(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / 'prefix').mkdir()
            (base / 'prefix/user.reg').write_text('WINE REGISTRY Version 2\n')
            config = runtime_config.RuntimeConfig(window_mode='integrated')
            with (
                mock.patch.object(doctor.runtime_config, 'load', return_value=config),
                mock.patch.object(doctor.profile_manager, 'load', return_value='gpu'),
                mock.patch.object(doctor.runtime_config, 'resolve_xwininfo', return_value=('/synthetic/xwininfo', {})) as resolve,
                mock.patch.object(doctor.runtime_config, 'integrated_preflight', side_effect=AssertionError('No display connection')),
                mock.patch.object(doctor.runtime_config.subprocess, 'run', side_effect=AssertionError('No subprocess')),
                mock.patch.object(doctor.runtime_config.subprocess, 'Popen', side_effect=AssertionError('No subprocess')),
                mock.patch.object(doctor.ctypes.util, 'find_library', return_value='synthetic.so'),
            ):
                result = doctor.check_environment(base, environment={'DISPLAY': ':23', 'PATH': '/usr/bin'})
            names = {c['name'] for c in result['checks']}
            self.assertEqual(result['window_mode'], 'integrated')
            self.assertTrue({'x11_display', 'xwininfo', 'x11_libraries', 'vulkan_loader'} <= names)
            self.assertFalse({'gamescope-brokey', 'Xwayland', 'wayland_socket_path', 'wayland_socket', 'xkb_compiler'} & names)
            resolve.assert_called_once()
            self.assertEqual(sorted(str(p.relative_to(base)) for p in base.rglob('*')), ['prefix', 'prefix/user.reg'])

    def test_integrated_missing_display_and_incompatible_profile_are_reported_without_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            config = runtime_config.RuntimeConfig(window_mode='integrated')
            with (
                mock.patch.object(doctor.runtime_config, 'load', return_value=config),
                mock.patch.object(doctor.profile_manager, 'load', return_value='compatible'),
                mock.patch.object(doctor.ctypes.util, 'find_library', return_value='synthetic.so'),
                mock.patch.object(doctor.runtime_config, 'resolve_xwininfo', side_effect=RuntimeError('missing helper')),
                mock.patch.object(doctor.runtime_config.subprocess, 'run', side_effect=AssertionError('No subprocess')),
                mock.patch.object(doctor.runtime_config.subprocess, 'Popen', side_effect=AssertionError('No subprocess')),
            ):
                result = doctor.check_environment(base, environment={})
            failed = {c['name'] for c in result['checks'] if c['status'] == 'fail'}
            self.assertTrue({'window_mode', 'x11_display', 'xwininfo', 'wine_window_preferences'} <= failed)
            self.assertFalse(result['okay'])
            self.assertEqual(list(base.iterdir()), [])

    def test_source_only_does_not_resolve_probe_or_read_installed_preferences(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            with (
                mock.patch.object(doctor.runtime_config, 'load', return_value=runtime_config.RuntimeConfig(window_mode='integrated')),
                mock.patch.object(doctor.runtime_config, 'resolve_xwininfo', side_effect=AssertionError('No runtime resolution')),
                mock.patch.object(doctor.runtime_config, 'validate_integrated_preferences', side_effect=AssertionError('No prefix read')),
                mock.patch.object(doctor.runtime_config.subprocess, 'run', side_effect=AssertionError('No subprocess')),
                mock.patch.object(doctor.runtime_config.subprocess, 'Popen', side_effect=AssertionError('No subprocess')),
            ):
                result = doctor.check_environment(base, source_only=True, environment={})
            self.assertTrue(result['okay'])
            self.assertEqual(list(base.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
