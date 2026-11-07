"""Isolated profile transactions and manifest paths; never operate on a live prefix."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import xml.etree.ElementTree as ET

import profile_manager


REGISTRY = 'WINE REGISTRY Version 2\n\n[Unrelated] 1\n"SyntheticPreference"="keep exactly"\n'
XML = b'''<?xml version="1.0" encoding="utf-8"?>
<Settings marker="keep"><Item><Name>SettingsManager.View.UseSoftwareRenderMode</Name><Value>false</Value></Item>
<Item scope="unchanged"><Name>SyntheticAccounts</Name><Value><Account label="sample-only">synthetic-account-reference</Account></Value></Item></Settings>'''


def installation(base, *, settings=XML, original_d3d9=b'original builtin'):
    prefix = base / 'prefix'
    dll = prefix / 'drive_c/windows/system32/d3d9.dll'
    dll.parent.mkdir(parents=True)
    if original_d3d9 is not None:
        dll.write_bytes(original_d3d9)
        dll.chmod(0o640)
    registry = prefix / 'user.reg'
    registry.write_text(REGISTRY)
    registry.chmod(0o640)
    app_settings = prefix / 'drive_c/AMPQuantowerInstalled/Settings/settings.xml'
    if settings is not None:
        app_settings.parent.mkdir(parents=True)
        app_settings.write_bytes(settings)
        app_settings.chmod(0o640)
    components = base / 'runtime-components'
    components.mkdir()
    (components / 'd3d9.dll').write_bytes(b'synthetic DXVK payload')
    return prefix, registry, dll, app_settings


class RegistryTests(unittest.TestCase):
    def test_missing_defaults_and_eof_header_are_inserted_once(self):
        key = r'Software\\Wine\\Fonts'
        for original in ('WINE REGISTRY Version 2\n', '[' + key + ']'):
            with self.subTest(original=original):
                once = profile_manager.registry_dword(original, key, 'LogPixels', 192)
                twice = profile_manager.registry_dword(once, key, 'LogPixels', 192)
                self.assertEqual(once, twice)
                self.assertEqual(once.count('[' + key + ']'), 1)
                self.assertIn('"LogPixels"=dword:000000c0', once)

    def test_case_variant_sections_and_values_are_rejected_as_duplicates(self):
        key = r'Software\\Wine\\Fonts'
        cases = ['[' + key + ']\n\n[' + key.lower() + ']\n',
                 '[' + key + ']\n\n[' + key.lower() + ']',
                 '[' + key + ']\n"LogPixels"=dword:00000060\n"logpixels"=dword:000000c0\n']
        for original in cases:
            with self.subTest(original=original), self.assertRaisesRegex(RuntimeError, 'Duplicate'):
                profile_manager.registry_dword(original, key, 'LogPixels', 96)

    def test_existing_case_variant_is_updated_in_place_and_unrelated_text_preserved(self):
        key = r'Software\\Wine\\Fonts'
        original = '[' + key.lower() + '] 123\n"logpixels"=dword:000000c0\n"Keep"="same"\n\n[Other]\n"Value"="stay"\n'
        result = profile_manager.registry_dword(original, key, 'LogPixels', 96)
        self.assertIn('[' + key.lower() + '] 123\n', result)
        self.assertEqual(result.count('"LogPixels"='), 1)
        self.assertIn('"Keep"="same"\n\n[Other]\n"Value"="stay"\n', result)


class ProfileTransactionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        patch = mock.patch.object(profile_manager, 'no_running_prefix')
        self.inactive = patch.start()
        self.addCleanup(patch.stop)

    def test_dpi_defaults_null_and_explicit_values_share_the_profile_transaction(self):
        for mode, supplied, expected in (('gpu', None, 96), ('compatible', None, 192),
                                         ('gpu', 96, 96), ('gpu', 192, 192),
                                         ('gpu', 480, 480)):
            with self.subTest(mode=mode, supplied=supplied), tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                _, registry, dll, settings = installation(base)
                account = ET.tostring(ET.fromstring(settings.read_bytes()).findall('Item')[1])
                profile_manager.prepare(base, mode, wine_dpi=supplied)
                text = registry.read_text()
                self.assertEqual(text.count('"LogPixels"=dword:' + format(expected, '08x')), 2)
                self.assertIn(REGISTRY, text)
                self.assertEqual(ET.tostring(ET.fromstring(settings.read_bytes()).findall('Item')[1]), account)
                applied = base / 'logs/applied-runtime-profile.json'
                self.assertEqual(json.loads(applied.read_text())['dpi'], expected)
                first = {p: profile_manager.file_state(p) for p in (registry, dll, settings, applied)}
                profile_manager.prepare(base, mode, wine_dpi=supplied)
                self.assertEqual(first, {p: profile_manager.file_state(p) for p in first})

    def test_invalid_dpi_is_refused_before_prefix_discovery_or_any_write(self):
        _, registry, dll, settings = installation(self.base)
        before = {p: profile_manager.file_state(p) for p in (registry, dll, settings)}
        for supplied in (True, False, 95, 481, 96.0, '192', [], {}):
            with self.subTest(supplied=supplied), self.assertRaisesRegex(ValueError, 'wine_dpi'):
                profile_manager.prepare(self.base, 'gpu', wine_dpi=supplied)
            self.assertEqual(before, {p: profile_manager.file_state(p) for p in before})
            self.assertFalse((self.base / 'logs').exists())
        self.inactive.assert_not_called()

    def test_explicit_dpi_does_not_bypass_active_or_uncertain_prefix_refusal(self):
        _, registry, dll, settings = installation(self.base)
        before = {p: profile_manager.file_state(p) for p in (registry, dll, settings)}
        for reason in ('active prefix', 'unverifiable prefix'):
            self.inactive.side_effect = RuntimeError(reason)
            with self.assertRaisesRegex(RuntimeError, reason):
                profile_manager.prepare(self.base, 'gpu', wine_dpi=192)
            self.assertEqual(before, {p: profile_manager.file_state(p) for p in before})
            self.assertFalse((self.base / 'logs').exists())

    def test_first_launch_without_vendor_xml_uses_scoped_wpf_setting(self):
        prefix, registry, dll, settings = installation(self.base, settings=None, original_d3d9=None)
        profile_manager.prepare(self.base, 'compatible')
        self.inactive.assert_called_once_with(prefix)
        self.assertFalse(settings.exists())
        self.assertFalse(settings.parent.exists(), 'do not invent a vendor settings tree')
        self.assertFalse(dll.exists())
        text = registry.read_text()
        self.assertIn('[Software\\\\Microsoft\\\\Avalon.Graphics]', text)
        self.assertIn('"DisableHWAcceleration"=dword:00000001', text)
        self.assertEqual(text.count('"LogPixels"=dword:000000c0'), 2)
        self.assertIn('[Unrelated] 1\n"SyntheticPreference"="keep exactly"\n', text)
        self.assertFalse((self.base / 'logs/runtime-profile-originals/settings.xml').exists())

    def test_unrecognized_vendor_xml_is_left_byte_identical(self):
        original = b'<?xml version="1.0"?><Settings><Item><Name>Other</Name><Value>sample</Value></Item></Settings>'
        _, registry, _, settings = installation(self.base, settings=original)
        profile_manager.prepare(self.base, 'compatible')
        self.assertEqual(settings.read_bytes(), original)
        self.assertIn('"DisableHWAcceleration"=dword:00000001', registry.read_text())

    def test_vendor_render_setting_changes_without_altering_account_subtree(self):
        _, _, _, settings = installation(self.base)
        before = ET.fromstring(settings.read_bytes())
        original_account = ET.tostring(before.findall('Item')[1])
        profile_manager.prepare(self.base, 'compatible')
        after = ET.fromstring(settings.read_bytes())
        self.assertEqual(after.findall('Item')[0].findtext('Value'), 'true')
        self.assertEqual(ET.tostring(after.findall('Item')[1]), original_account)
        self.assertEqual(after.attrib, before.attrib)

    def test_ambiguous_or_malformed_xml_fails_before_any_write(self):
        duplicate = b'<Settings><Item><Name>SettingsManager.View.UseSoftwareRenderMode</Name><Value>false</Value></Item><Item><Name>SettingsManager.View.UseSoftwareRenderMode</Name><Value>true</Value></Item></Settings>'
        no_value = b'<Settings><Item><Name>SettingsManager.View.UseSoftwareRenderMode</Name></Item></Settings>'
        for xml in (duplicate, no_value, b'<broken'):
            with self.subTest(xml=xml), tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                _, registry, dll, settings = installation(base, settings=xml)
                states = {p: profile_manager.file_state(p) for p in (registry, dll, settings)}
                with self.assertRaises((RuntimeError, ET.ParseError)):
                    profile_manager.prepare(base, 'gpu')
                self.assertEqual(states, {p: profile_manager.file_state(p) for p in states})
                self.assertFalse((base / 'logs').exists())

    def test_active_prefix_is_rejected_before_settings_or_backups_are_touched(self):
        _, registry, dll, settings = installation(self.base)
        self.inactive.side_effect = RuntimeError('active prefix')
        states = {p: profile_manager.file_state(p) for p in (registry, dll, settings)}
        with self.assertRaisesRegex(RuntimeError, 'active prefix'):
            profile_manager.prepare(self.base, 'gpu')
        self.assertEqual(states, {p: profile_manager.file_state(p) for p in states})
        self.assertFalse((self.base / 'logs').exists())

    def test_missing_registry_rejects_uninitialized_prefix_clearly(self):
        _, registry, dll, settings = installation(self.base)
        registry.unlink()
        original = dll.read_bytes(), settings.read_bytes()
        with self.assertRaisesRegex(RuntimeError, 'initialize the prefix'):
            profile_manager.prepare(self.base, 'compatible')
        self.assertEqual((dll.read_bytes(), settings.read_bytes()), original)
        self.assertFalse((self.base / 'logs').exists())

    def test_redirected_profile_files_and_dll_directory_are_rejected(self):
        for target in ('settings', 'registry', 'dll_directory'):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                _, registry, dll, settings = installation(base)
                if target == 'dll_directory':
                    external = base / 'outside-prefix-system32'
                    dll.parent.rename(external)
                    dll.parent.symlink_to(external, target_is_directory=True)
                    protected = external / 'd3d9.dll'
                else:
                    selected = settings if target == 'settings' else registry
                    external = base / 'outside-prefix-file'
                    selected.rename(external)
                    selected.symlink_to(external)
                    protected = external
                before = protected.read_bytes()
                with self.assertRaisesRegex(RuntimeError, 'redirected'):
                    profile_manager.prepare(base, 'gpu')
                self.assertEqual(protected.read_bytes(), before)
                self.assertFalse((base / 'logs').exists())

    def test_gpu_then_compatible_restores_the_first_original_dll(self):
        _, _, dll, _ = installation(self.base)
        profile_manager.prepare(self.base, 'gpu')
        self.assertEqual(dll.read_bytes(), b'synthetic DXVK payload')
        profile_manager.prepare(self.base, 'compatible')
        self.assertEqual(dll.read_bytes(), b'original builtin')
        self.assertEqual(dll.stat().st_mode & 0o777, 0o640)
        self.assertEqual((self.base / 'logs/runtime-profile-originals/d3d9.dll').read_bytes(), b'original builtin')

    def test_one_shot_atomic_failures_restore_every_modified_profile_file(self):
        original_atomic = profile_manager.atomic_bytes
        for failure in ('dll', 'settings', 'registry', 'applied'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                _, registry, dll, settings = installation(base)
                states = {p: profile_manager.file_state(p) for p in (registry, dll, settings)}
                applied = base / 'logs/applied-runtime-profile.json'
                target = {'dll': dll, 'settings': settings, 'registry': registry, 'applied': applied}[failure]
                failed = False
                def atomic(path, data, mode=0o600):
                    nonlocal failed
                    if path == target and not failed:
                        failed = True
                        raise OSError('synthetic write failure')
                    return original_atomic(path, data, mode)
                with mock.patch.object(profile_manager, 'atomic_bytes', side_effect=atomic), self.assertRaisesRegex(OSError, 'synthetic'):
                    profile_manager.prepare(base, 'gpu', wine_dpi=192)
                self.assertTrue(failed)
                self.assertEqual(states, {p: profile_manager.file_state(p) for p in states})
                self.assertFalse(applied.exists())

    def test_duplicate_wine_sections_fail_before_mutation(self):
        _, registry, dll, settings = installation(self.base)
        registry.write_text(REGISTRY + '\n[Control Panel\\\\Desktop]\n\n[control panel\\\\desktop]\n')
        states = {p: profile_manager.file_state(p) for p in (registry, dll, settings)}
        with self.assertRaisesRegex(RuntimeError, 'Duplicate'):
            profile_manager.prepare(self.base, 'compatible')
        self.assertEqual(states, {p: profile_manager.file_state(p) for p in states})
        self.assertFalse((self.base / 'logs').exists())


class ManifestTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        (self.base / 'runtime-components').mkdir()
        (self.base / 'runtime-profile.json').write_text(json.dumps({'version': 1, 'mode': 'compatible'}))
        self.payload = self.base / 'runtime-components/payload'
        self.payload.write_bytes(b'synthetic native bytes')
        self.digest = hashlib.sha256(self.payload.read_bytes()).hexdigest()

    def manifest(self, required):
        (self.base / 'runtime-components/manifest.json').write_text(json.dumps({'required_files': required}))

    def test_verified_component_loads_and_changed_bytes_fail(self):
        self.manifest({'runtime-components/payload': self.digest})
        self.assertEqual(profile_manager.load(self.base), 'compatible')
        self.payload.write_bytes(b'changed')
        with self.assertRaisesRegex(RuntimeError, 'verification'):
            profile_manager.load(self.base)

    def test_unsafe_paths_and_invalid_checksums_are_rejected(self):
        cases = [('../outside', self.digest), ('/absolute/path', self.digest), ('', self.digest),
                 ('runtime-components/payload', 'invalid'), ('runtime-components/payload', None)]
        for path, digest in cases:
            with self.subTest(path=path, digest=digest):
                self.manifest({path: digest})
                with self.assertRaisesRegex(RuntimeError, 'path or checksum'):
                    profile_manager.load(self.base)

    def test_symlink_escape_is_rejected_even_when_file_hash_matches(self):
        with tempfile.TemporaryDirectory() as directory:
            external = Path(directory) / 'payload'
            external.write_bytes(self.payload.read_bytes())
            link = self.base / 'runtime-components/escaped'
            link.symlink_to(external)
            self.manifest({'runtime-components/escaped': self.digest})
            with self.assertRaisesRegex(RuntimeError, 'escapes'):
                profile_manager.load(self.base)

    def test_invalid_profile_shapes_are_clear_errors(self):
        self.manifest({})
        for value in ([], None, {'version': True, 'mode': 'compatible'}, {'version': 2, 'mode': 'compatible'}):
            with self.subTest(value=value):
                (self.base / 'runtime-profile.json').write_text(json.dumps(value))
                with self.assertRaisesRegex(RuntimeError, 'rendering profile'):
                    profile_manager.load(self.base)


if __name__ == '__main__':
    unittest.main()
