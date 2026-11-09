# Native window authority source tests

These optional tests compile actual functions extracted from two Wine source trees against original, inert Win32/X11 mocks. They cover the placement-state policy in patch 0010 and the caption delegation policy in patch 0011. They do not start Wine, connect to a display, send input, inspect processes, or operate an installed application.

**Known regression:** experimental native caption moving intercepts application-drawn fullscreen, minimize, maximize and close controls when they share `WM_NCLBUTTONDOWN/HTCAPTION` with draggable caption space. The candidate suite deliberately demonstrates this missing logical button callback. A passing suite therefore does **not** establish that custom titlebar controls work. The ordinary native `HTCLOSE`/minimize/maximize hit-code negative controls do not cover that custom-chrome behavior.

The default Python test suite only tests the extractor and path/argument guards with temporary synthetic files. It does not compile or execute native programs. The command below is an explicit opt-in to compilation and execution of mock-boundary test binaries.

## Prepare source trees

Use the pinned Wine source identity and patch order in the repository's `runtime-lock.json`, with the source preparation/build tooling documented under `docs/`. Prepare a candidate tree with the complete patchset through 0011. Make an independent baseline copy and reverse only 0011, then 0010, from that copy:

```bash
patch --directory="$BASELINE_SOURCE" --strip=1 --reverse --fuzz=0 < patches/wine/0011-native-caption-move.patch
patch --directory="$BASELINE_SOURCE" --strip=1 --reverse --fuzz=0 < patches/wine/0010-winex11-native-window-placement.patch
```

`BASELINE_SOURCE` and `CANDIDATE_SOURCE` must be separate source roots containing `dlls/user32/winproc.c` and `dlls/winex11.drv/window.c`. The baseline retains all earlier patches through 0009. Do not use installed runtime or prefix directories. Do not reverse patches in the candidate copy.

## Run the optional compiled tests

Python 3 and a C11 compiler such as GCC are required. Choose a fresh, nonexistent output path outside the repository and both source trees; its parent directory must already exist.

```bash
python3 tests/native/window-authority/run-tests.py \
  --baseline-source "$BASELINE_SOURCE" \
  --candidate-source "$CANDIDATE_SOURCE" \
  --output "$SCRATCH_PARENT/window-authority-results"
```

`--cc` can select another compiler executable. There are no source-path defaults and no application/prefix discovery. Existing output paths, dangling output symlinks, identical source roots, source-file symlinks and output paths inside the source/repository trees are refused. Generated C retains each Wine source's upstream copyright/license header; Wine-derived extracts remain under their upstream terms. Original mock headers, cases and the runner are covered by the repository MIT license.

The fresh output contains generated C, binaries, compiler/run logs and `report.json` with compiler identity, source/harness hashes and artifact hashes. Keep these local generated files out of commits. The report contains no baked-in runtime binary identity, private research path or application state.

## What the checks establish

- Baseline geometry can promote a window to EWMH fullscreen and request fullscreen-monitor indices; the opted-in candidate suppresses those requests while preserving incoming host fullscreen, maximize, minimize and geometry state. Pending/current divergence, withdrawn/iconic/locked states and replay paths are exercised.
- Default, malformed and excluded-role caption messages retain application dispatch; eligible caption down delegates through DefWindowProc under the existing DPI context. ANSI/Unicode, LastError, exact XID capability markers, unsupported-host blocking and maximized mouse move versus keyboard/resize boundaries are checked.
- Marker publication/removal helper behavior is executed with inert property mocks. Five lifecycle source-order checks supplement those helper tests; they do not simulate every complete lifecycle function.
- Optional numeric trace checks exercise cheap message gating, nested call/parent/depth identity, valid/invalid hit-code handling, LastError, the 1024-pair cap and 600-second cutoff.
- A synthetic custom caption callback receives the generic caption event in baseline and loses its logical button action in the candidate. This is the documented regression, deliberately retained in the checkpoint.

These mocks do not establish real X11 ABI compatibility, mapped-module identity, physical tiling behavior, vendor grouping behavior or application control preservation. A future custom-chrome control fix needs a faithful fixture that shares native `HTCAPTION` between buttons and drag space, plus separate application acceptance. No disposable display/application fixture is run by this harness or included in the default tests.
