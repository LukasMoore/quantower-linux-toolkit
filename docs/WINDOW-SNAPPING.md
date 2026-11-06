# Windows that stick after snapping

The current source recipe includes Wine patch
`0006-win32u-unsnapped-pointer-tracking.patch`. It corrects a move/resize tracking
problem in the pinned Wine 11.20 fallback loop. The patch is a local correction,
not an upstream-accepted Wine release.

The published v0.2.0 runtime archive and corresponding source companion retain
that release's original contents. They do not contain this later patch. Follow
[the native source build instructions](../README.md#build-and-inspect-the-native-patches)
to evaluate the current recipe in a new isolated installation.

## Cause and resulting behaviour

Windows permits an application to change the rectangle supplied through
[WM_SIZING](https://learn.microsoft.com/en-us/windows/win32/winmsg/wm-sizing) or
[WM_MOVING](https://learn.microsoft.com/en-us/windows/win32/winmsg/wm-moving).
An application can use this to align a window with another panel or a screen edge.

In the [pinned Wine loop](https://github.com/wine-mirror/wine/blob/wine-11.20/dlls/win32u/defwnd.c),
each pointer movement was added to the same rectangle that the callback could
snap. For example, a callback may snap an edge back when it is within 20 pixels
of another window. Repeated 1-pixel movements then keep proposing an edge just
1 pixel from that boundary, even after the pointer has moved far beyond it. A
single movement larger than the attraction range can escape, making resizing
feel dependent on how quickly the pointer moves.

The correction keeps the requested tracking rectangle separate from the
application-adjusted rectangle. Pointer deltas accumulate on the tracking
rectangle; the application still chooses the displayed snapped bounds. A slow
drag can therefore escape a magnet while an endpoint inside its range still snaps.
The change preserves existing pointer clamps, size constraints, coordinate
conversion and cancellation paths. It does not disable application snapping.

## Distinguish the application's layout modes

Quantower documents several different behaviours:

- [Standalone panels](https://help.quantower.com/quantower/general-settings/standalone-panels)
  remain movable and resizable, with nearby borders and dimensions that can snap.
- [Binds](https://help.quantower.com/quantower/general-settings/binds) combine panels
  into one outer window. Resize the whole Bind at its outer boundary or adjust
  individual proportions using its internal dividers.
- [Workspace Lock](https://help.quantower.com/quantower/general-settings/workspaces-binds-groups)
  prevents panel movement and resizing. A
  [Group](https://help.quantower.com/quantower/general-settings/group-of-panels)
  instead arranges panels as tabs in one window.

Check the intended layout mode before diagnosing a corner that cannot resize.
The Wine patch changes its tracking loop; it does not override Workspace Lock,
restructure Binds or alter signed application files.

## Validation and limits

Original synthetic 64-bit Win32 fixtures exercised only their own windows in
disposable prefixes with Xvfb at 96 DPI. The accepted candidate checks were:

| Coverage | Accepted cases |
| --- | ---: |
| Eight resize directions and moving, against one Wine runtime variant | 61 |
| Same main matrix against a second Wine runtime variant | 61 |
| Endpoints inside each magnet's attraction range | 9 |
| Child coordinates, outline dragging and bounded keyboard controls | 38 |
| Total | 169 |

The main matrices cover slow 1-pixel and fast movement, reversal through a magnet,
Escape cancellation, ordinary unsnapped movement and exact existing size limits.
Both original runtimes reproduced the expected sticky cases while the candidates
passed their geometry gates. Supplemental fixtures check parent coordinates and
that outline mode leaves window geometry unchanged until commit. Invalid historical
injection attempts were preserved and excluded from these accepted counts; no
claim is made for the unsupported multi-key child/full-window development trials.

The `window_resize_tracking` record in
[patches/validation.json](../patches/validation.json) records the later patch's source
preparation, module build and synthetic checks. The older top-level records there
and in `source-validation.json` retain their historical scope.

[The source fixtures and runner](../tests/native/window-snapping/README.md) allow
these cases to be repeated in isolated test containers. Require both valid input
and exact expected bounds. A completed process or a successful compile alone is
insufficient. Native fixtures require explicit execution and are separate from
normal source-only CI tests.

These are native input/geometry correctness checks, not application benchmarks or
frame-rate measurements. High DPI, 32-bit UI cases and other runtimes need their own validation.
A real application's physical corner-drag gesture remains unverified by the
initial automated integration attempt because the nested compositor did not retain
reliable cursor placement. Do not count that inconclusive attempt as a pass or
infer its cause from a successful cursor API return. Test the actual gesture
manually in an isolated demo setup on your own system.

## Installing and rolling back

Build into a new directory with the source recipe, then use the normal installer
to create a new test installation from the complete staged runtime. The installer
refuses to overwrite an existing target. No automatic updater for existing
installations is provided by this source change.

For manual maintenance of an existing installation, close the application normally,
verify that its prefix is inactive and acquire its launch lock before changing
runtime files. Preserve the matching original runtime and component manifest as
a coherent rollback set. Validate replacement hashes and matching Wine interfaces;
a module compiled for another runtime must not be assumed compatible. Restart
through the normal supervised launcher. Roll back only after another normal close
and verified inactive prefix. Restore the original runtime and reconcile its
component pins/provenance in the current manifest with the verified original state,
keeping unrelated later manifest and configuration entries. Verify the resulting
installation before restarting. Never force a live application shutdown to apply
or undo this patch.

The original local patch and regression tools were largely AI-generated. Review
them and test an isolated demo installation individually before relying on them.
Upstream Wine source retains its original authorship and LGPL terms.
