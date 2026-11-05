"""Support archives read only allowlisted evidence from a temporary fake app."""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('support_bundle',
                                             Path(__file__).resolve().parents[1] / 'support-bundle.py')
bundle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bundle)


class SupportBundleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name) / 'app'
        self.base.mkdir()
        self.output = self.base / 'logs/support/bundle.tar.gz'

    def source(self, relative, data=b'test diagnostic'):
        path = self.base / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def contents(self, path):
        with tarfile.open(path) as archive:
            return {item.name: archive.extractfile(item).read() for item in archive.getmembers()}

    def test_allowlist_excludes_accounts_registry_ownership_and_backups(self):
        included = (
            'runtime-profile.json', 'runtime-components/manifest.json',
            'application-version.json', 'runtime-components/application-upgrade.json',
            'runtime-components/cef-locales.json', 'runtime-components/cef-angle.json',
            'runtime-components/account-template-migration.json',
            'runtime-components/x11/provenance.json', 'runtime-components/x11/source.json',
            'runtime-components/x11/validation.json',
            'runtime-components/x11/locking-xid-after-sequence-sync.patch',
            'runtime-components/gamescope/provenance.json',
            'runtime-components/gamescope/ordered-vulkan-shutdown.patch',
            'runtime-components/gamescope/black-buffer-fix.patch',
            'runtime-components/native-maintenance-20261105/deployment.json',
            'runtime-components/native-maintenance-20261105/font-maintenance-summary.json',
            'runtime-components/native-maintenance-20261105/font-official-sources.json',
            'runtime-components/native-maintenance-20261105/font-artifact-manifest.json',
            'runtime-components/native-maintenance-20261105/gdiplus/isolated-review-provenance.json',
            'runtime-components/native-maintenance-20261105/gdiplus/abi-validation.json',
            'runtime-components/native-maintenance-20261105/gdiplus/text-call-resources-on-installed.patch',
            'runtime-components/native-maintenance-20261105/gdi32/isolated-review-provenance.json',
            'runtime-components/native-maintenance-20261105/gdi32/abi-validation.json',
            'runtime-components/native-maintenance-20261105/gdi32/script-cache-thread-ownership.patch',
            'runtime-components/native-maintenance-20261105/explorer/provenance/build-manifest.json',
            'runtime-components/native-maintenance-20261105/explorer/provenance/abi-validation.json',
            'runtime-components/native-maintenance-20261105/explorer/source/explorer-respect-appwindow.patch',
            'runtime-components/icon-fonts-1.147.5/install-plan.json',
            'runtime-components/icon-fonts-1.147.5/manifest.json',
            'runtime-components/icon-fonts-1.147.5/native-validation.json',
            'runtime-components/icon-fonts-1.147.5/deployment.json',
            'runtime-components/icon-fonts-1.147.5/compact-native-validation.json',
            'runtime-components/icon-fonts-1.147.5/e900-proof.json',
            'runtime-components/icon-fonts-1.147.5/supplemental-proof-manifest.json',
            'runtime-components/icon-fonts-1.147.5/name-only-invariants-summary.json',
            'runtime-components/icon-fonts-1.147.5/metadata-pipeline-summary.json',
            'runtime-components/icon-fonts-1.147.5/official-sources.json',
            'runtime-components/icon-fonts-1.147.5/source-review.txt',
            'runtime-components/native-maintenance-20261105/explorer-topmost/deployment.json',
            'runtime-components/native-maintenance-20261105/explorer-topmost/live-validation.json',
            'runtime-components/native-maintenance-20261105/explorer-topmost/official-sources.json',
            'runtime-components/native-maintenance-20261105/explorer-topmost/provenance/build-manifest.json',
            'runtime-components/native-maintenance-20261105/explorer-topmost/provenance/abi-validation.json',
            'runtime-components/native-maintenance-20261105/explorer-topmost/tests/synthetic-fixture-results.json',
            'logs/launch.log', 'logs/events.jsonl', 'logs/latest-session.json',
            'logs/launch.log.4', 'logs/gpu/legacy.log', 'logs/native-assert/native.log',
            'logs/sessions/session1/summary.json', 'logs/sessions/session1/python-fault.log',
            'logs/sessions/session1/runtime.raw.log',
            'logs/sessions/session1/gpu/Starter_d3d9.log',
            'logs/sessions/session1/native-assert/assert1.log',
            'prefix/drive_c/AMPQuantowerInstalled/Logs/Serilog/20261105.slog',
            'prefix/drive_c/AMPQuantowerInstalled/Logs/CefLogs.log',
            'prefix/drive_c/AMPQuantowerInstalled/Logs/Cef/CefLogs.log',
        )
        excluded = (
            'prefix/user.reg', 'prefix/drive_c/AMPQuantowerInstalled/Settings/accounts.xml',
            'logs/session-processes.json', 'logs/launch.lock', 'logs/arbitrary.log',
            'logs/runtime-profile-originals/user.reg', 'logs/launch.log.5',
            'runtime-components/d3d9.dll', 'runtime-components/x11/libX11.so.6',
            'runtime-components/libquantower_assert_trace.so',
            'runtime-components/application-upgrade.json.bak',
            'runtime-components/account-template-migration.xml',
            'runtime-components/gamescope/gamescope-brokey.original',
            'gamescope/files/bin/gamescope-brokey',
            'logs/runtime-profile-originals/Charts 4.xml.before-account-format.bak',
            'runtime-components/native-maintenance-20261105/rollback/manifest-before.json',
            'runtime-components/native-maintenance-20261105/rollback/gdiplus.dll',
            'runtime-components/native-maintenance-20261105/rollback/explorer.exe',
            'runtime-components/native-maintenance-20261105/explorer/source/systray.c',
            'runtime-components/native-maintenance-20261105/explorer/provenance/candidate-objdump.txt',
            'runtime-components/icon-fonts-1.147.5/rollback/manifest.json',
            'runtime-components/icon-fonts-1.147.5/rollback/icomoon.ttf',
            'runtime-components/icon-fonts-1.147.5/rollback/explorer.exe',
            'runtime-components/icon-fonts-1.147.5/native-public-api-probe.cpp',
            'runtime-components/icon-fonts-1.147.5/prepare-fonts.py',
            'runtime-components/native-maintenance-20261105/explorer-topmost/explorer.exe',
            'runtime-components/native-maintenance-20261105/explorer-topmost/source/explorer-taskbar-appwindow-and-topmost.patch',
            'runtime-components/native-maintenance-20261105/explorer-topmost/tests/taskbar-fixture.c',
            'runtime-components/native-maintenance-20261105/explorer-topmost/licenses/COPYING.LIB',
        )
        for name in included + excluded:
            self.source(name)
        path, manifest = bundle.collect(self.base, self.output, 1024, 32768)
        entries = self.contents(path)
        self.assertEqual({name.removeprefix('diagnostics/') for name in entries if name != 'manifest.json'},
                         set(included))
        self.assertEqual(manifest, json.loads(entries['manifest.json']))
        for name in included + excluded:
            self.assertEqual((self.base / name).read_bytes(), b'test diagnostic')

    def test_tails_and_total_payload_are_bounded_and_recorded(self):
        self.source('logs/launch.log', b'a' * 100 + b'LAST_BYTES')
        self.source('logs/events.jsonl', b'b' * 100)
        path, manifest = bundle.collect(self.base, self.output, 20, 25)
        entries = self.contents(path)
        self.assertEqual(entries['diagnostics/logs/launch.log'], b'a' * 10 + b'LAST_BYTES')
        self.assertEqual(entries['diagnostics/logs/events.jsonl'], b'b' * 5)
        self.assertEqual(manifest['payload_bytes'], 25)
        event = next(e for e in manifest['files'] if e['path'] == 'logs/launch.log')
        self.assertEqual(event['source_size'], 110)
        self.assertEqual(event['tail_offset'], 90)
        self.assertTrue(event['truncated'])

    def test_source_symlinks_and_symlink_ancestors_are_not_read(self):
        secret = self.source('prefix/user.reg', b'SECRET')
        (self.base / 'logs').mkdir()
        (self.base / 'logs/launch.log').symlink_to(secret)
        outside = self.base.parent / 'outside'
        outside.mkdir()
        (outside / '20261105.slog').write_bytes(b'SECRET')
        parent = self.base / 'prefix/drive_c/AMPQuantowerInstalled/Logs'
        parent.mkdir(parents=True)
        (parent / 'Serilog').symlink_to(outside, target_is_directory=True)
        path, manifest = bundle.collect(self.base, self.output, 1024, 32768)
        entries = self.contents(path)
        self.assertNotIn('diagnostics/logs/launch.log', entries)
        self.assertFalse(any('20261105.slog' in name for name in entries))
        self.assertTrue(any(e['path'] == 'logs/launch.log' for e in manifest['skipped']))

    def test_private_output_permissions_and_no_overwrite(self):
        self.source('logs/launch.log')
        path, _ = bundle.collect(self.base, self.output, 1024, 32768)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
        original = path.read_bytes()
        with self.assertRaises(FileExistsError):
            bundle.collect(self.base, self.output, 1024, 32768)
        self.assertEqual(path.read_bytes(), original)

    def test_public_output_directory_and_output_symlink_rejected(self):
        public = self.base / 'public'
        public.mkdir(mode=0o755)
        public.chmod(0o755)
        with self.assertRaises(ValueError):
            bundle.collect(self.base, public / 'bundle.tar.gz', 1024, 32768)
        secret = self.source('original.tar.gz', b'PRESERVE')
        destination = self.base / 'redirected.tar.gz'
        destination.symlink_to(secret)
        with self.assertRaises(ValueError):
            bundle.collect(self.base, destination, 1024, 32768)
        self.assertEqual(secret.read_bytes(), b'PRESERVE')

    def test_only_three_recent_vendor_days_and_sessions_are_selected(self):
        for index in range(4):
            day = self.source(f'prefix/drive_c/AMPQuantowerInstalled/Logs/Serilog/day{index}.slog')
            session = self.source(f'logs/sessions/session{index}/summary.json').parent
            os.utime(day, (1000 + index, 1000 + index))
            os.utime(session, (1000 + index, 1000 + index))
        path, _ = bundle.collect(self.base, self.output, 1024, 32768)
        entries = self.contents(path)
        self.assertNotIn('diagnostics/logs/sessions/session0/summary.json', entries)
        self.assertNotIn('diagnostics/prefix/drive_c/AMPQuantowerInstalled/Logs/Serilog/day0.slog', entries)
        self.assertEqual(sum(name.endswith('.slog') for name in entries), 3)
        self.assertEqual(sum(name.endswith('/summary.json') for name in entries), 3)

    def test_cli_limits_must_be_positive_finite_and_reasonable(self):
        for value in ('0', '-1', 'nan', 'inf', '1025'):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                bundle.positive_mib(value)
        self.assertEqual(bundle.positive_mib('0.5'), bundle.MIB // 2)


if __name__ == '__main__':
    unittest.main()
