# Experimental host-managed windows

`window_mode=integrated` runs the selected AMP Quantower application directly on
host X11/Xwayland using the dedicated Wine runtime, private Xlib and GPU/DXVK path.
Appropriate standalone panels can then be managed by the Linux desktop. This is an
explicit experimental option. `nested` remains the default, and existing launcher
configuration files without the new fields keep their previous behavior.

| Window mode | Rendering profile | Behavior |
| --- | --- | --- |
| `nested` | `gpu` | Existing gamescope/private-Xwayland virtual desktop |
| `nested` | `compatible` | Existing Wine virtual desktop |
| `integrated` | `gpu` | Direct host X11/Xwayland, experimental |
| `integrated` | `compatible` | Refused before profile/prefix writes |

Quantower remains a Windows application running through Wine. Groups, Binds and
tabbed panels remain one outer window. Create or detach standalone panels through
the normal application UI. See Quantower's [standalone panels](https://help.quantower.com/quantower/general-settings/standalone-panels),
[Groups](https://help.quantower.com/quantower/general-settings/group-of-panels)
and [Binds](https://help.quantower.com/quantower/general-settings/binds) guides.

## Configuration and one-launch selection

Close the application normally before maintenance. Keep the regular launcher
configuration and shortcut on `nested` during a trial. Copy the installed
`launcher-config.json` to a separate private JSON file, preserving its existing
values, and add these schema-version-1 fields:

```json
{
  "window_mode": "integrated",
  "host_display": null,
  "wine_dpi": null
}
```

This excerpt contains only the additional fields; retain `schema_version: 1` and
the other fields in the copied configuration. Launch from the normal host desktop
with an absolute path to that alternate configuration:

```bash
QUANTOWER_LAUNCHER_CONFIG=/absolute/private/integrated-config.json ./launch.sh
```

The selected rendering profile must already be `gpu`. Profile selection remains
separate from window placement. Use `launch.sh` for the shared launch lock,
recovery, preflight and diagnostics; the internal render helper refuses integrated
configuration.

`host_display: null` inherits the caller's `DISPLAY`. An explicit value overrides
only the child endpoint and must use local X11 syntax such as `:0` or `:0.0`.
There is no guessed display, remote/SSH X11 support or alternate-backend retry.
Inherited nested-compositor markers/private endpoints are rejected. Launch
preflight performs one bounded passive `xwininfo -root -stats` connection check
before profile writes. It reads root metadata, not pixels. It does not change
Xauthority or start, stop or modify the host display server. `doctor.py` checks
prerequisites only; it does not connect to displays or execute that probe.

`wine_dpi` accepts `null` or an integer from 96 through 480. `null` retains the
rendering-profile default, 96 for GPU and 192 for compatible. An explicit integrated
DPI is applied through the same guarded atomic profile transaction as rendering
settings and both Wine LogPixels values, and is recorded in the applied profile
and session diagnostics. Start own-fixture measurements at 96; test 192 only as a
separate normally stopped transaction if sizing calls for it. No value is a promise
of correct real-application scaling on an untested desktop.

In `nested`, both `host_display` and `wine_dpi` must be absent or `null`. Integrated
mode retains but does not use nested desktop/output dimensions, fullscreen and refresh configuration. Keeping those fields permits a deliberate return
to the previous nested setup.

## Requirements, ownership and failure behavior

The complete installed runtime manifest is still verified, including declared
assets unused by the direct launch. No signed application file, branding,
licensing, account or connection selection is changed. Wine's effective Explorer
Desktop preference must be absent, and effective managed-window/graphics-driver
preferences must permit X11. Conflicting, duplicate or malformed preferences are
refused rather than silently rewritten. Native Wine Wayland is disabled for this
launch; X11 selection must be established by original own-window fixtures.

The direct child may hand off to Starter and exit. Supervision continues through
the bounded startup handoff and while authenticated Starter processes remain.
Direct-child status is recorded separately from the unknown vendor application
exit status. Unexpected nonzero child status remains a failed launcher result.
Failure never automatically restarts the app or switches backend.

All UID, canonical prefix, session token, process-birth and pidfd guards remain.
Integrated cleanup requires canonical dedicated Wine executable roles and verified
Starter absence. Unknown identities/roles defer cleanup and remain in the ledger;
same-birth loss of identity evidence does not establish process death. A prior
session's persisted mode governs its recovery even if the new configuration has
changed. Host Xwayland, GNOME and other native processes are never cleanup targets.
Forced Wine-helper cleanup remains explicit in warnings and summaries. Never
force-kill Starter, kill process groups or use an unscoped wineserver command.

## Explicit new-session choices in desktop adapters

A desktop adapter that accepts only a previous `finished`/exit-0 summary can block
every later open after a failed session, even when cleanup completed. The shared
`session_restart.py` policy distinguishes a finalized failure from unfinished or
unverifiable history. It can offer **Start a new session**, with Cancel focused by
default, after the adapter proves the installation is fully stopped. This does not
establish why the earlier application session failed or fix its shutdown behavior.

This is a source API for optional desktop adapters. The public toolkit does not
include an Open-or-Recover desktop frontend. The normal `launch.sh` remains the
front door; adding this policy does not change its behavior or enable automatic
restarts. New installations include the module for adapter integrations.

`stopped_session_state(summary)` classifies an already authenticated summary as
`inactive` or `inactive-failed`, or raises on incomplete/inconsistent data. History
alone never proves process inactivity. A failed session must have a nonzero integer
exit, completed cleanup, final failed phase, a valid session ID and timezone-aware
end timestamp, an exited child, no Starter PIDs and no pending handoff.

`confirm_failed_session_start(...)` requires four no-argument callbacks:

- `inactive_lock`: acquire the normal shared launch lock and verify canonical
  prefix inactivity, recorded process births and all users of the dedicated
  runtime. Unknown ownership must refuse. It returns a context manager and runs
  again after acceptance.
- `read_summary`: safely read a fresh private JSON object with ownership, regular
  file, path and size checks; refuse missing, redirected or malformed files.
- `verify_deployment`: verify the complete selected runtime manifest and adapter
  configuration while the inactivity lock is held.
- `ask`: show the explicit choice and return an integer exit code: 0 accepts,
  1 cancels. Other codes, booleans and exceptions refuse. The provided
  `ask_start_new_session()` uses `/usr/bin/zenity` with Cancel selected by default.

The question appears outside the shared launch lock. Acceptance reacquires that
lock and compares the complete fresh summary with the original snapshot. The
adapter must hold its own duplicate-action lock across classification, confirmation
and start. Only a True result may reach its one-shot normal front-door request,
which must repeat the production inactivity, deployment and ownership checks.
Concurrent launches can refuse; never retry, signal processes or switch backend.
Recovery-only actions must never ask to start or start a new application session.

The policy never launches the app, changes a summary, cleans up processes or retries.
The portable regressions use synthetic summaries and mocked callbacks/GUI; they do
not establish physical desktop behavior or resolve application shutdown hangs.

## DPI and acceptance limits

Physical scanout, desktop logical coordinates, X11 backing pixels, Win32 virtualized
coordinates, WPF DIPs and browser CSS units can differ. Negative coordinates on a
monitor left of the primary are valid. Do not copy numbers between those spaces
or alter host monitor settings to manufacture a passing result. Changing Wine DPI
does not change the application's declared or actual awareness. No signed-manifest
rewrite, late awareness hook or PerMonitorV2 guarantee is included. Microsoft's
[DPI guidance](https://learn.microsoft.com/en-us/windows/win32/hidpi/high-dpi-desktop-application-development-on-windows)
explains the distinction; measure actual HWND awareness and GetDpiForWindow.

Before deployment, run offline regressions and original own-window fixtures in a
disposable prefix. Establish mapped X11 backend, independent top-level windows,
monitor/workarea units, readable output, child controls, focus, owned menus,
tooltips/dialogs and callbacks. A build or synthetic pass does not establish actual
Quantower behavior.

For an isolated demo, preserve the user's workspace and use a separate test
workspace through the UI. Physically move representative standalone Chart,
DOM/Order Entry and Group windows to every target monitor. Verify focus with Linux
apps, keyboard entry, all resize edges/corners, slow snap escape/reversal,
maximize/restore, popup placement, Alt-Tab and taskbar minimize/recovery. Check actual
non-trading Settings, Notification Center and Connections-manager actions through
three reopen cycles without changing preferences or connections. Verify saved
monitor placements after normal close/relaunch and three complete normal lifecycle
cycles. Quantity editing requires a saved original value and controlled restoration
that preserves intervening user changes. Do not submit/cancel orders, flatten
positions or change accounts/connections.

Keep observer/startup hooks and heavyweight capture disabled during acceptance.
Use matched bounded baseline/candidate screening and light resource sampling;
20 minutes of changing-chart activity in each mode is a screening target, not
proof of long-term stability, FPS, latency or trading safety. Keep known vendor
errors visible. Stop further testing if a new GPU fault or desktop stall occurs,
preserve private evidence and attempt normal close only if responsive. No automatic
restart, GPU reset, reboot, driver/kernel or monitor change is part of this feature.
If kernel evidence or physical checks are unavailable, record those gates as
unverified. Mixed-DPI, rotation, hotplug and changed-primary behavior require
separate validation. Integrated mode is not an established GPU-lockup or CEF fix.

## Tracking tooltips at negative monitor origins

Wine 11.20's common-controls tracking tooltip handler widened the packed x/y words
of `TTM_TRACKPOSITION` as unsigned values. A point on a monitor left of or above the
primary could therefore become a large positive coordinate before existing
placement rules ran. Local patch
`0007-comctl32-signed-tracking-tooltip-coordinates.patch` casts each word through
`SHORT` before widening to `INT`. This applies the signed 16-bit screen-coordinate
contract for both axes while retaining the existing tooltip layout, workarea and
activation behavior. See Microsoft's [tracking-position message](https://learn.microsoft.com/en-us/windows/win32/controls/ttm-trackposition)
and signed-coordinate [x](https://learn.microsoft.com/en-us/windows/win32/api/windowsx/nf-windowsx-get_x_lparam)/[y](https://learn.microsoft.com/en-us/windows/win32/api/windowsx/nf-windowsx-get_y_lparam)
extraction guidance.

The [original tooltip-coordinate regression](../tests/native/integrated-windows/tooltip-coordinates/README.md)
compares baseline and candidate common-controls v5/v6 at 96/192 prefix DPI, with
negative x/y and nonnegative controls. It distinguishes requested tracking points,
client origins and outer bounds and verifies the actual mapped module. A disposable
synthetic display establishes only signed-decoding evidence. Some negative points
on the sole-screen Xvfb fixture lie outside all active workareas; unchanged
clipping can produce a zero-width tooltip despite correct decoded coordinates.
The portable runner retains that failed geometry gate and classifies targets from
actual `EnumDisplayMonitors` workareas. It does not report those cases as visible
negative-monitor acceptance. Actual host tooltip
visibility, ownership, placement and unchanged focus on each target monitor remain
separate acceptance gates, as do vendor popups and dialogs. The patch does not add
wider-than-signed-16-bit message coordinates or establish general balloon-tooltip,
mixed-DPI or GPU stability correctness.

This is a local Wine source correction, not an upstream-accepted fix. Existing Wine
patches, private Xlib, DXVK and vendor application remain in place; deploying the
changed common-controls modules requires the same stopped-prefix, shared-lock,
matching-hash and coherent-backup guards. Source builds gain the additional patch.
Previously published 0.2.0 runtime/source archives are unchanged and must not be
represented as containing it. It is not a GPU-lockup or CEF fix.

## Deployment and rollback

Deploy reviewed changes only after normal close, exact prefix-inactivity checks and
acquiring `logs/launch.lock`. Save a coherent, hash-verified, no-overwrite private
backup of changed code/config/profile/manifest state and a separate private settings
reference snapshot. Port reviewed files or hunks; an existing installation can
have meaningful source drift. Never replace an installation wholesale or clone a
running prefix. Keep captures, logs, runtime resources, settings and machine-specific
measurements out of public source and support uploads.

With the new code, omit the alternate-config override to use the untouched normal
nested configuration. If editing the alternate config instead, set
`window_mode: "nested"`, `host_display: null` and `wine_dpi: null` together. The next
deliberate guarded launch reapplies nested profile DPI (GPU 96, compatible 192).
This selection does not launch or roll back automatically.

To restore old code, first close normally, verify inactivity and acquire the same
lock. Verify backup hashes and compare current files against the deployed candidate;
refuse outside edits. Restore only this feature's owned code/config/profile/DPI
changes. Old strict parsers reject all three new keys even when mode is nested:
unset this launch's `QUANTOWER_LAUNCHER_CONFIG` override and use the untouched old
configuration, or remove only the transaction's own new keys with comparison
checks. Restore old code and a compatible old configuration coherently. Never
blindly overwrite independently changed config/environment, whole registry or
current account/workspace settings. Retain runtime pins and native DLLs. A failed
write/rollback leaves the app stopped with explicit diagnostics.

## Minimized GDI previews in desktop overview

A compositor can retain a managed X11 window after minimization for its overview
preview. Wine's buffered GDI presentation can replace the normal chart image and
bounding shape with its small iconic client, producing a white rectangle.
Local source patch `0009-winex11-preserve-minimized-preview.patch` preserves the
last normal GDI pixels and shape under this explicit opt-in:

```bash
QTK_PRESERVE_MINIMIZED_PREVIEW=1 \
  QUANTOWER_LAUNCHER_CONFIG=/absolute/private/integrated-config.json ./launch.sh
```

Only the exact string `1` enables preservation; unset, empty, `0` and other values
retain the original behavior. Set it for the selected integrated launch, after
building and verifying a coherent runtime containing `0009`. Merely setting the
flag with an older prebuilt runtime does not install the fix. Existing published
runtime and source-companion assets remain unchanged. The source patch has no
`0008` dependency and is not an upstream-accepted Wine release.

Eligibility is cached outside the GDI surface lock. Minimized flushes defer pixels
and shape changes; normal rendering and deferred shape resume after restoration.
This preserves a static last frame. It does not change window ownership, taskbar
listing, popup/close policy, application binaries or GPU client-surface rendering.
Repeated deferred flush work and application/compositor behavior require separate
resource and physical acceptance; no GPU-lockup correction is claimed.

The [original callback and GDI fixtures](../tests/native/integrated-windows/minimized-preview/README.md)
cover opt-in/default behavior, exact drawable/shape preservation and two restore
cycles, including resize. Keep generated binaries, captures, prefixes, private
application state and machine-specific reports outside public source. Deployment
and rollback retain the normally stopped prefix, shared lock, backup and complete
runtime-manifest requirements above. Disabling the flag changes the next deliberate
launch only; do not replace a loaded module or restart automatically.
