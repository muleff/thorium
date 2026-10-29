import importlib.util
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

MODULE = Path(__file__).resolve().parents[1] / "tools" / "windows_build.py"


def load_module():
    spec = importlib.util.spec_from_file_location("windows_build", MODULE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class WindowsBuildTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = load_module()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_read_version_requires_all_four_fields(self):
        source = self.root / "chrome"
        source.mkdir()
        (source / "VERSION").write_text(
            "MAJOR=138\nMINOR=0\nBUILD=7204\nPATCH=306\n", encoding="utf-8")
        self.assertEqual(self.build.read_version(self.root), "138.0.7204.306")
        (source / "VERSION").write_text("MAJOR=138\n", encoding="utf-8")
        with self.assertRaises(self.build.BuildError):
            self.build.read_version(self.root)

    def test_output_path_cannot_escape_source_or_replace_source_root(self):
        for value in ["../outside", ".", "out/../../outside"]:
            with self.subTest(value=value):
                with self.assertRaises(self.build.BuildError):
                    self.build.output_path(self.root, value)
        expected = self.root / "out" / "release with spaces"
        self.assertEqual(self.build.output_path(self.root, "out/release with spaces"), expected)

    def test_pgo_uses_exact_revision_profile_instead_of_newest(self):
        directory = self.root / "chrome" / "build"
        profiles = directory / "pgo_profiles"
        profiles.mkdir(parents=True)
        (directory / "win64.pgo.txt").write_text("expected.profdata\n", encoding="utf-8")
        (profiles / "unrelated-newer.profdata").write_bytes(b"unrelated")
        with self.assertRaises(self.build.BuildError):
            self.build.matching_pgo(self.root)
        expected = profiles / "expected.profdata"
        expected.write_bytes(b"profile fixture")
        self.assertEqual(self.build.matching_pgo(self.root), expected)

    def test_pgo_rejects_path_traversal_and_empty_data(self):
        directory = self.root / "chrome" / "build"
        directory.mkdir(parents=True)
        for value in ["../../escape.profdata", "..\\escape.profdata", "", "/tmp/a"]:
            (directory / "win64.pgo.txt").write_text(value, encoding="utf-8")
            with self.subTest(value=value):
                with self.assertRaises(self.build.BuildError):
                    self.build.matching_pgo(self.root)

    def test_check_reports_all_missing_infrastructure_without_mutations(self):
        issues = self.build.preflight(
            self.root, self.root / "missing", "avx", host="Linux",
            free_bytes=25 * 1024**3, tools={})
        self.assertTrue(any("Windows" in issue for issue in issues))
        self.assertTrue(any("100" in issue for issue in issues))
        self.assertTrue(any("Chromium" in issue for issue in issues))
        self.assertTrue(any("gn" in issue for issue in issues))
        self.assertEqual(list(self.root.iterdir()), [])

    def test_cpu_profiles_keep_source_setup_and_gn_variant_paired(self):
        profile = self.build.CPU_PROFILES["sse3"]
        self.assertEqual(profile.setup_flag, "--sse3")
        self.assertEqual(profile.args_file, "other/SSE3/win64_SSE3_args.gn")
        self.assertEqual(profile.installer, "thorium_SSE3_mini_installer.exe")
        self.assertEqual(self.build.CPU_PROFILES["avx"].args_file, "win_args.gn")

    def test_package_rejects_non_pe_and_wrong_architecture(self):
        binary = self.root / "installer.exe"
        for content in [b"not a binary", b"MZ" + bytes(80)]:
            binary.write_bytes(content)
            with self.assertRaises(self.build.BuildError):
                self.build.validate_pe(binary)
        self.write_pe(binary, machine=0x14C)
        with self.assertRaises(self.build.BuildError):
            self.build.validate_pe(binary)

    def write_pe(self, path, machine=0x8664):
        content = bytearray(512)
        content[:2] = b"MZ"
        struct.pack_into("<I", content, 0x3C, 128)
        content[128:132] = b"PE\x00\x00"
        struct.pack_into("<H", content, 132, machine)
        path.write_bytes(content)

    def test_package_preserves_bytes_and_records_sha256(self):
        source = self.root / "out"
        source.mkdir()
        binary = source / "thorium_mini_installer.exe"
        self.write_pe(binary)
        destination = self.root / "artifacts"
        result = self.build.package_installer(source, destination, {"version": "test"})
        copied = destination / "thorium_mini_installer.exe"
        self.assertEqual(copied.read_bytes(), binary.read_bytes())
        import hashlib
        digest = hashlib.sha256(binary.read_bytes()).hexdigest()
        self.assertEqual(result["sha256"], digest)
        manifest = json.loads((destination / "build-manifest.json").read_text())
        self.assertEqual(manifest["artifact"]["sha256"], digest)
        self.assertIn(digest, (destination / "SHA256SUMS").read_text())

    def test_package_selects_cpu_variant_and_never_falls_back_to_generic(self):
        output = self.root / "out"
        output.mkdir()
        self.write_pe(output / "thorium_mini_installer.exe")
        selected = self.build.CPU_PROFILES["sse3"].installer
        with self.assertRaises(self.build.BuildError):
            self.build.package_installer(output, self.root / "artifacts", {}, selected)
        self.write_pe(output / selected)
        result = self.build.package_installer(output, self.root / "artifacts", {}, selected)
        self.assertEqual(result["filename"], selected)

    def test_command_arguments_remain_separate(self):
        self.assertEqual(
            self.build.command_line("gn.exe", ["gen", "out/release with spaces"], "Windows"),
            ["gn.exe", "gen", "out/release with spaces"])
        with self.assertRaises(self.build.BuildError):
            self.build.command_line("gn.bat", ["gen", "out/%unsafe%"], "Windows")

    def test_batch_command_avoids_a_second_windows_argv_quoting_layer(self):
        command = self.build.command_line(
            r"C:\depot tools\autoninja.bat", ["-C", "out/thorium", "thorium"], "Windows")
        self.assertIsInstance(command, str)
        self.assertIn('/d /s /c ""C:\\depot tools\\autoninja.bat" -C out/thorium thorium"', command)
        self.assertNotIn('\\"', command)

    def test_native_report_requires_each_new_regression_to_execute_successfully(self):
        report = self.root / "native-tests.json"
        expected = [
            "MemorySaverModeDefaultTest.PlatformDefaultSurvivesMigrations",
            "MemorySaverModeDefaultTest.ExplicitDisableSurvivesMigrations",
            "MemorySaverModeDefaultTest.LegacyExplicitDisableSurvivesNewDefault",
            "MemorySaverModeDefaultTest.ManagedDisableTakesPrecedence",
        ]
        successful = {name: [{"status": "SUCCESS"}] for name in expected}
        report.write_text(json.dumps({"per_iteration_data": [successful]}), encoding="utf-8")
        self.assertEqual(self.build.validate_native_results(report), 4)
        for status in ["FAILURE", "SKIPPED", "NOTRUN"]:
            failed = dict(successful, **{expected[0]: [{"status": status}]})
            report.write_text(json.dumps({"per_iteration_data": [failed]}), encoding="utf-8")
            with self.subTest(status=status):
                with self.assertRaises(self.build.BuildError):
                    self.build.validate_native_results(report)
        for data in [{}, {"per_iteration_data": []}, {"per_iteration_data": [{}]}]:
            report.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(self.build.BuildError):
                self.build.validate_native_results(report)

    def test_native_report_rejects_missing_or_invalid_json(self):
        report = self.root / "native-tests.json"
        with self.assertRaises(self.build.BuildError):
            self.build.validate_native_results(report)
        report.write_text("not JSON", encoding="utf-8")
        with self.assertRaises(self.build.BuildError):
            self.build.validate_native_results(report)


if __name__ == "__main__":
    unittest.main()
