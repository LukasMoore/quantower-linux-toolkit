import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import application_runtime


class ApplicationRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)

    def test_legacy_installation_without_selection_remains_usable(self):
        selected = application_runtime.load(self.base)
        self.assertEqual(selected.version, '1.146.7')
        self.assertTrue(str(selected.directory).endswith('TradingPlatform/v1.146.7'))

    def test_selected_official_version_is_consistent_in_windows_and_linux_paths(self):
        (self.base / 'application-version.json').write_text(json.dumps(
            dict(schema_version=1, application_version='1.147.5')))
        selected = application_runtime.load(self.base)
        self.assertTrue(str(selected.directory).endswith('TradingPlatform/v1.147.5'))
        self.assertEqual(selected.windows_executable,
                         r'C:\AMPQuantowerInstalled\TradingPlatform\v1.147.5\Starter.exe')

    def test_malformed_selection_and_path_injection_are_rejected(self):
        records = [[], None, {}, dict(schema_version=2, application_version='1.147.5')]
        records += [dict(schema_version=1, application_version=value)
                    for value in (None, '../1.147.5', '1.147.5;exec', '1.147.5/other', 123, '1.147.5\n')]
        for record in records:
            with self.subTest(record=record):
                (self.base / 'application-version.json').write_text(json.dumps(record))
                with self.assertRaises(ValueError):
                    application_runtime.load(self.base)
