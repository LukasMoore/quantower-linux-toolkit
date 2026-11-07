# Original signed-tooltip coordinate regression

Wine 11.20's `TTM_TRACKPOSITION` handler reads two packed screen coordinates. Each
word is signed: negative X/Y positions are valid on a virtual desktop. Patch
`0007-comctl32-signed-tracking-tooltip-coordinates.patch` changes only their
extraction to sign-extend through `SHORT`, equivalent to `GET_X_LPARAM` and
`GET_Y_LPARAM`. Monitor choice, clipping, sizing and painting are preserved. This
local Wine correction retains Wine's LGPL terms and has not been accepted upstream.

The MIT fixture is original source with its own owner and tracking tooltip. Its
nine ordered points cover three nonnegative controls and six negative-X/Y cases.
It requests its own PMv2 context, reports actual DPI, and compares client-screen
origins with independently derived expectations. X coordinates left of the sole
Xvfb workarea are expected to retain Wine's existing workarea clipping; negative Y
remains an exact signed-coordinate check. This deliberately tests decoding rather
than simulating physical monitors or adding a global geometry clamp.

## Compile without execution

Use existing x86-64 MinGW C and resource compilers. The driver verifies the exact
original C, manifest and resource-source hashes and refuses an existing output:

```bash
python3 tests/native/integrated-windows/tooltip-coordinates/build.py \
  --output-dir /absolute/private/new-tooltip-build
```

The v5 binary uses the default common control. The v6 binary has an original
manifest requesting common-controls version 6. Both are built from the same C
source. A separate original `monitor-workareas.c` companion calls
`EnumDisplayMonitors`/`GetMonitorInfoW` in the same awareness context to classify
requests against actual active workareas; it creates no windows and changes no
display geometry. Compiler flags, input hashes and output hashes are in `build.json`;
`compile.log` retains compiler diagnostics. No dependencies are downloaded, Wine
executed or prefix modified. Generated files remain private and outside Git.

## Run a disposable matrix

Use the same explicit non-root, loopback-only networkless container and runtime
mounts described in [the parent guide](../README.md). Mount this directory at
`/fixture`, the new build at `/probe`, the selected runtime at `/runtime/wine`,
matched private Xlib at `/runtime/x11`, dependency libraries at `/runtime/deps`, and
a NEW private writable directory at `/test`. Verify the complete runtime first.
Do not mount or copy the application prefix or its state. The container must have
Python 3 and Xvfb already installed.

Run the selected family against a deliberately selected original, independently
rebuilt control, or patched runtime. Supply the SHA256 of that family's exact DLL:

```bash
python3 /fixture/run-container.py --family v5 --expect candidate \
  --expected-comctl32-sha256 EXACT_64_HEX_DIGEST
```

Use `--family v6` for `comctl32_v6.dll`; v5 checks `comctl32.dll`. Each invocation
creates a new prefix and its own Xvfb, then tests both 96 and 192 DPI. `--expect
baseline` requires exactly the original six negative-coordinate decoding failures
and all three nonnegative passes at each DPI. `--expect candidate` requires every
coordinate case to pass. Those are explicit matrix expectations, not retries or
alternate backends.

The harness validates the exact request order, independently derived coordinates,
nonempty rectangles, result/exit agreement, source/binary identity and the mapped
DLL hash. Mapping observations require exact UID/prefix/session-token/process-birth
and canonical Wine executable evidence before and after sampling. Missing, changed
or ambiguous evidence fails the gate. Unexpected exit or timeout preserves failed
evidence; the harness never force-kills a survivor, changes backend or restarts it.
All reports and runtime output remain private under `/test/results`.

`coordinate_evidence_passed` reports the signed-decoding assertions and identity
evidence. It is separate from the stronger `passed` gate, which also requires
nonempty rectangles and requested points inside actual enumerated workareas.
The original single-screen Xvfb matrix has negative points outside every active
workarea. Its far-left points can collapse the rectangle to zero width through
Wine's unchanged clipping behavior. These failures remain visible and make the
stronger gate fail; a coordinate pass is not a visible-tooltip pass. The report
retains outside-workarea and collapsed-rectangle cases and leaves negative-monitor
acceptance false. Do not remove points or loosen the geometry checks to get a pass.

## Scope of the checks

These are synthetic own-tooltip checks on Xvfb **without a window manager**. They
do not establish host popup behavior, physical input, focus stability, composed
pixels, GPU stability or application panel acceptance. Test negative-origin host
monitor placement and actual application popups separately, with exact ownership
guards. Retain failed/refused trials. Existing Wine patches remain in the selected
runtime; the signed vendor application is outside this fixture and correction.

Fresh source builds include patch 0007. Published v0.2.0 runtime and source-companion
archives remain the original release and have not acquired the correction.

References: [tracking coordinates](https://learn.microsoft.com/en-us/windows/win32/controls/ttm-trackposition),
[signed X extraction](https://learn.microsoft.com/en-us/windows/win32/api/windowsx/nf-windowsx-get_x_lparam),
[signed Y extraction](https://learn.microsoft.com/en-us/windows/win32/api/windowsx/nf-windowsx-get_y_lparam),
[Wine's handler](https://github.com/wine-mirror/wine/blob/wine-11.20/dlls/comctl32/tooltips.c).
