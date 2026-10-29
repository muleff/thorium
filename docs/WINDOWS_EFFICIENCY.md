# Windows efficiency changes and build guide

## What changed

- Windows defaults to Chromium's Memory Saver, with its existing medium mode.
  Eligible inactive tabs can be discarded and reload on return. Chromium's tab
  protections, exception lists and policy remain responsible for eligibility.
  Explicit existing user choices take precedence. Configure this at
  `chrome://settings/performance`.
- Windows tab hover cards do not capture screenshot previews by default. Textual
  cards and memory usage information remain. The existing Appearance setting can
  enable images again; existing explicit preferences are preserved.
- All Windows GN profiles resolve the downloaded, revision-matched Chromium PGO
  profile automatically instead of referencing `/home/alex/...`.
- A build driver checks source version, modified sources, matching CPU variant,
  disk space, tools and PGO; builds the browser, installer and relevant native
  tests; then records artifact SHA-256 checksums and source hashes.
- A dependency-free Node.js probe collects the actual GPU renderer, individual
  feature statuses, clean-profile startup, frame intervals, process CPU time and
  browser process-memory samples. It rejects software/headless results as hardware
  performance evidence unless explicitly requested for diagnostic use.

The existing ANGLE/D3D compositor, hardware video paths, driver blocklist and
workarounds, GPU sandbox, software fallback, site isolation, PDF, extensions,
WebRTC, accessibility, downloads and normal browser functions are retained.
Background extension mode already defaulted off in upstream Thorium.

## Why this differs from WaGo

WaGo is a Go application with a native Gio UI. It talks to WhatsApp's protocol
directly and has no HTML/CSS/JavaScript engine. GPU use alone cannot make a full
browser have the same memory footprint. Its transferable principles are avoiding
unneeded captures, drawing on demand, controlling retained resources and measuring
actual use. Working-set trimming was deliberately not copied: it can page useful
memory out and make returning to a tab slower.

These are resource-oriented default changes, not a demonstrated speedup. No
percentage, RAM target or performance parity with WaGo has been established.

## Build requirements

Use Windows 10/11 x64, Visual Studio 2022 with Desktop C++, MFC/ATL, Windows SDK
22621 and debugging tools, depot_tools, Python 3.10+, and at least 100 GiB free.
32 GiB RAM and a fast SSD are advisable for an optimized full-browser link.
Use an x64 Native Tools terminal with depot_tools on PATH.

This overlay is pinned to Chromium **138.0.7204.306**. Follow
[BUILDING_WIN.md](BUILDING_WIN.md) for fetching Chromium, depot_tools and the
matching dependencies. A Thorium clone alone is not the Chromium tree. This work
does not update the engine version or address later upstream security releases;
do not treat this historical source pin as a current browser release.

Initialize the pinned Thorium submodules:

```bat
git submodule update --init --recursive
```

After the Chromium tree has been checked out, synced and its hooks run at the
pinned tag, run this from the modified Thorium directory. `--prepare` applies
Thorium's existing setup script to that clean Chromium tree and downloads PGO.
Do not rerun setup on an already patched tree; existing upstream patches are not
idempotent. Use a dedicated checkout, since the upstream version/trunk scripts
include cleaning operations.

```bat
python tools\windows_build.py --chromium-src C:\src\chromium\src --cpu avx --prepare --jobs 4 --report validation\windows-build.json
```

For a previously prepared matching tree, omit `--prepare`:

```bat
python tools\windows_build.py --chromium-src C:\src\chromium\src --cpu avx --jobs 4 --report validation\windows-build.json
```

For validation alone append `--check-only`. Check-only runs no build or setup
commands. If using `--prepare --check-only`, it checks preparation prerequisites,
not the state after setup.

Choose one matching CPU profile; the preparation flag and GN args are paired:

| `--cpu` | Required CPU | Installer output |
| --- | --- | --- |
| `sse3` | x64 + SSE3 | `thorium_SSE3_mini_installer.exe` |
| `sse4` | x64 + SSE4.1 | `thorium_SSE4_mini_installer.exe` |
| `avx` | x64 + AVX | `thorium_AVX_mini_installer.exe` |
| `avx2` | x64 + AVX2 | `thorium_AVX2_mini_installer.exe` |

Do not change CPU profiles on an already patched tree. Prepare a clean pinned
Chromium tree for the new variant. Switching only args.gn can leave mismatched
source or Rust settings. GPU support does not depend on selecting AVX2.

The driver keeps PGO, ThinLTO, V8's optimized tiers, proprietary codecs and the
existing essential components. It compiles `thorium_all`, `setup`, `mini_installer`
and `components_unittests`, and runs `MemorySaverMode*` native tests. Successful
artifacts go into `artifacts/windows/`, together with `SHA256SUMS` and a source
and PGO hash manifest. The runnable build is `out/thorium/thorium.exe` with its
generated supporting files. A standalone copy of that EXE alone is insufficient;
use the generated self-contained installer to distribute the browser.

## GitHub Actions

The [Validate build tools](../.github/workflows/validate.yml) workflow runs the
Python and JavaScript helper suites on hosted Linux and Windows runners on every
push and pull request. This does not compile Chromium.

