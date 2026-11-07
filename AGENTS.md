Installation-specific context may be recorded in an ignored `AGENTS.local.md`.
Read it when present, while preserving the process-ownership rules below. Keep
local authorization, paths and runtime history out of public commits.

# Quantower Linux Toolkit maintenance

This repository contains original launcher/diagnostic code and a native source
patchset. Optional runtime archives contain redistributable dependencies with
matching notices and corresponding sources. Installation state is separate: a dedicated Wine prefix, user-obtained
application/runtime files and generated manifests are local and excluded from Git.
Read README.md for installation and THIRD_PARTY.md for licensing boundaries.

## Working on the launcher

- Preserve exact UID, Wine prefix, session token, PID start-time and pidfd guards.
  Unknown process ownership blocks cleanup. An active Starter blocks cleanup,
  including cleanup of its ancestors. Never replace these rules with process-name
  kills, global `wineserver -k`, or an automatic restart on an error or timeout.
- `application-version.json` pins an explicit application version.
  `application_runtime.py` derives its Wine path and working directory. Retain
  broker-specific branding, licensing and connection-vendor selection.
- `runtime-profile.json` selects `gpu` or `compatible`. `profile_manager.py` verifies
  installed component hashes and applies profile changes only to an inactive prefix.
  The installer creates an installation-local manifest; do not weaken verification
  to accept missing or changed declared files.
- `launcher-config.json` is installation-local. Keep hardware and display values
  configurable and validate them before constructing process arguments. Never source
  untrusted configuration as shell code. The public example has neutral defaults.
- Keep runtime paths inside the installer-managed canonical layout so process
  executable checks remain meaningful. Changing runtime locations requires updating
  ownership selectors and tests together, not just replacing a command string.
