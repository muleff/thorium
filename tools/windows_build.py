#!/usr/bin/env python3
"""Validate, build and test the modified Windows x64 Thorium installer."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import struct
import subprocess
import sys
from typing import Mapping, Sequence

VERSION = "138.0.7204.306"
MIN_FREE_BYTES = 100 * 1024**3
OVERLAYS = (
    "components/performance_manager/user_tuning/prefs.cc",
    "components/performance_manager/user_tuning/prefs_unittest.cc",
    "chrome/browser/ui/browser_ui_prefs.cc",
)


class BuildError(RuntimeError):
    """A build prerequisite, command or artifact failed validation."""


@dataclass(frozen=True)
class CpuProfile:
    args_file: str
    setup_flag: str | None
    installer: str


CPU_PROFILES = {
    "avx": CpuProfile("win_args.gn", None, "thorium_AVX_mini_installer.exe"),
    "avx2": CpuProfile("other/AVX2/win_AVX2_args.gn", "--avx2", "thorium_AVX2_mini_installer.exe"),
    "sse4": CpuProfile("other/SSE4.1/win64_SSE4.1_args.gn", "--sse4", "thorium_SSE4_mini_installer.exe"),
    "sse3": CpuProfile("other/SSE3/win64_SSE3_args.gn", "--sse3", "thorium_SSE3_mini_installer.exe"),
}


def read_version(source: Path) -> str:
    try:
        fields = dict(line.split("=", 1) for line in
                      (source / "chrome/VERSION").read_text(encoding="utf-8").splitlines()
                      if "=" in line)
        values = [fields[key].strip() for key in ("MAJOR", "MINOR", "BUILD", "PATCH")]
        if not all(value.isdigit() for value in values):
            raise ValueError("version fields must be integers")
        return ".".join(values)
    except (OSError, KeyError, ValueError) as error:
        raise BuildError(f"Invalid Chromium chrome/VERSION: {error}") from error


def output_path(source: Path, value: str) -> Path:
    source = source.resolve()
    destination = (source / value).resolve()
    if destination == source or not destination.is_relative_to(source):
        raise BuildError("Build output must be a subdirectory of Chromium src")
    return destination


def matching_pgo(source: Path) -> Path:
    try:
        name = (source / "chrome/build/win64.pgo.txt").read_text(encoding="utf-8").strip()
        if not name or any(char in name for char in "/\\:") or not name.endswith(".profdata"):
            raise BuildError("Invalid win64.pgo.txt profile filename")
        profile = source / "chrome/build/pgo_profiles" / name
        if not profile.is_file() or profile.stat().st_size == 0:
            raise BuildError(f"Matching PGO profile missing: {profile}; run Thorium setup.py")
        return profile
    except OSError as error:
        raise BuildError(f"Cannot resolve matching Windows PGO profile: {error}") from error


def available_tools(source: Path) -> dict[str, str]:
    result = {}
    for name in ("gn", "autoninja", "cl.exe"):
        executable = shutil.which(name)
        if executable:
            result[name] = executable
    bundled_gn = source / "buildtools/win/gn.exe"
    if bundled_gn.is_file():
        result["gn"] = str(bundled_gn)
    return result


def preflight(repo: Path, source: Path, cpu: str, *, host: str | None = None,
              free_bytes: int | None = None,
              tools: Mapping[str, str] | None = None,
              prepare: bool = False) -> list[str]:
    issues = []
    if (host or platform.system()) != "Windows":
        issues.append("Native build requires Windows 10/11 x64 with Visual Studio 2022 and SDK 22621")
    if free_bytes is None:
        parent = source.resolve()
        while not parent.exists():
            parent = parent.parent
        free_bytes = shutil.disk_usage(parent).free
    if free_bytes < MIN_FREE_BYTES:
        issues.append(f"At least 100 GiB free required; available: {free_bytes / 1024**3:.1f} GiB")
    resolved_tools = available_tools(source) if tools is None else tools
    for name in ("gn", "autoninja", "cl.exe"):
        if name not in resolved_tools:
            issues.append(f"Missing {name}; use depot_tools and an x64 Native Tools terminal")
    try:
        actual_version = read_version(source)
        if actual_version != VERSION:
            issues.append(f"Chromium version {actual_version} differs from required {VERSION}")
    except BuildError as error:
        issues.append(str(error))
    for relative in ("DEPS", "build/vs_toolchain.py", "tools/update_pgo_profiles.py"):
        if not (source / relative).is_file():
            issues.append(f"Incomplete Chromium checkout: missing {relative}")
    if not (repo / CPU_PROFILES[cpu].args_file).is_file():
        issues.append(f"Missing Thorium GN CPU profile: {cpu}")
    if prepare:
        if not (repo / "thorium-libjxl/src/DEPS").is_file():
            issues.append("Missing pinned thorium-libjxl submodule; run git submodule update --init")
    else:
        try:
            matching_pgo(source)
        except BuildError as error:
            issues.append(str(error))
        for relative in OVERLAYS:
            expected, actual = repo / "src" / relative, source / relative
            if not expected.is_file() or not actual.is_file() or expected.read_bytes() != actual.read_bytes():
                issues.append(f"Modified Thorium overlay not applied: {relative}; run with --prepare once")
        marker = source / "out/thorium/thor_ver"
        expected_cpu = {"avx": "AVX", "avx2": "AVX2", "sse4": "SSE4", "sse3": "SSE3"}[cpu]
        if not marker.is_file() or marker.read_text(encoding="utf-8").split() != ["Win", expected_cpu]:
            issues.append(f"Applied source CPU marker does not match {cpu}; prepare the matching CPU overlay")
    return issues


def command_line(executable: str, arguments: Sequence[str], host: str | None = None) -> list[str] | str:
    command = [executable, *arguments]
    if (host or platform.system()) == "Windows" and Path(executable).suffix.lower() in (".bat", ".cmd"):
        if any(any(char in value for char in '%!&|<>^"\r\n') for value in command):
            raise BuildError("Unsafe cmd.exe metacharacter in build path or argument")
        line = subprocess.list2cmdline(command)
        interpreter = subprocess.list2cmdline([os.environ.get("COMSPEC", "cmd.exe")])
        return f'{interpreter} /d /s /c "{line}"'
    return command


def run(executable: str, arguments: Sequence[str], cwd: Path,
        environment: Mapping[str, str] | None = None) -> None:
    command = command_line(executable, arguments)
    printable = command if isinstance(command, str) else subprocess.list2cmdline(command)
    print(f"Running: {printable}", flush=True)
    try:
        subprocess.run(command, cwd=cwd, env=environment, check=True)
    except (OSError, subprocess.CalledProcessError) as error:
        raise BuildError(f"Build command failed: {error}") from error


def validate_pe(path: Path) -> None:
    try:
        with path.open("rb") as binary:
            header = binary.read(64)
            if len(header) != 64 or header[:2] != b"MZ":
                raise BuildError(f"Not a PE executable: {path}")
            offset = struct.unpack_from("<I", header, 0x3C)[0]
            if offset < 64 or offset > path.stat().st_size - 6:
                raise BuildError(f"Invalid PE header offset: {path}")
            binary.seek(offset)
            signature = binary.read(6)
            if signature[:4] != b"PE\x00\x00" or signature[4:] != b"\x64\x86":
                raise BuildError(f"Expected Windows x64 PE executable: {path}")
    except OSError as error:
        raise BuildError(f"Cannot read executable {path}: {error}") from error


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        digest = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
        return digest.hexdigest()


def validate_native_results(path: Path) -> int:
    required = {
        "MemorySaverModeDefaultTest.PlatformDefaultSurvivesMigrations",
        "MemorySaverModeDefaultTest.ExplicitDisableSurvivesMigrations",
        "MemorySaverModeDefaultTest.LegacyExplicitDisableSurvivesNewDefault",
        "MemorySaverModeDefaultTest.ManagedDisableTakesPrecedence",
    }
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise BuildError(f"Cannot read native test results: {error}") from error
    iterations = report.get("per_iteration_data") if isinstance(report, dict) else None
    if not isinstance(iterations, list) or not iterations:
        raise BuildError("Native test report has no completed iterations")
    executed: set[str] = set()
    for iteration in iterations:
        if not isinstance(iteration, dict) or not required.issubset(iteration):
            raise BuildError("Native test report is missing required MemorySaverMode regressions")
        for name, results in iteration.items():
            if not isinstance(results, list) or not results:
                raise BuildError(f"No native results for {name}")
            if any(not isinstance(result, dict) or result.get("status") != "SUCCESS"
                   for result in results):
                raise BuildError(f"Native test did not complete successfully: {name}")
            executed.add(name)
    return len(executed)


def package_installer(output: Path, destination: Path,
                      metadata: Mapping[str, object],
                      filename: str = "thorium_mini_installer.exe") -> dict[str, object]:
    if Path(filename).name != filename or any(char in filename for char in "/\\:"):
        raise BuildError("Installer filename must be a basename")
    installer = output / filename
    validate_pe(installer)
    destination.mkdir(parents=True, exist_ok=True)
    copied = destination / installer.name
    if copied.resolve() == installer.resolve():
        raise BuildError("Artifact directory must differ from build output")
    shutil.copy2(installer, copied)
    digest = sha256(copied)
    if digest != sha256(installer):
        raise BuildError("Installer changed while packaging")
    artifact = {"filename": copied.name, "bytes": copied.stat().st_size, "sha256": digest}
    (destination / "SHA256SUMS").write_text(f"{digest}  {copied.name}\n", encoding="utf-8")
    (destination / "build-manifest.json").write_text(
        json.dumps({**metadata, "artifact": artifact}, indent=2) + "\n", encoding="utf-8")
    return artifact


def main(arguments: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chromium-src", required=True, type=Path)
    parser.add_argument("--cpu", choices=CPU_PROFILES, default="avx")
    parser.add_argument("--jobs", type=int, default=min(os.cpu_count() or 1, 4))
    parser.add_argument("--prepare", action="store_true", help="Run upstream setup.py once on a clean, synced pinned Chromium tree")
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--report", type=Path, help="Write preflight JSON even on failure")
    parser.add_argument("--artifacts", type=Path, default=Path("artifacts/windows"))
    options = parser.parse_args(arguments)
    if not 1 <= options.jobs <= 128:
        parser.error("--jobs must be between 1 and 128")
    repo = Path(__file__).resolve().parents[1]
    source = options.chromium_src.resolve()
    issues = preflight(repo, source, options.cpu, prepare=options.prepare)
    report = {"version": VERSION, "host": platform.system(), "issues": issues,
              "browser_built": False, "native_tests_run": False}
    if options.report:
        options.report.parent.mkdir(parents=True, exist_ok=True)
        options.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if issues:
        for issue in issues:
            print(f"BLOCKED: {issue}", file=sys.stderr)
        return 2
    if options.check_only:
        print("Preflight passed; no build commands executed.")
        return 0
    profile = CPU_PROFILES[options.cpu]
    if options.prepare:
        environment = dict(os.environ, CR_DIR=str(source), THOR_DIR=str(repo))
        run(sys.executable, [str(repo / "win_scripts/setup.py"),
                            *([profile.setup_flag] if profile.setup_flag else [])], repo, environment)
        issues = preflight(repo, source, options.cpu)
        if issues:
            raise BuildError("Post-setup validation failed: " + "; ".join(issues))
    output = output_path(source, "out/thorium")
    output.mkdir(parents=True, exist_ok=True)
    args_file = output / "args.gn"
    if args_file.exists() and not args_file.with_suffix(".gn.before-efficiency").exists():
        shutil.copy2(args_file, args_file.with_suffix(".gn.before-efficiency"))
    shutil.copy2(repo / profile.args_file, args_file)
    tools = available_tools(source)
    run(tools["gn"], ["gen", "out/thorium", "--fail-on-unused-args"], source)
    run(tools["autoninja"], ["-C", "out/thorium", "thorium_all", "setup", "mini_installer",
                             "components_unittests", f"-j{options.jobs}"], source)
    options.artifacts.mkdir(parents=True, exist_ok=True)
    native_report = options.artifacts.resolve() / "native-tests.json"
    native_report.unlink(missing_ok=True)
    run(str(output / "components_unittests.exe"),
        ["--gtest_filter=MemorySaverMode*", "--test-launcher-bot-mode",
         "--test-launcher-summary-output=" + str(native_report)], source)
    native_count = validate_native_results(native_report)
    validate_pe(output / "thorium.exe")
    metadata = {"chromium_version": VERSION, "cpu": options.cpu,
                "args_sha256": sha256(args_file), "pgo_sha256": sha256(matching_pgo(source)),
                "native_tests": "MemorySaverMode*", "native_test_count": native_count,
                "hardware_performance_verified": False,
                "modified_sources": {name: sha256(repo / "src" / name) for name in OVERLAYS}}
    artifact = package_installer(output, options.artifacts.resolve(), metadata, profile.installer)
    if options.report:
        report.update(browser_built=True, native_tests_run=True, artifact=artifact)
        options.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Built and tested installer: {options.artifacts / str(artifact['filename'])}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (BuildError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
