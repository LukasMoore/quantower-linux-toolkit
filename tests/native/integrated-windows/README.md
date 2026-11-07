# Original integrated-window fixtures

These MIT fixtures exercise original windows and controls. They contain no vendor
application code, UI resources, account state or trading actions. Keep generated
binaries, prefixes, reports and captures outside the source tree in private paths.
The separate [WPF/CefSharp guide](WPF-CEF.md) describes the managed fixture.

## Compile the Win32 fixture

`window-probe.c` creates an ordinary top-level window, an independent borderless
layered window, child edit/button controls, an owned popup, a menu and an actual
common-controls tracking tooltip. The layered window has a labeled client header
that returns `HTCAPTION` only inside that exact own window's header; default resize
edge/corner hit results are preserved. Its three awareness variants select the fixture's own thread
context before window creation: `0` unaware, `1` system aware, `2` PerMonitorV2.
API acceptance and actual window awareness/DPI are reported separately. No vendor
process's awareness is changed.

The compile-only driver requires an existing x86-64 MinGW C compiler. It refuses
an existing output directory, records source/binary hashes and compiler flags, and
does not run Wine, download dependencies or touch any prefix:

```bash
python3 tests/native/integrated-windows/build.py \
  --output-dir /absolute/private/new-native-build
```

Use `--compiler /absolute/path/to/x86_64-w64-mingw32-gcc` when necessary. A successful
build establishes compilation only.

## Disposable synthetic display

`run-container.py` requires a non-root, explicitly opted-in, networkless container
with only the loopback interface. It starts its own Xvfb, creates a NEW prefix
under `/test/results`, and uses only these explicit read-only runtime inputs:

| Container path | Required input |
| --- | --- |
| `/runtime/wine` | Exact candidate Wine installation with `bin/wine`, `bin/wineserver` and `lib/wine` |
| `/runtime/x11` | Matched private Xlib tree with `lib/` and `share/X11/locale/` |
| `/runtime/deps` | Wine's matching userspace dependency library closure |
| `/probe` | New native build directory containing `window-probe.exe` |
| `/fixture` | This original fixture source directory |
| `/test` | New private writable result directory |

Prepare a local container image with Python 3 and Xvfb already installed. Verify
the complete selected runtime/component hashes before running; the driver's own
reported binary and `win32u.so` hashes are not a replacement for full verification.
Do not mount an application prefix, settings, live ledger or proprietary resources.
For example, replace every input path and the image name before running:

```bash
docker run --rm --network none --user "$(id -u):$(id -g)" \
  -e QTK_SYNTHETIC_DATAONLY=1 -e HOME=/tmp \
  --mount type=bind,source=/absolute/candidate/wine,target=/runtime/wine,readonly \
  --mount type=bind,source=/absolute/candidate/private-x11,target=/runtime/x11,readonly \
  --mount type=bind,source=/absolute/candidate/dependencies,target=/runtime/deps,readonly \
  --mount type=bind,source=/absolute/private/new-native-build,target=/probe,readonly \
  --mount type=bind,source=/absolute/source/tests/native/integrated-windows,target=/fixture,readonly \
  --mount type=bind,source=/absolute/private/new-test-directory,target=/test \
  local-fixture-image python3 /fixture/run-container.py
```

The driver sets explicit builtin X11/disabled Wayland overrides. It tests 96 and
192 prefix DPI, waiting for its own Wine server between DPI changes, and runs each
of the three fixture awareness variants. It records the exact fixture PID/birth,
canonical executable and mapped display driver, rather than borrowing backend
evidence from another prefix helper. Results and raw output remain in the private
result directory. A final unreachable local `DISPLAY` case must fail window
creation and report that the Wayland module was not loaded. This does not replace
the launcher's current-endpoint/authentication preflight. A failure is retained;
the driver has no alternate backend or
automatic restart path. A surviving process or timeout requires explicit diagnosis.