The [Build Windows installer](../.github/workflows/windows-build.yml) workflow
performs the native build. It deliberately runs only through `workflow_dispatch`:
pull requests cannot schedule arbitrary code on the self-hosted build machine.

1. In `muleff/thorium`, open **Settings → Actions → Runners → New self-hosted
   runner** and register a dedicated **Windows x64** machine. Add the custom label
   **`thorium-build`**, in addition to the default `self-hosted`, `Windows`, `X64`
   labels. No runner was registered when this workflow was added.
2. Install Git for Windows, PowerShell 7 (`pwsh`), and Visual Studio 2022 Desktop
   C++ with MFC/ATL. Install Windows SDK **10.0.22621.0** and its x64 Debugging
   Tools. Allow HTTPS access to GitHub, Chromium Git, CIPD and Google Storage.
   Use a dedicated runner account with no unrelated credentials.
3. Provide **300 GiB free before checkout**, on NTFS, and preferably 32 GiB RAM
   or more. The existing driver also requires 100 GiB free after dependency setup.
   The default disposable build location is `C:\tb`. To use another volume, set
   `THORIUM_BUILD_BASE` in the runner service environment and restart the runner.
   Build and repository paths must contain no spaces or cmd metacharacters.
4. Open **Actions → Build Windows installer → Run workflow**, choose `main`, a
   matching CPU profile, and the number of compiler jobs. The runner itself must
   support that instruction set to execute native tests. The default is AVX and
   four jobs; older computers may need SSE3. The job initializes the Visual Studio
   environment, fetches pinned depot_tools and Chromium 138.0.7204.306, applies the
   pinned JXL dependency overlay before sync, runs hooks, then builds this fork.
5. After success, download the `thorium-windows-x64-...` artifact from that run.
   It contains the installer EXE, `SHA256SUMS`, `build-manifest.json`,
   `build-report.json`, `native-tests.json` and `ci-build.json`. The driver rejects
   a native run that skips or omits any of the four new regression tests.

Check logs and the failure artifact if the build fails. Dependency and compiler
output is retained in Actions logs. Disposable source and output trees are cleaned
when the Python process exits normally, including handled build errors. A forced
runner shutdown can leave `C:\tb\thorium-*` directories; remove those only after
confirming no build is using them. Build artifacts remain outside that directory.
Every invocation uses a fresh source tree; changing CPU profiles never repatches
an existing tree. Native builds may take many hours; the workflow permits 72 hours.

Standard hosted Windows runners advertise only 14 GB of storage, so they cannot
satisfy this build. Registering the runner above is required before dispatch; with
no matching runner, GitHub queues the job. This workflow does not provision paid
machines, configure signing, publish a release, or claim hardware GPU benchmarks.

## Measure on real Windows hardware

Use Node.js 22.4+ with a global WebSocket implementation (Node.js 24 recommended):

```bat
node tools\gpu_probe.mjs --browser C:\src\chromium\src\out\thorium\thorium.exe --output validation\gpu-candidate.json --frames 180
```

The probe launches its own temporary profile and loopback-only debugging endpoint,
then closes the browser and deletes that profile. Keep its window visible and do
not interact with it during the workload. It never opens your normal profile.
On Windows, an early parent-process crash prevents reliable verification of
descendant cleanup; this is reported as a cleanup warning.

Inspect `gpu.featureStatus` for compositing, rasterization, WebGL and video decode;
not every individual path is necessarily hardware accelerated on every driver.
`hardwarePerformanceEligible` means the browser reports an accelerated compositor,
the workload renderer is not a known software adapter, and the test was headed.
It is not a guarantee that every rendering feature uses hardware.

For diagnostic software/headless runs only, add `--headless --allow-software`.
These runs cannot establish hardware GPU performance. The probe does not time GPU
passes directly. Frame intervals measure the browser's complete foreground pacing,
including CPU, driver and presentation effects.

Memory baseline is captured after attaching to the existing blank tab; samples
are captured every 500 ms during the workload; a final snapshot is explicitly
post-teardown. Summed RSS can double-count shared pages, and sampled peak is a
lower bound. The sampler itself adds overhead. Compare only like-for-like runs.

Compare unmodified Thorium and the modified build on the same machine with the
same CPU variant, window, display refresh rate and driver. Run at least five times
per build in alternating order and report median and p95 frame intervals plus
the spread across runs. The synthetic workload tests foreground rendering; it
does not validate Memory Saver's inactive-tab reclamation. For that, separately
use identical real tabs, wait for eligible tab discard and inspect
`chrome://discards` and `chrome://settings/performance`. Exercise audio, pinned
tabs, capture, unsaved forms and configured exceptions before release.

## Verification performed in this environment

```sh
python3 -m unittest discover -s tests -v
node --test tests/gpu_probe.test.mjs
python3 -m py_compile tools/windows_build.py
node --check tools/gpu_probe.mjs
git diff --check
```

The host-side suites test build validation/packaging and real loopback WebSocket
transport. They are not a Chromium build, a native preference test run or a GPU
benchmark. See [TASK_TRACKER.md](../TASK_TRACKER.md) and `validation/` for the
actual available checks and outstanding infrastructure blockers.
