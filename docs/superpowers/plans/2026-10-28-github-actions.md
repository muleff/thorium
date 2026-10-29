# GitHub publication and Windows CI

Goal: publish the complete modified Thorium overlay to muleff/thorium, preserving
the existing main commit, and provide a real Windows installer build workflow.

The standard hosted Windows runner has insufficient storage for Chromium.
Use hosted Linux and Windows runners for helper tests and a manually dispatched
self-hosted Windows x64 runner, labeled `thorium-build`, for the full build.
Do not provision paid infrastructure or run untrusted pull requests there.

- [x] Inspect target repository, permissions, source changes and build constraints.
- [x] Add tests for safe, pinned Chromium/depot_tools preparation and JXL DEPS.
- [x] Implement isolated checkout, dependency hooks and existing build-driver use.
- [x] Add hosted validation and manual Windows build workflows with artifacts.
- [x] Document runner requirements, build dispatch and installer retrieval.
- [x] Run helper suites, syntax and workflow checks; scan changes for credentials.
- [ ] Commit all sources with muleff attribution and push without force.
- [ ] Verify remote files and hosted workflow results.

Preserve Chromium 138.0.7204.306, the pinned JXL submodule and PGO resolution.
The build must run native MemorySaverMode tests before publishing an installer,
and record SHA-256 checksums. No credential belongs in source or workflow YAML.
Prepared source trees must be disposable and never reuse or reset user checkouts.
No Windows runner is currently registered, so creating the build workflow does
not by itself produce an EXE. Keep that infrastructure blocker explicit.
