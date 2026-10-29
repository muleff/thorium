import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))


class PrepareWindowsCITest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = TOOLS / "prepare_windows_ci.py"
        if not path.is_file():
            raise AssertionError("Windows CI preparation script is missing")
        spec = importlib.util.spec_from_file_location("prepare_windows_ci", path)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_invalid_depot_revision_cannot_become_a_git_option(self):
        for value in ["main", "--upload-pack=bad", "f" * 39, "g" * 40]:
            with self.subTest(value=value):
                with self.assertRaises(self.module.BuildError):
                    self.module.validate_revision(value)
        self.assertEqual(self.module.validate_revision("a" * 40), "a" * 40)

    def test_jxl_dependency_overlay_copies_all_three_files(self):
        repo = self.root / "repo"
        source = self.root / "src"
        source.mkdir()
        for name in self.module.DEPENDENCY_FILES:
            original = repo / "thorium-libjxl/src" / name
            original.parent.mkdir(parents=True, exist_ok=True)
            original.write_bytes((name + "\n").encode())
        self.module.copy_dependency_overlay(repo, source)
        for name in self.module.DEPENDENCY_FILES:
            self.assertEqual((source / name).read_bytes(), (name + "\n").encode())

    def test_missing_dependency_overlay_fails_before_partial_copy(self):
        repo = self.root / "repo"
        source = self.root / "src"
        source.mkdir()
        original = repo / "thorium-libjxl/src/DEPS"
        original.parent.mkdir(parents=True)
        original.write_text("revision data", encoding="utf-8")
        with self.assertRaises(self.module.BuildError):
            self.module.copy_dependency_overlay(repo, source)
        self.assertEqual(list(source.iterdir()), [])

    def test_checkout_fetches_only_requested_revision_from_local_git(self):
        original = self.root / "original"
        original.mkdir()
        def git(*args):
            return subprocess.check_output(["git", "-C", str(original), *args], text=True).strip()
        git("init", "--quiet")
        git("config", "user.name", "Test")
        git("config", "user.email", "test@example.invalid")
        (original / "revision.txt").write_text("pinned", encoding="utf-8")
        git("add", ".")
        git("commit", "--quiet", "-m", "Pinned")
        pinned = git("rev-parse", "HEAD")
        (original / "revision.txt").write_text("newer", encoding="utf-8")
        git("commit", "--quiet", "-am", "Newer")
        destination = self.root / "checkout"
        self.module.checkout(str(original), pinned, destination)
        self.assertEqual((destination / "revision.txt").read_text(), "pinned")
        actual = subprocess.check_output(["git", "-C", str(destination), "rev-parse", "HEAD"], text=True).strip()
        self.assertEqual(actual, pinned)

    def test_checkout_never_overwrites_an_existing_directory(self):
        directory = self.root / "existing"
        directory.mkdir()
        keep = directory / "keep.txt"
        keep.write_text("keep", encoding="utf-8")
        with self.assertRaises(self.module.BuildError):
            self.module.checkout("unused", "a" * 40, directory)
        self.assertEqual(keep.read_text(), "keep")

    def test_build_space_never_allows_an_artifact_directory_inside_temp_root(self):
        with self.assertRaises(self.module.BuildError):
            self.module.validate_paths(self.root, self.root / "nested")
        self.module.validate_paths(self.root / "build", self.root / "artifacts")


if __name__ == "__main__":
    unittest.main()