The fixture checks monitor/workarea extents and intersections, one primary monitor,
caller-owned desktop membership, own window identity, client/screen coordinate
round trips, programmatic placement, minimize/restore, child focus, popup ownership
and three cycles of programmatic button callbacks. The popup owner is re-enabled
before popup destruction, following [the Win32 lifecycle contract](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-enablewindow).
The tooltip uses `TTF_TRACK | TTF_ABSOLUTE`, an explicit nonactivating style and
checks visibility, ownership, target workarea/monitor and unchanged focus/activation.
Its requested screen coordinates, actual window bounds and client origin are
reported separately; see [tracking-position semantics](https://learn.microsoft.com/en-us/windows/win32/controls/ttm-trackposition).
Negative monitor origins are
valid. Geometry records label their caller-context Win32 units. The simple popup
does not reproduce a full application's dialog lifecycle.

Bounded diagnostics preserve requested, immediate, original 100-ms-pump and
post-lifecycle placement rectangles, plus the own window's X11 ID. Up to 128 own
window-position events and 256 own focus/activation events are recorded, with
dropped-record counters. These records diagnose failures; they do not relax exact
placement checks or replace physical input. They are fixture-only code, not vendor
startup hooks or production observers.

There is **no window manager** on this synthetic Xvfb display. Programmatic focus,
`BM_CLICK`, `ShowWindow` and `SetWindowPos` checks do not establish host focus policy,
physical pointer/keyboard delivery, decoration, taskbar/Alt-Tab recovery or physical
movement across monitors. A single synthetic screen does not prove three-monitor
topology. Logs, successful API calls and driver mappings do not prove final composed
pixels, legibility, popup visibility or long-term GPU stability.

## Signed tracking-tooltip coordinate regression

The separate [tooltip-coordinate fixture](tooltip-coordinates/README.md) isolates
`TTM_TRACKPOSITION` decoding using original own controls and tooltips. It exercises
common-controls v5 and manifest-selected v6, negative x/y and nonnegative controls,
at 96 and 192 prefix DPI. Compare the original runtime and a freshly rebuilt control
against the candidate; verify the mapped common-controls module hash for every run.
A successful build or a tooltip appearing somewhere is insufficient.

Local Wine 11.20 patch
`0007-comctl32-signed-tracking-tooltip-coordinates.patch` changes only the two
tracking-coordinate assignments: cast packed `LOWORD`/`HIWORD` through `SHORT`
before widening to `INT`. This preserves negative screen coordinates under the
signed 16-bit message contract and retains existing tooltip layout, clipping and
activation rules. Microsoft documents the packed [screen-coordinate message](https://learn.microsoft.com/en-us/windows/win32/controls/ttm-trackposition)
and signed [x](https://learn.microsoft.com/en-us/windows/win32/api/windowsx/nf-windowsx-get_x_lparam)/[y](https://learn.microsoft.com/en-us/windows/win32/api/windowsx/nf-windowsx-get_y_lparam)
extraction. Requested screen points, actual client origins and outer window bounds
remain distinct assertions; a negative point outside a single synthetic monitor can
legitimately clip to that monitor's workarea.

The regression does not prove actual host monitor placement, physical input,
application tooltip behavior, broader balloon-tooltip rules or GPU stability.
Retain every existing Wine patch and runtime dependency, keep results private and
follow stopped-prefix/lock/backup/hash guards for any module deployment. This local
correction is not upstream-accepted. New source builds include it; previously
published 0.2.0 archives remain unchanged. It is not a GPU-lockup fix.

## Controlled host verification

An optional second `interactive` argument to the native executable keeps only its
own windows available for a bounded physical-input session. It records final own
geometry and does not assert that physical actions occurred. Run it on the host only
through a separately reviewed disposable-prefix harness with exact process/birth,
prefix/token, HWND and desktop-membership guards. Do not reuse helpers pinned to a
named virtual desktop or send broad desktop input. Never clone a running prefix.

Complete the actual host/window actions and application acceptance described in
[the integrated-window guide](../../../docs/INTEGRATED-WINDOWS.md). Preserve refused
or failed trials as inconclusive evidence. Stop further test actions on a GPU fault
or desktop stall; preserve evidence and attempt normal close only if responsive.
Do not stop host display services, force-kill an application Starter, change host
drivers/monitors, automatically switch backend or restart after failure.

## Minimized GDI preview regression

The [minimized-preview fixture](minimized-preview/README.md) supplies an original
GDI drawable/shape differential and a portable actual-callback test driver for
opt-in Wine patch `0009`. It covers default behavior, retained minimized pixels,
deferred shape replay, restoration and resize without vendor resources. Generated
binaries, prefixes and captures remain outside the source tree.
