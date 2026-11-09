# Experimental native window authority checkpoint

**Known regression: do not enable patch 0011 for normal use.** Its caption bypass
can disable the application's custom fullscreen, minimize, maximize and close
buttons. Those controls can share `HTCAPTION` with the draggable header, so the
bypass can intercept them before the application distinguishes the button. This
source checkpoint preserves incomplete progress for review; it is not a completed
grouping fix or a production-ready integration.

Both feature policies and their optional trace are **off by default**. The public
launcher does not enable them. The existing v0.2.0 runtime and source-companion
assets are unchanged. Setting a variable cannot add these patches to an older
runtime. Original toolkit changes and tests were largely AI-generated; retain the
[README disclosure](../README.md) and review/test an isolated demo installation.
Wine retains its upstream authorship and LGPL terms; these local patches are not
upstream-accepted releases.

## Patch 0010: native fullscreen placement

`0010-winex11-native-window-placement.patch` changes Wine 11.20's X11 driver under
exact opt-in `QTK_NATIVE_WINDOW_PLACEMENT=1`. Eligibility requires a managed,
nonembedded, nonroot X11 whole window outside Wine's virtual desktop. This policy
is broader than the Chart/Group predicate in 0011.

For eligible windows it stops translating full-monitor window bounds into
competing host fullscreen requests, suppresses `_NET_WM_FULLSCREEN_MONITORS`
requests and replay, and retains fullscreen state selected by the host. It accepts
host geometry within the fullscreen monitor and the captionless maximize/restore
path. Wine still translates Win32 geometry/styles and retains its internal
`is_fullscreen` value for presentation/resizability. This does not remove Wine,
change the application's DPI awareness, alter monitor scaling or replace the
window manager. It does not suppress application grouping or clear an existing
preview.

Unset, empty, `0` and other values retain the previous policy. The patch affects
`dlls/winex11.drv/window.c` only.

## Patch 0011: caption move bypass, with a known control regression

`0011-native-caption-move.patch` builds on 0010. Exact
`QTK_NATIVE_CAPTION_MOVE=1` additionally requires 0010's placement opt-in and a
current X11-driver marker. The PE policy is restricted to same-process,
same-thread top-level, resizable `Chart`/`Group` windows with the expected
`HwndWrapper[Starter;;` class prefix, `WS_EX_APPWINDOW`, and a visible, enabled,
root `AMP Quantower` owner. Child, popup, tool, no-activate, modal-frame, minimized
and disabled cases do not qualify. These version-specific role checks are not a
general setting to disable grouping in other applications or broker editions.

For eligible `WM_NCLBUTTONDOWN` / `HTCAPTION` messages, the policy bypasses the
application callback and delegates to Wine's default caption move, which can
request native `_NET_WM_MOVERESIZE`. It also permits the supported native caption
move path to start from maximized state. The X11 driver publishes markers bound
to the current whole-window XID and removes them on destruction or loss of
eligibility. When the eligible backend lacks `_NET_WM_MOVERESIZE`, the caption
message is blocked rather than forwarded into the application's grouping path.
That can leave the caption unable to move; verify backend support in an isolated
setup before any experiment.

The predicate cannot distinguish custom buttons that also report `HTCAPTION`.
**Fullscreen, minimize, maximize and close controls can therefore stop working.**
A fixture in which a close button reports distinct `HTCLOSE` only checks that
separate branch; it does not validate the application's custom header controls.
Do not describe those fixture results as "controls preserved." Preventing a
caption callback also does not prove cleanup of a preview already present or
acceptance of intentional Group/tab interactions.

This patch changes both `dlls/user32/winproc.c` and
`dlls/winex11.drv/window.c`. Evaluate a coherent matching runtime, including both
modules. Do not replace only one component in a loaded installation.

## Explicit parameters and bounded trace

| Variable | Exact enabling value | Purpose |
| --- | --- | --- |
| `QTK_NATIVE_WINDOW_PLACEMENT` | `1` | 0010 placement policy; direct-host windows only |
| `QTK_NATIVE_CAPTION_MOVE` | `1` | 0011 caption policy; also requires the placement policy and driver eligibility |
| `QTK_NATIVE_CAPTION_TRACE` | `1` | Optional diagnostic output for the scoped panel message boundary; independent of enabling the caption bypass |

Leave all three unset during ordinary use of this checkpoint. Use environment
values for one deliberate test launch, not global shell/profile defaults. They
are environment variables, not launcher JSON keys. The integrated configuration
and GPU prerequisites remain those in the [integrated-window guide](INTEGRATED-WINDOWS.md).
The independent minimized-preview flag from 0009 is not a requirement of 0010/0011.

The trace uses Wine's `warn` channel `qtkcaption` and the `QTKCAP` numeric prefix.
The channel must be enabled in the selected Wine debug configuration; it is
included by `warn+all`. Avoid broad `+relay` or `+msg`. At most 1024 eligible calls
are admitted, each with an entry and return record, plus one count-limit marker.
New entries stop ten minutes after the first eligible traced call. This bounds
this trace, not every other source of Wine/application log output.

The whitelist covers nonclient left-button down/up/double-click, move/size
enter/exit, cancel/capture changes and move/size system commands. It records
numeric window/thread IDs, call/parent/depth, ticks, branch reason and limited
hit-test/command/capture metadata. It does not record typed keys, text, pixels or
arbitrary message payloads. A missing return can mean that a native modal move
has not returned. Nested callback forwarding is identified by parent/depth.
Keep generated traces private; this repository publishes no application logs.
Clear the trace variable before the next ordinary launch.

## Build, validation and rollback boundaries

`runtime-lock.json` applies 0010 then 0011 after the retained 0001–0007 and 0009 series;
there is no 0008 dependency. Use the pinned [source build workflow](../native/README.txt)
in a new directory. This checkpoint adds source patches and portable tests only;
it ships no replacement binaries or installation-specific deployer.

Source preparation and synthetic policy tests have separate scopes. They can
establish exact patch application or their selected message branches. See the
[portable fixture guide](../tests/native/window-authority/README.md) for the
explicit compiler harness and custom-caption regression case. They do not establish custom caption-button behavior, actual application
stability, a particular compositor's gestures or production trading suitability.
The known 0011 control failure remains unresolved even when automated tests pass.

Runtime changes require a normally stopped application, the shared launch lock,
verified inactive prefix and runtime users, complete manifest verification and a
coherent original-runtime backup. Preserve user state and outside edits. Do not
replace loaded modules, reset Groups/workspaces, patch signed vendor files or
restart automatically. Disabling a feature flag affects the next deliberate
process launch; it cannot change code or repair managed drag state already loaded
in a running process. Restore matching original modules and manifest together
under the same guards if rolling back a runtime trial.
