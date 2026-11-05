import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('public_release_checker', ROOT / 'tools/check-public-release.py')
checker = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = checker
spec.loader.exec_module(checker)


class PublicReleaseTests(unittest.TestCase):
    def test_rejects_private_payloads_and_allows_native_source(self):
        for name in ('prefix/user.reg', 'logs/session-processes.json', 'config/pulse/cookie',
                     'launcher-config.json', 'maintenance/report.json', 'third_party/vendor.ttf',
                     'native/rollback/old.c'):
            with self.subTest(name=name):
                self.assertTrue(checker.inspect(checker.Source(name, b'{}')))
        self.assertEqual(checker.inspect(checker.Source('native/probe.c', b'int main(void) { return 0; }')), [])
        self.assertEqual(checker.inspect(checker.Source('launcher-config.example.json', b'{}')), [])

    def test_rejects_personal_paths_and_credential_without_echoing_values(self):
        private = ('/home/' + 'private-person/work/app').encode()
        key = ('ghp_' + 'A' * 36).encode()
        result = checker.audit([checker.Source('notes.txt', private + b' ' + key)])
        self.assertEqual(result['result'], 'FAIL')
        self.assertIn('personal_home_path', result['findings'][0]['issues'])
        self.assertIn('github_credential', result['findings'][0]['issues'])
        self.assertNotIn('private-person', str(result))
        self.assertNotIn(key.decode(), str(result))

    def test_index_audit_reads_staged_blob_not_clean_working_copy(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            file = root / 'notes.txt'
            file.write_text('/home/' + 'private-person/secret')
            subprocess.run(['git', '-C', str(root), 'add', 'notes.txt'], check=True)
            file.write_text('safe working copy\n')
            result = checker.audit(checker.indexed_sources(root))
            self.assertEqual(result['result'], 'FAIL')
            self.assertEqual(result['findings'][0]['issues'], ['personal_home_path'])

    def test_ignored_state_does_not_enter_index_audit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            (root / '.gitignore').write_text('/prefix/\n')
            (root / 'code.py').write_text('print("safe")\n')
            (root / 'prefix').mkdir()
            (root / 'prefix/user.reg').write_text('private installation state')
            subprocess.run(['git', '-C', str(root), 'add', '.'], check=True)
            self.assertEqual(checker.audit(checker.indexed_sources(root))['result'], 'PASS')
            self.assertEqual(checker.audit(checker.tree_sources(root))['result'], 'FAIL')

    def test_export_rejects_symlink_without_following_target(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'external').symlink_to('/nonexistent-private-target')
            result = checker.audit(checker.tree_sources(root))
            self.assertIn('symlink_or_nonregular_source', result['findings'][0]['issues'])

    def test_synthetic_tokens_and_api_declarations_are_allowed(self):
        data = b"SESSION_VARIABLE = 'QUANTOWER_LAUNCH_SESSION'\ntoken = '1' * 32\n"
        self.assertEqual(checker.inspect(checker.Source('tests/test_ownership.py', data)), [])


if __name__ == '__main__':
    unittest.main()
