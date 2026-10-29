#!/usr/bin/env python3
"""Prepare an isolated, pinned Chromium tree and build the Windows installer."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import re
import shutil
import sys
import tempfile
from typing import Sequence

from windows_build import BuildError, CPU_PROFILES, VERSION, run

DEPENDENCY_FILES = ("DEPS", ".gitmodules", "third_party/.gitignore")
MIN_START_BYTES = 300 * 1024**3
DEPOT_URL = "https://chromium.googlesource.com/chromium/tools/depot_tools.git"
CHROMIUM_URL = "https://chromium.googlesource.com/chromium/src.git"


def validate_revision(value: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{40}", value):
        raise BuildError("depot_tools revision must be an exact 40-character commit SHA")
    return value


def validate_paths(build_base: Path, artifacts: Path) -> None:
    base = build_base.resolve()
    destination = artifacts.resolve()
    if destination == base or destination.is_relative_to(base):
        raise BuildError("Artifacts must be outside the disposable build directory")


def checkout(url: str, revision: str, destination: Path) -> None:
    if destination.exists():
        raise BuildError(f"Refusing to replace existing checkout: {destination}")
    destination.mkdir(parents=True)
    run("git", ["init", "--quiet"], destination)
    run("git", ["config", "core.longpaths", "true"], destination)
    run("git", ["config", "core.autocrlf", "false"], destination)
    run("git", ["remote", "add", "origin", url], destination)
    run("git", ["fetch", "--depth=1", "origin", revision], destination)
    run("git", ["checkout", "--detach", "FETCH_HEAD"], destination)


def copy_dependency_overlay(repo: Path, source: Path) -> None:
    original = repo / "thorium-libjxl/src"
    missing = [name for name in DEPENDENCY_FILES if not (original / name).is_file()]
    if missing:
        raise BuildError("Pinned JXL dependency overlay missing: " + ", ".join(missing))
    for name in DEPENDENCY_FILES:
        target = source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(original / name, target)


def build(repo: Path, workspace: Path, artifacts: Path, cpu: str, jobs: int,
          depot_revision: str) -> None:
    depot = workspace / "depot_tools"
    chromium = workspace / "chromium"
    source = chromium / "src"
    checkout(DEPOT_URL, depot_revision, depot)
    environment = dict(os.environ)
    environment.update(
        PATH=str(depot) + os.pathsep + environment.get("PATH", ""),
        DEPOT_TOOLS_WIN_TOOLCHAIN="0", DEPOT_TOOLS_UPDATE="0",
        DEPOT_TOOLS_METRICS="0", GCLIENT_PY3="1")
    chromium.mkdir()
    (chromium / ".gclient").write_text(
        "solutions = [{\"name\": \"src\", \"url\": " + repr(CHROMIUM_URL) +
        ", \"managed\": False, \"custom_deps\": {}, \"custom_vars\": {}}]\n"
        "target_os = [\"win\"]\n", encoding="utf-8")
    checkout(CHROMIUM_URL, "refs/tags/" + VERSION, source)
    copy_dependency_overlay(repo, source)
    gclient = str(depot / "gclient.bat")
    run(gclient, ["sync", "--no-history", "--nohooks", f"--jobs={jobs}"],
        chromium, environment)
    run(gclient, ["runhooks"], chromium, environment)
    run(sys.executable, [str(repo / "tools/windows_build.py"),
                        "--chromium-src", str(source), "--prepare", "--cpu", cpu,
                        "--jobs", str(jobs), "--artifacts", str(artifacts),
                        "--report", str(artifacts / "build-report.json")],
        repo, environment)


def main(arguments: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-base", type=Path, default=Path(r"C:\tb"))
    parser.add_argument("--artifacts", type=Path, default=Path("artifacts/windows"))
    parser.add_argument("--cpu", choices=CPU_PROFILES, default="avx")
    parser.add_argument("--jobs", type=int, default=4)
    options = parser.parse_args(arguments)
    if not 1 <= options.jobs <= 128:
        parser.error("--jobs must be between 1 and 128")
    if platform.system() != "Windows" or platform.machine().lower() not in ("amd64", "x86_64"):
        raise BuildError("The installer build requires a native Windows x64 runner")
    repo = Path(__file__).resolve().parents[1]
    base = options.build_base.resolve()
    artifacts = options.artifacts.resolve()
    validate_paths(base, artifacts)
    for path in (base, repo):
        if re.search(r'[\s%!&|<>^"\r\n]', str(path)):
            raise BuildError("Build and repository paths must have no spaces or cmd metacharacters")
    for tool in ("git", "cl.exe"):
        if not shutil.which(tool):
            raise BuildError(f"Missing {tool}; initialize Visual Studio x64 developer environment")
    depot_revision = validate_revision(
        (repo / "depot_tools/DEPOT_TOOLS_REVISION").read_text(encoding="utf-8").strip())
    base.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(base).free
    if free < MIN_START_BYTES:
        raise BuildError(f"At least 300 GiB free before checkout required; available {free / 1024**3:.1f} GiB")
    artifacts.mkdir(parents=True, exist_ok=True)
    record = {"chromium_version": VERSION, "depot_tools_revision": depot_revision,
              "cpu": options.cpu, "jobs": options.jobs, "completed": False}
    try:
        with tempfile.TemporaryDirectory(prefix="thorium-", dir=base) as temporary:
            build(repo, Path(temporary), artifacts, options.cpu, options.jobs, depot_revision)
        record["completed"] = True
    except (BuildError, OSError) as error:
        record["error"] = str(error)
        raise
    finally:
        (artifacts / "ci-build.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (BuildError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
