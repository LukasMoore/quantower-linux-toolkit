"""Offline checks only: no compiler, native executable, display, or Wine launch."""
import contextlib
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    'window_authority_source_tests', ROOT / 'tests/native/window-authority/run-tests.py')
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def fake_source(root):
    for relative in runner.SOURCE_FILES:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('/* Synthetic source for offline path validation. */\n')
    return root


class ExtractorTests(unittest.TestCase):
    def test_extracts_actual_function_while_ignoring_string_char_and_comment_braces(self):
        source = '''unrelated declaration;
static BOOL selected( HWND hwnd )
{
    const char *text = "escaped quote: \\\" and brace }";
    char close = '}'; /* } ignored { */
    // } ignored {
    if (hwnd) { return TRUE; }
    return FALSE;
}
static BOOL unrelated( HWND hwnd ) { return FALSE; }
'''
        result = runner.extract_function(source, 'selected')
        self.assertTrue(result.startswith('static BOOL selected('))
        self.assertTrue(result.endswith('}\n'))
        self.assertIn('return FALSE;', result)
        self.assertNotIn('unrelated', result)

    def test_missing_required_optional_and_unterminated_functions(self):
        with self.assertRaisesRegex(ValueError, 'Missing actual source function'):
            runner.extract_function('/* no functions */', 'missing')
        self.assertEqual(runner.extract_function('', 'missing', optional=True), '')
        with self.assertRaisesRegex(ValueError, 'Unterminated actual source function'):
            runner.extract_function('static void broken( void )\n{\n', 'broken')

    def test_calls_and_function_name_prefixes_do_not_match(self):
        source = '''call(selected);
static BOOL selected_suffix( void )
{
    return FALSE;
}
static BOOL selected( void )
{
    return TRUE;
}
'''
        self.assertEqual(runner.extract_function(source, 'selected'),
                         'static BOOL selected( void )\n{\n    return TRUE;\n}\n')


class ExplicitOfflineGuards(unittest.TestCase):
    def test_all_three_paths_are_required_without_discovery_or_execution(self):
        with mock.patch.object(runner, 'run') as run, contextlib.redirect_stderr(io.StringIO()):
            for args in ([], ['--output', 'unused'],
                         ['--baseline-source', 'unused', '--candidate-source', 'unused']):
                with self.subTest(args=args), self.assertRaises(SystemExit):
                    runner.main(args)
            run.assert_not_called()

    def test_separate_valid_roots_and_fresh_scratch_are_accepted_without_writes(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            old, new = fake_source(base / 'baseline'), fake_source(base / 'candidate')
            output = base / 'fresh'
            self.assertEqual(runner.validate_paths(old, new, output), (old, new, output))
            self.assertFalse(output.exists())

    def test_existing_output_file_directory_and_dangling_symlink_are_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            old, new = fake_source(base / 'baseline'), fake_source(base / 'candidate')
            file = base / 'file'
            file.write_text('preserve')
            directory = base / 'directory'
            directory.mkdir()
            link = base / 'dangling'
            link.symlink_to(base / 'missing')
            for output in (file, directory, link):
                with self.subTest(output=output.name), self.assertRaises(FileExistsError):
                    runner.validate_paths(old, new, output)
            self.assertEqual(file.read_text(), 'preserve')
            self.assertTrue(directory.is_dir())
            self.assertTrue(link.is_symlink())

    def test_identical_sources_missing_inputs_and_source_symlinks_refuse(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            old, new = fake_source(base / 'baseline'), fake_source(base / 'candidate')
            with self.assertRaisesRegex(ValueError, 'separate source trees'):
                runner.validate_paths(old, old, base / 'fresh')
            source = new / runner.SOURCE_FILES[0]
            source.unlink()
            with self.assertRaisesRegex(ValueError, 'regular nonsymlink'):
                runner.validate_paths(old, new, base / 'fresh')
            source.symlink_to(old / runner.SOURCE_FILES[0])
            with self.assertRaisesRegex(ValueError, 'regular nonsymlink'):
                runner.validate_paths(old, new, base / 'fresh')
            self.assertFalse((base / 'fresh').exists())

    def test_outputs_inside_input_trees_or_repository_refuse(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            old, new = fake_source(base / 'baseline'), fake_source(base / 'candidate')
            for output in (old / 'fresh', new / 'fresh', ROOT / 'unused-test-output'):
                with self.subTest(output=output), self.assertRaisesRegex(ValueError, 'outside source trees'):
                    runner.validate_paths(old, new, output)
                self.assertFalse(output.exists())

    def test_output_parent_must_exist(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            old, new = fake_source(base / 'baseline'), fake_source(base / 'candidate')
            with self.assertRaisesRegex(ValueError, 'parent must already exist'):
                runner.validate_paths(old, new, base / 'absent' / 'fresh')

    def test_bad_output_refuses_before_compiler_lookup_or_subprocess(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            old, new = fake_source(base / 'baseline'), fake_source(base / 'candidate')
            output = base / 'retained'
            output.mkdir()
            with mock.patch.object(runner.shutil, 'which') as which, \
                    mock.patch.object(runner.subprocess, 'run') as spawn, \
                    mock.patch.object(runner.subprocess, 'check_output') as check:
                with self.assertRaises(FileExistsError):
                    runner.run(old, new, output)
                which.assert_not_called()
                spawn.assert_not_called()
                check.assert_not_called()

    def test_missing_policy_functions_fail_before_output_or_native_execution(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            old, new = fake_source(base / 'baseline'), fake_source(base / 'candidate')
            output = base / 'fresh'
            with mock.patch.object(runner.shutil, 'which', return_value='/synthetic/compiler'), \
                    mock.patch.object(runner.subprocess, 'run') as spawn, \
                    mock.patch.object(runner.subprocess, 'check_output') as check:
                with self.assertRaises(ValueError):
                    runner.run(old, new, output)
                spawn.assert_not_called()
                check.assert_not_called()
                self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
