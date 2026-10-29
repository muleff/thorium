# GPU efficiency implementation plan

**Goal:** reduce avoidable Windows browser resource use and produce a verified
Windows build when the necessary build infrastructure exists.

**Architecture:** version-matched Chromium overlays retain the native GPU and
tab lifecycle. Separate dependency-free tools validate builds and collect actual
browser measurements.

**Tech stack:** Chromium C++/GN, Python 3.10+, Node.js 22+.

**Spec:** `docs/superpowers/specs/2026-10-28-gpu-efficiency-design.md`.

## Constraints and review focus

Preserve browser security, compatibility, explicit preferences and policy.
Exercise legacy preference migration, policy disable, missing/wrong source,
matching PGO, invalid paths, missing runtime files, CDP timeout/closure, software
GPU detection and cleanup. Never report a browser build from helper tests alone.

- [x] Add version-matched native preference regression tests and changed defaults.
  Existing migration tests retain their original disabled-default fixture; new
  tests exercise the real Windows default, migrations and explicit overrides.
- [x] Correct all Windows GN profile paths using the existing GN resolver.
- [x] Write failing host tests for build preflight, version matching, PGO,
  path handling, package validation and hashes; implement the Windows build driver.
- [x] Write failing CDP tests for response correlation, protocol errors, closure
  and deadline; implement hardware/memory/frame diagnostics with scoped cleanup.
- [x] Run host tests and syntax/diff checks.
- [ ] On a suitable Windows machine run GN, compile, native tests, mini_installer
  and GPU workloads. Blocked by missing host infrastructure.
- [ ] Record exact evidence and blockers in TASK_TRACKER.md; package source and
  patch without substituting a stock browser for the requested modified EXE.
