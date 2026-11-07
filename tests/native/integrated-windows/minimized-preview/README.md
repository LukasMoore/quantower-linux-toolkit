# Original minimized GDI preview regression

Wine patch `0009` preserves a managed window's last normal GDI pixels and bounding
shape when an X11 compositor retains the minimized drawable for overview. Only
exact `QTK_PRESERVE_MINIMIZED_PREVIEW=1` enables it; unset, empty, `0` and other
values retain the original behavior. It is a local Wine LGPL modification, not an
upstream-accepted release. The surrounding original test scaffolding and GDI
fixture are MIT licensed; extracted Wine function bodies retain Wine's notices.

The patch does not change window ownership, taskbar policy, close handling or
application binaries. It preserves a static last frame, rather than updating a
minimized chart. It applies to buffered GDI surfaces, not every GPU client surface.
Cached eligibility avoids acquiring window/user locks while holding a surface
lock. Rejected flushes retain dirty bounds and defer shape replay until restore.

## Actual callback tests without a display

Prepare two separate copies of the pinned Wine 11.20 source: one with the existing
patches `0001` through `0007`, and one additionally containing `0009`. There is no
`0008` dependency. Use the source builder's prepare-only workflow in
[the native guide](../../../../native/README.txt). These tests pin the exact
baseline/candidate `dlls/winex11.drv/bitblt.c` hashes and require an existing GCC C
compiler and Python 3. They do not run Wine, connect to X11 or touch a prefix.

```bash
python3 tests/native/integrated-windows/minimized-preview/test-surface-policy.py \
  --baseline /absolute/private/baseline-source/dlls/winex11.drv/bitblt.c \
  --candidate /absolute/private/candidate-source/dlls/winex11.drv/bitblt.c \
  --output /absolute/private/new-callback-results
```

The driver compiles the actual `X11DRV_CreateWindowSurface` and
`x11drv_surface_flush` bodies with inert X/Win32 mocks. Each variant runs 4,096
cases covering every opt-in/window guard, repeated skipped flushes, deferred shape
replay, shared-memory/fallback submission, and direct alpha/copy paths. Canary
mocks reject native/window-data queries during flush. Simplified types and inert
mocks do not prove real concurrent lock behavior or compositor acceptance.

All generated C, native binaries, compile logs and `summary.json` go in the new
private output directory, outside the source tree. A changed input hash, compiler
failure, unexpected result or nonzero test exit fails the driver.

## Original GDI pixel and shape differential

`preview-fixture.cpp` creates and paints only its own window. It paints a labeled
chart-like pattern normally and a white iconic client when minimized. Six paused
phases cover normal → minimize → restore and resize → minimize → restore. A
separate observer compares exact drawable bytes and XShape bounding rectangles;
API success alone is insufficient.

Compile into a new private directory using an existing x86-64 MinGW C++ compiler:

```bash
x86_64-w64-mingw32-g++ -DUNICODE -D_UNICODE -std=c++17 -O2 \
  -Wall -Wextra -Werror -municode -static-libgcc -static-libstdc++ \
  -Wl,--no-insert-timestamp \
  tests/native/integrated-windows/minimized-preview/preview-fixture.cpp \
  -o /absolute/private/new-preview-build/preview-fixture.exe -lgdi32 -luser32
```

`run-container.py` requires a non-root networkless container with Python 3, Xvfb,
Xlib and Xext already installed. Mount only verified runtime inputs and this
original fixture. Never mount an application prefix, live ledger, settings or
proprietary resources. The runtime dependencies and required 32-bit loader/libraries
must already be available in the image. For example, replace each input and image:

```bash
docker run --rm --network none --user "$(id -u):$(id -g)" \
  -e QTK_SYNTHETIC_DATAONLY=1 -e HOME=/tmp \
  --mount type=bind,source=/absolute/candidate/wine,target=/runtime/wine,readonly \
  --mount type=bind,source=/absolute/candidate/private-x11,target=/runtime/x11,readonly \
  --mount type=bind,source=/absolute/candidate/dependencies,target=/runtime/deps,readonly \
  --mount type=bind,source=/absolute/private/new-preview-build,target=/probe,readonly \
  --mount type=bind,source=/absolute/source/tests/native/integrated-windows/minimized-preview,target=/fixture,readonly \
  --mount type=bind,source=/absolute/private/new-preview-results,target=/evidence \
  local-fixture-image python3 /fixture/run-container.py candidate-enabled 1 preserved
```

The runner creates its own Xvfb and a new prefix. It authenticates the exact
fixture process by UID, prefix, session token and process birth, and checks the
mapped X11 driver hash around every pixel/shape capture. It captures no host
root/window pixels and contains no application actions or fallback restart.
Failures and timeouts remain failures; inspect retained evidence explicitly.

Run the original runtime with `baseline-unset unset broken`, then the candidate
with `candidate-unset unset broken`, `candidate-zero 0 broken` and
`candidate-enabled 1 preserved`, each in a separate disposable invocation and new
label. `broken` means both minimized snapshots must differ from their preceding
normal/resized pixels or shape; both restored snapshots must exactly match.
`preserved` requires exact pixels and shape across both minimize/restore cycles.

There is no window manager on this synthetic Xvfb display. The regression covers
GDI drawable/shape preservation and restore; it does not establish GNOME taskbar,
popup lifecycle, physical input, multi-monitor layout, GPU stability or trading
suitability. Keep captures, generated prefixes, binaries and machine-specific
reports private. Published prebuilt runtime/source-companion assets are unchanged;
use a coherent newly built runtime and the normal stopped-prefix deployment guards.
