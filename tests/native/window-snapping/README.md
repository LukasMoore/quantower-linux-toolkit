# Synthetic window-snapping regression probes

These original MIT fixtures create only their own Win32 windows. They contain no
Quantower code, assets, settings, market data or external-window hooks. They test
Wine's fallback move/size loop when an application changes `WM_MOVING` or
`WM_SIZING` rectangles to implement snapping.

Run them only in the disposable container layout below, with a complete runtime
under evaluation. The driver refuses root, an absent explicit opt-in, external
network interfaces, an existing result directory, invalid input/capture and wrong
geometry. It creates a new Xvfb server and a new Wine prefix for each invocation.
It does not connect to a host display, reuse a prefix, start the application,
restart on failure or issue a global Wine-server kill. Container exit tears down
its remaining test processes. Mount only the runtime trees, probes, driver and a
new private results directory; never mount an app installation, prefix, home
folder, display socket, Docker socket or credentials.

## Compile without executing

An x86-64 MinGW C compiler is required. From the repository root:

```bash
QTK_SNAPPING_PROBES=/absolute/path/to/new-probe-directory
python3 tests/native/window-snapping/build.py --output-dir "$QTK_SNAPPING_PROBES"
```

The builder refuses an existing output directory and compiles four probes with
warnings as errors and deterministic PE timestamps. It records compiler, flags,
source hashes and binary hashes in `build.json`; it never executes Wine or probes.
The checked-in `.c` sources are byte-identical to the successful immutable fixture
sources used for the recorded validation. Compiled executables and generated
results remain outside the Git checkout. MinGW/GCC startup runtime terms apply to
compiled probes; preserve the applicable notices if redistributing them.

## Run in a new networkless container

The repository's `packaging/Dockerfile.smoke-test` supplies Debian 13, Python and
Xvfb. Build that image once before the networkless test:

```bash
docker build -f packaging/Dockerfile.smoke-test -t qtk-snapping-test .
```

Set these to absolute paths. Wine must be a complete prepared runtime tree with its
required libraries; Xlib must contain `lib/` and `share/X11/`. A Wine build alone
may need the library collection described in [the bundle guide](../../../docs/RUNTIME-BUNDLE.md).
Select either an original pinned Wine 11.20 runtime or one rebuilt with the snapping
patch. Keep them in separate directories; never replace a loaded runtime.

```bash
QTK_SNAPPING_SOURCE="$(pwd)/tests/native/window-snapping"
QTK_SNAPPING_PROBES=/absolute/path/to/new-probe-directory
QTK_WINE_TREE=/absolute/path/to/prepared-wine-tree
QTK_X11_TREE=/absolute/path/to/prepared-x11-tree
QTK_SNAPPING_RESULTS=/absolute/path/to/new-private-results-directory
mkdir -m 700 "$QTK_SNAPPING_RESULTS"

docker run --rm --init --network none --user "$(id -u):$(id -g)" \
  --cpus 2 --memory 2g -e QTK_SYNTHETIC_DATAONLY=1 \
  --mount "type=bind,src=$QTK_SNAPPING_SOURCE,dst=/driver,readonly" \
  --mount "type=bind,src=$QTK_SNAPPING_PROBES,dst=/probe,readonly" \
  --mount "type=bind,src=$QTK_WINE_TREE,dst=/runtime/wine,readonly" \
  --mount "type=bind,src=$QTK_X11_TREE,dst=/runtime/x11,readonly" \
  --mount "type=bind,src=$QTK_SNAPPING_RESULTS,dst=/test" \
  qtk-snapping-test python3 -B /driver/run-container.py \
  --suite main --expect candidate --label candidate-main
```

Use `--expect baseline` for the unmodified pinned fallback loop and a different
label/results directory. The expected mode is explicit; names do not select it.
Repeat the container command with `--suite snap-hold` and `--suite supplemental`,
using new labels. Each command creates its own prefix and display; binaries are
hashed before and after execution. No host X11 socket or GPU is needed.

The image's standard user is UID/GID 1000. Match that identity when using the
provided image, or build an image whose writable container home belongs to your
selected non-root UID. The driver preserves that existing container login home;
Wine requires it. It does not import an existing user prefix. Older Wine trees
that have a separate 32-bit Unix runtime can use `--legacy-wine64`, but require a
matching 32-bit Linux loader/libraries in their test image. The modern WoW64 recipe
in this repository does not have that Linux multilib requirement.

`summary.json` includes every attempted case and its actual result. A zero exit
requires valid delivery and all mode-specific geometry gates. Timeouts or setup
failures are failures, not passes; retain their output. This driver is intentionally
not part of the ordinary Python CI's live-process execution. CI tests its refusal
and evidence-verification rules without starting Wine.

## Suites and exact gates

- **main: 61 cases.** Eight resize directions plus moving; slow 1 px and fast
  250 px motions, reversing through a magnet, Escape cancellation, no snapping and
  exact preexisting minimum/maximum boundary behavior. The original Wine 11.20
  baseline must reproduce exactly 18 sticky cases and pass the other 43. The
  candidate must pass all 61. Valid delivery and nonzero capture/entry/exit checks
  are required independently of the rectangle result.
- **snap-hold: 9 cases.** Each resize direction and moving stop the pointer inside
  the magnet range (190 px travel, 200 px snapped endpoint). All cases must snap
  and finish at the exact expected rectangle. This prevents “fixing” the trap by
  silently disabling snapping.
- **supplemental: 38 accepted cases.** Child screen/parent coordinate conversion,
  full versus outline dragging, move/right/bottom-right pointer motions, reverse
  and Escape, bounded keyboard sizing and border movement. Outline geometry must
  remain unchanged until commit. The baseline reproduces exactly 14 specified
  sticky failures; the candidate passes every accepted case.

The supplemental suite uses 36 unchanged `child-keyboard-v3.c` cases and two bounded
`child-keyboard-v4.c` controls. Three historical multi-key child/full border-drag
attempts per runtime were invalid in both variants: Wine exited after one 8 px key
step when it dispatched paint/size work without a held mouse button. Those trials
were retained privately, excluded from success counts, and replaced by bounded
one-key commit/Escape checks. The suite makes no multi-key child/full reliability
claim. Do not weaken injection guards or count these historical invalid trials as
correctness passes.

All recorded native geometry validation was at 96 DPI. It establishes the tested
input/geometry behavior, not higher-DPI support, visible FPS, application market-data
latency or production suitability. The local Wine correction is not an accepted
upstream release. Original toolkit code and these probes were largely AI-generated;
review them and independently test an isolated demo before relying on the toolkit.

Primary sources:

- [Microsoft WM_SIZING](https://learn.microsoft.com/en-us/windows/win32/winmsg/wm-sizing)
- [Microsoft WM_MOVING](https://learn.microsoft.com/en-us/windows/win32/winmsg/wm-moving)
- [Wine 11.20 move/size loop](https://github.com/wine-mirror/wine/blob/wine-11.20/dlls/win32u/defwnd.c)
- [Wine's SC_SIZE regression](https://github.com/wine-mirror/wine/blob/wine-11.20/dlls/user32/tests/win.c)
