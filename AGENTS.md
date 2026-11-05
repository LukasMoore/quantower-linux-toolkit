Installation-specific context may be recorded in an ignored `AGENTS.local.md`.
Read it when present, while preserving the process-ownership rules below. Keep
local authorization, paths and runtime history out of public commits.

# Quantower Linux Toolkit maintenance

This repository contains original launcher/diagnostic code and a source-only native
patchset. Installation state is separate: a dedicated Wine prefix, user-obtained
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

## Testing and public release

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
