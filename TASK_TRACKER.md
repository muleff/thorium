# GPU efficiency work

## GitHub publication and Actions

| Item | State | Evidence / next action |
| --- | --- | --- |
| Inspect muleff/thorium and authenticated write access | done | Main contains only README; GitHub account muleff has admin/push access |
| Prepare pinned Windows build environment automatically | done | Disposable Chromium checkout, matching JXL DEPS and pinned depot_tools; six preparation regressions pass |
| Add hosted helper validation and manual installer workflow | done | Linux/Windows helper matrix; native installer requires a Windows x64 runner labeled thorium-build; actionlint validation |
| Check native test execution before artifact publication | done | Reject missing, skipped, failed or empty native test results; build thorium_all release prerequisites |
| Publish complete modified overlay to main without force | done | Commit 23e182f; remote tree matches local; original target commit preserved; supplied author identity; credential scan clean |
| Verify remote source and Actions results | in progress | First hosted Linux run passed; Windows found an 8.3 path comparison in the test fixture; canonical expected path corrected for next run |
| Execute full Windows compilation | pending | BLOCKER: repository has zero registered self-hosted runners; standard hosted storage is insufficient |

## Scope and evidence

Requested: clone Alex313031/thorium and muleff/WaGo, improve Thorium's GPU use,
memory and responsiveness using WaGo as a reference, and compile a Windows EXE.

| Item | State | Evidence / next action |
| --- | --- | --- |
| Clone both repositories and record revisions | done | Thorium `91b29e10e3c934ae187d9e7106fc88db64560eea`; WaGo `957b47d1dad40ba6189612ff48275ebd0fe2855e` |
| Audit architecture, GPU, memory, lifecycle and build | done | Native Gio versus Chromium overlay; see design document |
| Design safe browser changes and verification | done | Preserve normal GPU selection, browser security, media and preference overrides |
| Enable native Memory Saver by default | done | Windows-only preference default; complete pinned upstream overlay; four native regression cases added but native execution blocked |
| Avoid default tab-hover screenshot capture | done | Windows-only default; text and memory hover information and user choice retained |
| Portable Windows build configuration and artifact validation | done | All Windows profiles use GN PGO resolver; driver validates source, CPU, tools, disk, installer variant and checksums; Windows execution blocked |
| Hardware GPU / process-memory measurement tool | done | Actual CDP GPU status, during-workload memory, frame latency, startup; software detection; browser integration and GPU measurements blocked |
| Run available tests, syntax checks and source diff review | done | 19 Python + 9 JavaScript tests pass locally; syntax and actionlint checks pass; logs in validation/; hosted Windows rerun pending |
| Compile modified Windows browser and installer | pending | BLOCKER: Linux host, ~25 GB free, no full Chromium checkout, GN or Windows SDK. Upstream requires >=100 GB. Do not substitute a launcher or stock EXE. |
| Package modified source and instructions | done | Downloadable change kit includes exact patch, full modified files, license, instructions and validation evidence |

## Build blocker

The Thorium repository is a source overlay, not a complete Chromium checkout.
Its pinned Chromium version is 138.0.7204.306. Neither native Windows compilation
nor Linux cross-compilation can run without the full tree and Windows toolchain.
Host inventory: 9.7 GiB RAM, 9 CPUs, 32 GB total filesystem, about 25 GB free.
No `/dev/dri` physical GPU device is exposed. These are infrastructure limits,
not compiler failures; repeating a full checkout will not resolve them.

## Verification and remaining work

`validation/verification.json` records the exact scope of successful host checks.
No browser EXE has been produced, no native C++ suite has run, and no measured
GPU/RAM/speed improvement is claimed. The browser probe could not be integration
tested: no browser was preinstalled, and a Playwright browser download returned a
truncated/invalid ZIP. Do not rerun that same download without new information.

The source defaults and native tests require a real Windows build. Then run the
documented hardware and inactive-tab cases, including media/capture, explicit
disable, policy, and discard exceptions. The current host cannot close those
items. Windows crash-descendant cleanup is reported as unverifiable on an early
parent exit; normal cleanup uses Browser.close with a process-tree fallback.

Independent review caught and corrected CPU-specific installer selection,
Windows batch quoting, a blank-page readiness race, baseline tab-count mismatch
and missing during-workload memory sampling. Transport tests cover concurrent
responses, errors, timeout, closure and malformed responses.
