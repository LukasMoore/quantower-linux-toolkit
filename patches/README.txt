Pinned upstream patch series

The sequence in runtime-lock.json applies to original upstream archives; no
unpublished local Git repository or developer-specific toolchain tree is needed.
All patch files are SHA256-pinned and applied forward with zero context fuzz.
source-validation.json and the original top-level fields in validation.json retain
historical source-preparation and module checks for the earlier eight-patch series.
The later window_resize_tracking section in validation.json records the added
Wine patch separately. These records are scoped evidence, not a claim about every
application's behavior on a newly compiled runtime.

wine/0001-dotnet-amd64-suspension-fallback.patch
  Removes QueueUserAPC2 from AMD64 named lookup while preserving its ordinal and
  the selected SRW-lock ordinal slot, allowing the .NET thread-suspension fallback.
  Other architectures retain their previous exports. Local modification, not
  upstream accepted.

wine/0002-xinput-button-mapping-recovery.patch
  Handles expected XInput BadDevice/BadMatch results during device open, button
  mapping and close. It keeps successful mappings, uses bounded identity fallback,
  and preserves unrelated X errors. Local modification, not upstream accepted.

wine/0003-gdiplus-call-resources.patch
  Reuses measurement resources within one layout call, allocates fallback resources
  lazily, restores selected GDI objects and bounds retained font handles. No
  cross-call cache, text approximation or application binary modification.

wine/0004-uniscribe-thread-owned-cache.patch
  Corrects losing-cache unlink/free ownership and prevents independent threads'
  implicit font-cache lookups from sharing mutable shaping arrays. Existing
  explicitly supplied SCRIPT_CACHE values retain the original path. This is a
  local correction, not an assertion that all caller cache sharing is thread-safe.

wine/0005-explorer-appwindow-taskbar.patch
  Gives visible owned WS_EX_APPWINDOW panels taskbar buttons. The enabled virtual
  desktop shell taskbar is TOPMOST so ordinary full-height panels cannot cover it.
  The taskbar remains an overlay; shared monitor workarea reservation is not added.

wine/0006-win32u-unsnapped-pointer-tracking.patch
  Keeps pointer tracking independent of the rectangle applications adjust through
  WM_MOVING/WM_SIZING. Slow drags can escape a snapping threshold while callbacks
  still control applied window bounds. Pointer clamps, size limits, child/screen
  conversion, outline dragging and Escape cancellation are preserved. Original
  synthetic fixtures cover geometry correctness at 96 DPI; physical corner dragging
  in a real application remains an individual integration check. Local correction,
  not upstream accepted. See ../docs/WINDOW-SNAPPING.md and
  ../tests/native/window-snapping/README.md. Current source builds include it;
  the published v0.2.0 runtime/source-companion assets remain the original release.

x11/0001-xid-after-sequence-sync.patch
  Replenishes the cached XID after sequence synchronization, which can release
  the display mutex. It preserves assertions and existing error/lock paths.
  Local modification to libX11 1.8.14, not upstream accepted.

gamescope/0001-static-black-buffer.patch
  Upstream Valve commit 175a67258952ae1c54a85183927b09328d20be2e, by its original
  author. Do not acquire/release client bookkeeping for the immutable black buffer.

gamescope/0002-ordered-vulkan-shutdown.patch
  Completes pipeline work and GPU submissions, then releases owning globals
  before driver process-exit handlers. Local modification, not upstream accepted.

The Wine patches retain LGPL-2.1-or-later terms. Xlib modifications retain the
applicable notices in third_party/xlib/COPYING. gamescope patches retain the
applicable BSD notices in third_party/gamescope/LICENSE. Third-party material is
not relicensed by the project's root MIT license.

The series deliberately excludes earlier installation-specific bitmap profiling,
vendor font/HTML changes and proprietary application resources. Public native
builds are a distinct upstream-based runtime; previous local binary hashes and
application measurements are not attributed to them.
