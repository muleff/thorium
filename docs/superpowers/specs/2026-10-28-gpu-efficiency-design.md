# Thorium Windows resource efficiency

The deliverable is a modified full browser and a reproducible Windows build,
with lower unnecessary resource use while preserving normal web compatibility.
No measured speedup or memory parity with WaGo is claimed without hardware data.

## Audit and gap analysis

Thorium revision `91b29e10e3c934ae187d9e7106fc88db64560eea` overlays Chromium
`138.0.7204.306`. WaGo revision `957b47d1dad40ba6189612ff48275ebd0fe2855e`
uses a Go/Gio native interface and a direct WhatsApp protocol implementation.
There is no Chromium in WaGo to transplant. Its useful design principles are
bounded caches, work on demand, resource release, and measurement.

| Subsystem | Current state | Decision |
| --- | --- | --- |
| GPU compositing, rasterization, video | Chromium automatically selects compatible hardware | Retain ANGLE/D3D, driver workarounds, GPU sandbox and fallback |
| Browser process after window close | Background extension mode already defaults off | Preserve existing behavior |
| Inactive tabs | Memory Saver defaults disabled in pinned Chromium | Default to enabled on Windows, medium mode; retain policies, user choices, protected tabs and exceptions |
| Tab hover | Screenshot previews enabled | Default screenshots off on Windows; retain text and memory information and existing setting |
| Cache pressure | Blink already releases resources on pressure | Do not add forced working-set trimming or arbitrary cache/heap caps |
| Essential browser features | Extensions, PDF, media, WebRTC, accessibility, downloads, sync code | Preserve |
| Windows PGO | Developer-specific Linux absolute path in Windows GN files | Use Chromium's automatic matching-profile resolution |
| Verification | No dedicated GPU capture/measurement tool in this overlay | Add actual browser CDP hardware inspection and repeatable frame workload; identify software rendering |
| Windows build | Requires complete Chromium tree, depot tools and SDK | Add validated build/test/package driver with explicit preflight failures |

## Approaches

1. Replace Blink/V8 with a Gio app: destroys general browser compatibility and
   does not meet the request to modify Thorium.
2. Force flags, trim the working set or shrink caches blindly: can break drivers,
   cause page faults and regress foreground latency; unsupported by measurements.
3. Preserve Chromium's accelerated architecture, remove avoidable work, enable
   supported inactive-tab reclamation, and add measurement/build validation.
   This is the selected engineering approach under the user's instruction to
   proceed autonomously while keeping necessary functionality.

## Implementation boundaries

Only Windows preference defaults change. Existing explicit preferences and
enterprise policies retain precedence. Memory Saver may reload eligible inactive
tabs; sites can be exempted at `chrome://settings/performance`. Screenshots can be
enabled again in Settings. No preload/cache changes are justified by measurements.

The native C++ tests extend version-matched upstream tests already referenced by
Chromium's build. Host-side tests verify build validation and real CDP transport
behavior independently. A browser benchmark reports feature statuses, adapter,
frame latency and process memory when available; it must reject software results
as evidence of hardware performance. No timing threshold belongs in hosted CI.

## Infrastructure constraint

This Linux host has approximately 25 GB free, no physical GPU and no Windows SDK.
Thorium's Windows instructions require at least 100 GB and the MSVC/SDK toolchain.
A complete browser EXE and native tests remain blocked until an adequate build
machine is available. A launcher or upstream binary cannot satisfy that item.