- Keep the GPU compositor's `PATH` headed by managed `gamescope/files/bin`,
  preserving the caller path or `os.defpath` when absent. Gamescope launches
  `gamescopereaper` by name through `execvp`; an inherited host path is insufficient.
  Source: [Valve Gamescope 3.16.25](https://github.com/ValveSoftware/gamescope/blob/3.16.25/src/Utils/Process.cpp).
- Keep launcher diagnostics stdlib-only. Tests use temporary fake installations and
  mocked process discovery. They must never launch or terminate the real app.
- Apply native changes to pinned official sources in new build directories. Preserve
  upstream notices and record that local corrections are not upstream-accepted
  releases. A fresh rebase needs its own validation; historical tests do not prove
  a different runtime build correct.
- Every application upgrade must review version-specific icon/CEF compatibility
  needs. Do not copy older vendor resources over newer assets, mix CEF versions,
  patch a signed vendor DLL to hide errors, or publish proprietary resource files.
- Maintain this document when changing logging paths, retention or commands.

## Experimental integrated windows

- `window_mode=nested` stays the default. Integrated v1 supports GPU only; reject
  integrated+compatible and nested non-null host/DPI fields before prefix writes.
- Read [docs/INTEGRATED-WINDOWS.md](docs/INTEGRATED-WINDOWS.md) before changing the
  direct-launch plan, connection preflight, DPI transaction or mode-aware recovery.
  Builders and doctor remain read-only; only explicit launch preflight connects.
- Preserve complete runtime manifest checks and the dedicated Wine/private-Xlib/
  DXVK closure. Never adopt, signal, unlink or restart host display/native services.
  Integrated cleanup additionally requires exact dedicated Wine executable roles at
  each boundary; same-birth identity loss/unknown roles remain uncertain in ledger.
- Recover the prior ledger mode before beginning a newly requested mode. Continue
  supervising Starter after bootstrap exit and keep unexpected child failure sticky.
- Apply explicit DPI only through `prepare(..., wine_dpi=...)` under the existing
  inactive-prefix/shared-lock gates. Do not alter vendor awareness or signed files.
- Offline and own-fixture passes do not prove physical multi-monitor behavior. Keep
  live input guards, hooks off and GPU-fault stop rules. No automatic recovery.
- Wine 11.20 patch `0007-comctl32-signed-tracking-tooltip-coordinates.patch` fixes
  only signed 16-bit extraction of `TTM_TRACKPOSITION` screen x/y. Retain every
  prior patch, complete runtime hash verification, tooltip placement rules and
  signed vendor files. Test
  common-controls v5/v6 with negative and nonnegative controls; own-fixture passes
  still require separate host/application popup acceptance. Preserve zero-width
  and outside-workarea failures; signed decoding does not prove visible placement.
  This is a local
  correction, not upstream acceptance or a GPU-lockup fix. Source builds include
  the added patch; existing published 0.2.0 archives remain unchanged.
- Old-code rollback requires an old-parser-compatible config without any new keys;
  preserve outside edits and never restore whole current account/workspace state.

## Logging architecture

`launch.sh` sets a private umask and calls `diagnostics.py run`. Diagnostics acquires
`logs/launch.lock` before rotating logs or writing session files. If another
supervisor owns it, a duplicate launch exits without touching that session. Direct
execution of `supervisor.py` delegates to the same diagnostics entry point.
`render-launch.sh` is internal; normal launches use the front door.

The entry point catches supervisor import/startup failures and unhandled exceptions.
The supervisor records runtime/profile selection, child PID, first Starter identity,
startup timeout, child exit code/signal, launcher stop signal and cleanup outcome.
Exceptions include Python tracebacks. Cleanup retains all ownership guards.

A background reader follows merged child stdout/stderr in an append-only regular
file, including Wine, gamescope, DXVK and cleanup commands. Mirrored lines receive
UTC timestamps, session ID, source and inferred severity. ANSI escapes are removed;
invalid UTF-8 is replaced and long/no-newline output is split into 8 KiB chunks.
Severity is a text heuristic, not an authoritative application-error classification.
Python stdout/stderr are captured too. Children write to `runtime.raw.log`, not a
supervisor-owned pipe: a surviving process can keep writing after supervision exits
without a broken pipe or SIGPIPE. Preserve this design.

| Path relative to the installation | Contents and policy |
| --- | --- |
| `logs/launch.log` | Human-readable lifecycle, tracebacks and runtime output; rotates at 10 MiB with `.1` through `.4` |
| `logs/events.jsonl` | Structured events and heartbeats; rotates at 2 MiB with `.1` through `.4`; raw lines remain in `launch.log` |
| `logs/latest-session.json` | Atomic latest-session snapshot: phase, observed PIDs, status, timestamps, output counts and logging failure counters |
| `logs/sessions/<UTC timestamp>-<random ID>/summary.json` | Per-session snapshot; the public correlation ID differs from the private process token |
| `logs/sessions/<ID>/runtime.raw.log` | Original append-only stdout/stderr, usable by surviving app processes |
| `logs/sessions/<ID>/gpu/` | Per-session DXVK files when that runtime is selected |
| `logs/sessions/<ID>/native-assert/` | Optional installed assertion tracer's crash metadata; not complete memory dumps |
| `logs/sessions/<ID>/python-fault.log` | Fatal Python diagnostics and explicitly requested supervisor thread dumps; normally empty |
| `logs/launch.lock`, `logs/session-processes.json` | Live ownership state; never rotate/delete while used; private ledger/token excluded from support archives |
| `logs/setup.raw.log` | Private Wine/.NET/native-extraction output for new-prefix setup; preserved on failure, no automatic rotation |
| `logs/font-backups/` | Explicit local icon-repair transaction records and original private font bytes; never publish or auto-delete |
| `prefix/drive_c/AMPQuantowerInstalled/Logs/Serilog/*.slog` | Vendor-managed application logs, left unchanged |
| `prefix/drive_c/AMPQuantowerInstalled/Logs/Cef/CefLogs.log` | Vendor embedded-browser log; support collection checks known alternate layouts too |
| `logs/gpu/`, `logs/native-assert/` | Legacy diagnostic locations, if present |
| `logs/support/*.tar.gz` | Explicit local support archives; inspect before sharing |

Wrapper timestamps are UTC. Vendor timestamps can use their own convention.
Status describes the launcher: `running`, `finished` (exit 0), or `failed` (nonzero).
Starter disappearance records `starter_disappeared`; its application exit status is
unavailable. A direct Wine/compositor child exit is not necessarily Quantower's
managed application status. Lifecycle changes immediately update phase and observed
Starter PIDs; verified disappearance clears them, while deferred cleanup retains
last-known identity. The first observed Starter enters the guarded ledger.

Final direct-child status is recorded after cleanup. Unexpected nonzero status
marks the session failed. SIGTERM is expected only when this cleanup actually sent
it through the guarded process handle to that live direct child. External SIGTERM,
SIGKILL, and deferred/unconfirmed cleanup remain explicit. A forced Wine-helper
termination emits `wine_helper_cleanup_escalated` and summary fields
`cleanup_required_force`/`cleanup_forced_wine_helpers`; a compositor exit 0 does not
hide that escalation. Compositor shutdown precedes its Xwayland child shutdown.

## Health information and retention

- Default Wine diagnostics are `-all,err+all,warn+all`; trace/fixme flood channels
  remain off. `QUANTOWER_WINEDEBUG` overrides one launch and is recorded at startup.
- Heartbeats occur every 60 seconds while monitoring. They record phase, PIDs,
  uptime, output byte count and last output time. They confirm supervision evidence,
  not UI/market-data/order-routing responsiveness. Quiet output alone is not a hang.
- A later launch reports `previous_session_unclean` for an unfinished prior summary.
  It means supervision did not finalize; it does not by itself establish an app
  crash. SIGKILL or power loss cannot be logged at the instant it happens.
- On startup, eligible finalized historical sessions are pruned to at most 19
  previous sessions, at most 30 days old and an approximate 100 MiB budget. Only
  generated directory names qualify. Current, unfinished, corrupt-summary and
  deferred/unconfirmed-cleanup sessions are preserved because a survivor may still
  hold their raw file. These exceptions can exceed the limits.
- Rolling text/events files are bounded during a run. Raw, DXVK and native files
  are not rotated underneath external writers; a long current run can exceed the
  historical budget. Vendor logs, legacy diagnostics, support archives and rollback
  copies are not automatically deleted. Remove selected stale evidence only after
  verifying its processes have stopped.
- New files are mode 600, private directories mode 700, with umask 077. Existing
  legacy/vendor permissions are not changed recursively.
- Rolling write failures use terminal fallback and increment counters. Summary
  write failures are recorded when possible without stopping supervision. Raw and
  sidecar writes may still fail on full storage. Missing/unwritable startup logging
  storage can prevent launch and produces a terminal traceback. Shutdown mirroring
  has a bounded drain deadline; surviving writers retain their original raw file.

## Diagnostic commands

Run these inside the installed target directory:

```bash
tail -F logs/launch.log
tail -n 30 logs/events.jsonl
python3 -m json.tool logs/latest-session.json
python3 support-bundle.py
python3 support-bundle.py --max-file-mib 10 --max-total-mib 80
du -sh logs prefix/drive_c/AMPQuantowerInstalled/Logs
```

The support collector is read-only to the app and can run while it is active. It
includes current/recent rolling logs, the latest three session summaries, bounded
raw tails, Python/GPU/assert files, recent vendor-log tails, profile, app selection,
installed runtime manifest and explicitly allowlisted small provenance files.
Defaults are 5 MiB per source and 40 MiB total source payload, before archive overhead.
Its manifest lists sources, sizes, truncation/tail offsets and skipped files. A
truncated text/JSON tail may start mid-record. This is a best-effort live snapshot,
not a consistent installation backup.

Registry, settings, account files, environment, process tokens and arbitrary rollback
files are excluded. Symlinks and symlink ancestors are rejected. Output must be a new
archive in a private directory; existing files are never overwritten. Select another
private destination with `--output`. Raw vendor logs can still contain account or
trading information. Automatic redaction is not promised. Inspect before sharing;
nothing uploads automatically. Support archives have no automatic retention.

For a verbose reproduction, close the app normally and then run:

```bash
QUANTOWER_WINEDEBUG='-all,err+all,warn+all,+seh' ./launch.sh
```

Avoid `+all`; it creates substantial output and overhead. `SIGUSR1` requests a Python
thread dump only from a verified current diagnostics supervisor with that handler.
Never send it to Starter or an unrelated/older process. It dumps wrapper threads,
not managed UI threads.

Normal launches clear inherited `DOTNET_STARTUP_HOOKS`. Explicit maintenance may use
`QUANTOWER_DIAGNOSTIC_STARTUP_HOOKS` with absolute Windows DLL paths separated by
semicolons. Hooks are executable code inside the app and are never normal logging
defaults. Do not leave experimental observers or migration hooks enabled.

## GDI presentation maintenance

- In pinned DXVK 3.1.1, a partial destination with COPY semantics (including
  windowed DISCARD with one backbuffer) can deliberately use GDI. Its warning is
  once per swapchain, not a frame count or FPS measurement. Preserve untouched
  pixels, retained backbuffer contents and writable public `GetDC`/`ReleaseDC`
  behavior; do not bypass the fallback or invent a backbuffer-count option.
  Sources: [presentation selection](https://github.com/doitsujin/dxvk/blob/b1a1c99ab52b687cf950d62c88bc2fa316b41663/src/d3d9/d3d9_swapchain.cpp),
  [COPY semantics](https://learn.microsoft.com/en-us/windows/win32/direct3d9/d3dswapeffect),
  [surface DC handling](https://github.com/doitsujin/dxvk/blob/b1a1c99ab52b687cf950d62c88bc2fa316b41663/src/d3d9/d3d9_surface.cpp).
- WPF 10.0.13 recognizes
  `Switch.System.Windows.Media.MediaContext.DisableDirtyRectangles`. Treat it as
  an experimental GPU-profile candidate: it increases redraw area and GPU work,
  and clipping can still require partial presentation. Review the matching
  [switch implementation](https://github.com/dotnet/wpf/blob/v10.0.13/src/Microsoft.DotNet.Wpf/src/PresentationCore/MS/internal/CoreAppContextSwitches.cs#L331)
  and [full-target invalidation](https://github.com/dotnet/wpf/blob/v10.0.13/src/Microsoft.DotNet.Wpf/src/WpfGfx/core/hw/hwdisplayrt.cpp#L383).
  Change runtime configuration only, retain signed vendor DLLs, and never enable
  this automatically for the compatible/software profile.
- Require independent pixel-preservation checks, resize and minimize/restore
  checks, and steady CPU-call comparisons on the exact runtime and target
  hardware before retaining a candidate. Compare frame pacing under a matched
  changing visual workload before claiming smoother display or higher FPS.
  Exclude startup compilation from steady statistics and keep a verified rollback
  under the ownership guards.
- Temporary diagnostic builds must use explicit opt-in, bounded metadata and
  aggregate counters; avoid per-frame logging. Local diagnostic toggles are not
  features of released upstream DXVK unless independently verified. Host Present
  call timings describe call cost, not visible FPS or display latency.

## Window move and resize maintenance

- Wine 11.20 allows application callbacks to change the rectangle passed through
  `WM_MOVING` and `WM_SIZING`. Applying each new pointer delta to that adjusted
  rectangle can trap slow movement inside a snapping threshold. Patch
  `0006-win32u-unsnapped-pointer-tracking.patch` retains a separate unsnapped
  tracking rectangle while preserving the callback-adjusted rectangle for display.
  Retain snapping, pointer clamps, minimum/maximum sizes, Escape cancellation,
  child/screen coordinate conversions and full/outline dragging.
  Sources: [pinned Wine loop](https://github.com/wine-mirror/wine/blob/wine-11.20/dlls/win32u/defwnd.c),
  [WM_SIZING](https://learn.microsoft.com/en-us/windows/win32/winmsg/wm-sizing),
  [WM_MOVING](https://learn.microsoft.com/en-us/windows/win32/winmsg/wm-moving).
- Gate native regression results on valid input/capture delivery and exact expected
  geometry, not just a harness exit code. Cover all eight resize directions, slow
  and fast motion, reversal through a magnet, endpoints inside its range, ordinary
  unsnapped movement, size limits and cancellation. Keep child, outline and bounded
  keyboard checks. Preserve invalid development attempts as excluded evidence;
  never count them as passing cases. Fixtures are under
  [tests/native/window-snapping](tests/native/window-snapping/README.md).
- [Quantower standalone panels](https://help.quantower.com/quantower/general-settings/standalone-panels)
  can snap during movement and resize. A
  [Bind](https://help.quantower.com/quantower/general-settings/binds) uses one outer
  frame with internal dividers, and
  [Workspace Lock](https://help.quantower.com/quantower/general-settings/workspaces-binds-groups)
  disables panel movement/resizing. Check which state is involved before attributing
  a corner problem to Wine. Do not patch signed vendor DLLs or invent a registry
  setting to disable the application's magnets.
- Runtime maintenance requires a normally stopped application, verified inactive
  prefix and the shared launch lock. Preserve the matching original runtime and
  manifest, validate replacement hashes, and roll back the coherent pair only while
  inactive. Never replace a loaded module or weaken process-ownership guards.
- A nested display can fail to retain a programmatic cursor warp even when the API
  returns success. Keep pointer-delivery guards and report inconclusive live gestures
  honestly. Independent synthetic geometry checks do not establish that a particular
  app's physical corner drag, frame rate or trading workflow has been verified.
  See [docs/WINDOW-SNAPPING.md](docs/WINDOW-SNAPPING.md).

## Testing and public release

Runtime bundles target Debian 13 x86-64 and keep host libc and graphics drivers
outside the archive. Follow [docs/RUNTIME-BUNDLE.md](docs/RUNTIME-BUNDLE.md) and
[docs/BUNDLE-LICENSING.md](docs/BUNDLE-LICENSING.md). Stage only newly built,
relocatable runtime trees into a new directory. Every payload file and symlink
needs component ownership, preserved notices and a verified matching source
artifact before sealing. Publish the binary and source companions together.
Keep Quantower, Windows installers, prefixes, accounts, logs and vendor assets
out of both runtime and public source archives.

`tools/setup.py` verifies the complete sealed bundle before importing its setup
helpers. Its default and `--plan` operations must remain read-only; `--stage`
must not execute Wine or download. Only explicit `--install` fetches pinned
publisher inputs and creates a new prefix. Do not add an implicit launcher start.
Post-install repairs must wait for complete guarded setup cleanup and acquire
their own inactive-prefix lock. Preserve archive traversal, link-containment,
checksum, restrictive-permission and no-overwrite tests. Synthetic tests do not
replace a fresh-prefix application test of the exact packaged runtime.

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q .
bash -n launch.sh render-launch.sh tests/native/build-xid-stress.sh
python3 tools/check-public-release.py
```

Cover output floods, malformed output, exceptions, startup/child failures, duplicate
locking, storage failures and active-app preservation in isolated fixtures. Process
cleanup changes require ownership/PID-reuse checks. Real app restarts are manual
integration work, never an automated test. CI's Linux Python matrix does not prove
cross-GPU or live-trading behavior.

Public-source checks must inspect indexed blobs and a clean archive. Keep private
paths, account state, logs, vendor binaries/fonts/artwork and platform performance
reports out of Git. Preserve upstream licenses for all native sources. The
published source must contain enough pinned inputs and instructions to rebuild its
advertised patches. See SECURITY.md and CONTRIBUTING.md for review expectations.

## AI provenance and user validation

Keep the prominent AI-generation disclosure in the README and release notes.
Original toolkit code and local patches were largely AI-generated; upstream work
retains its own attribution. Report concrete checks and their limits. Require users
to review and individually test an isolated demo setup before relying on the toolkit;
do not equate test passes with production readiness or trading safety.
