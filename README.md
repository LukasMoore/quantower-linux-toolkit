# Quantower Linux Toolkit

**AI-generated software — individual testing required.** The toolkit's original
launcher code, tools, local patches and tests were largely generated or modified
by AI. Upstream projects retain their original authorship and licenses. AI output
can contain mistakes, and passing automated tests is not independent verification.
Review the source and test it yourself in an isolated demo environment on your own
hardware before trusting it with accounts, data or live trading. This experimental
release has not established production readiness.

**Experimental source checkpoint — known title bar regression:** optional Wine
patch 0011 can disable custom fullscreen, minimize, maximize and close controls.
Patches 0010/0011 are off by default and are not a completed native-grouping fix.
Read the [native window authority checkpoint](docs/NATIVE-WINDOW-AUTHORITY.md)
before evaluating this source. Published v0.2.0 assets are unchanged.

**Prebuilt download:** [v0.2.0 — Debian 13 x86-64 runtime bundle](https://github.com/LukasMoore/quantower-linux-toolkit/releases/tag/v0.2.0).
The release includes the runtime archive, SHA256 checksums, matching source
companion and [verification report](https://github.com/LukasMoore/quantower-linux-toolkit/releases/download/v0.2.0/quantower-linux-runtime-v0.2.0-verification.json).
Download the named runtime archive; GitHub's automatic “Source code” archives
contain source only and do not include `setup.sh` or compiled runtimes.

**Source update after v0.2.0:** the current Wine build recipe includes a correction
for windows that become difficult to resize after snapping. It keeps pointer
tracking separate from application-adjusted snap rectangles. The published v0.2.0
runtime and source-companion assets remain the original release; using its download
does not install this later correction. See the [snapping fix and validation guide](docs/WINDOW-SNAPPING.md)
for source builds, regression fixtures and validation limits.

An experimental launcher, diagnostics suite and native compatibility patchset for
running a user-obtained Quantower installation through Wine on Linux. The Git
repository contains source; the released Debian runtime bundle includes
redistributable dependencies with matching notices and source archives.
The toolkit keeps each installation in a dedicated prefix, records bounded local
logs, and verifies process ownership before cleanup.

Quantower and its fonts, artwork, Windows installers and market data are obtained
separately from their publishers. This project is independent of Quantower, AMP,
Wine and Valve. It does not change account permissions, licensing or trading rules.

## Scope and support

| Area | Status |
| --- | --- |
| Launcher, logging and process-ownership tests | Isolated Python tests; no trading account or display required |
| Native compatibility patches | Local source corrections with component-specific tests; not represented as upstream accepted |
| v0.2.0 app integration | A fresh Debian 13 installation reached the full app workspace in compatible/software mode and closed normally |
| v0.2.0 GPU check | Headless gamescope/Vulkan/Xwayland/child startup passed on an NVIDIA RTX 2070 SUPER; full GPU-profile app behavior still needs individual testing |
| Other GPUs, distributions and displays | Configuration is supported; actual compatibility needs testing on those systems |
| Native Windows, macOS and 32-bit Linux | Outside this launcher's supported platform |

There is no claim that every application error is corrected, that every workload
is faster, or that passing isolated tests establishes production trading reliability.
Begin with a demo installation and keep a separate backup of your application state.
The public repository contains no platform performance comparisons or vendor UI
screenshots.

## Requirements

The **prebuilt bundle supplies Wine, gamescope, private Xlib, DXVK and open
DejaVu fonts**. You do not need a system Wine installation or compiler SDK to
use it. It targets Debian 13 x86-64 with glibc 2.41 or newer, Python 3.11 or
newer, Bash and a working graphical desktop. Host graphics drivers, CA
certificates and desktop prerequisites remain required; Debian Xwayland uses
`/usr/bin/xkbcomp` (`x11-xkb-utils`) and keyboard data (`xkb-data`).

For source/manual installations, supply or build the runtime components below:

- Linux x86-64, Python 3.11 or newer, Bash, `/proc`, Unix file locks and pidfd support.
- A Wine runtime that supports the 64-bit application **and 32-bit Windows PE
  bootstrap installers**. The source recipe builds modern WoW64 (`i386,x86_64`)
  on a 64-bit Linux host; a pure 64-bit-only Wine build cannot run those
  bootstrappers. The host itself does not need a 32-bit Linux userspace for this
  recipe.
- For the GPU profile: gamescope with its Xwayland resources, a Wayland desktop and
  a working Vulkan driver. A user-supplied DXVK `d3d9.dll` can be staged separately.
- The Windows .NET **Desktop Runtime** required by your chosen Quantower version,
  installed into its dedicated Wine prefix. Inspect `Starter.runtimeconfig.json`;
  the selected application may require both `Microsoft.NETCore.App` and
  `Microsoft.WindowsDesktop.App`. Do not infer the required version from an old guide.
- A Quantower installer obtained from its official channel and the applicable
  application, broker and market-data agreements.

`doctor.py` checks local prerequisites and configuration without starting Quantower.
From a source-only checkout, use `python3 doctor.py --source-only`.
The runtime build recipe lists additional compiler and development-library needs.
No command installs system packages or changes a host GPU driver.

## Set up a new installation

Download `quantower-linux-runtime-0.2.0-debian13-x86_64.tar.gz` and its matching
`.sha256` from the [v0.2.0 release](https://github.com/LukasMoore/quantower-linux-toolkit/releases/tag/v0.2.0)
into the same directory, then verify, extract and install:

```bash
sha256sum --check quantower-linux-runtime-0.2.0-debian13-x86_64.tar.gz.sha256
tar -xzf quantower-linux-runtime-0.2.0-debian13-x86_64.tar.gz
cd quantower-linux-runtime-0.2.0-debian13-x86_64
./setup.sh --plan
./setup.sh --target "$HOME/.local/share/quantower-linux" --install --profile compatible
```

`./setup.sh --plan` only verifies and reports a plan. The explicit `--install`
operation uses bundled Wine, gamescope, Xlib and DXVK, downloads the pinned
publisher installers separately, and creates a new isolated installation.
Debian 13, glibc 2.41 or newer, Python 3.11 or newer and a graphical session are
required. Host graphics drivers remain a host prerequisite. Other systems need
their own validation. The quick start selects the compatible rendering profile
used for the full fresh-app test. Optional `--repair-icons` opts in to the tested
local icon-font identity repair; use it only with assets you are permitted to
modify. Read [the bundle setup and packaging guide](docs/RUNTIME-BUNDLE.md)
for profile selection, offline staging, corresponding sources and testing limits.

For a source checkout, supply or build the runtime components as follows.
Keep the checkout separate from installed state. Use a **new** target directory;
the installer refuses to overwrite an existing installation.

```bash
python3 tools/install.py --help
python3 tools/install.py \
  --target "$HOME/.local/share/quantower-linux" \
  --wine-home /path/to/wine-install \
  --application-version 1.147.5 \
  --plan
```

`--plan` validates the supplied inputs and reports the intended installation
without writing files or running executables. To stage a runtime-only installation
for manual setup, repeat that command without `--plan`. To initialize and run the
user-supplied Windows installers in one operation, use the combined command below
instead. Each actual installation needs a new target.

The supplied Wine directory must be a complete install tree, including its loaders,
server and libraries. The default profile is `compatible`. For a GPU installation,
select `--profile gpu` and supply `--gamescope-home` with a complete gamescope
install tree and `--dxvk-d3d9` with the matching
64-bit DLL. Optional `--x11-home` selects a matched private Xlib install tree;
`--assert-trace` selects the locally built assertion-trace library. Relative runtime
layouts are preserved by the installer and recorded in an installation-local hash
manifest. Supplied runtime trees must be trusted and relocatable, with symlinks
contained inside their own tree. Never place the new target inside a supplied
runtime directory.

The default install operation stages source, runtime files and configuration.
Creating a prefix and running Windows installers are explicit operations. A local
Windows installer must be accompanied by its expected SHA-256 digest:

```bash
python3 tools/install.py \
  --target "$HOME/.local/share/quantower-linux" \
  --wine-home /path/to/wine-install \
  --application-version 1.147.5 \
  --app-installer /path/to/official-quantower-installer.exe \
  --app-installer-sha256 EXPECTED_SHA256 \
  --desktop-runtime-installer /path/to/windowsdesktop-runtime.exe \
  --desktop-runtime-sha256 EXPECTED_SHA256 \
  --initialize-prefix --run-installers
```

Obtain installer digests from a trusted publisher or independently verify their
signatures and provenance. A checksum supplied alongside an untrusted download
only establishes that the same bytes were copied. Review publisher terms before
explicitly running installers. Configure credentials and broker agreements yourself.
Windows installers may display interactive prompts and may automatically start
Quantower. Close any
application started by an installer, then use `./launch.sh` for a supervised run.

Pinned installer downloads can be fetched without executing them:

```bash
python3 tools/fetch-installers.py --cache "$HOME/.cache/quantower-linux/installers"
```

`installer-lock.json` records the exact official URLs and digests. The cache is
private and remains outside the published source. Recheck the application's
runtime requirements when selecting another version.

Official downloads: [AMP application manifest](https://updates.quantower.com/AMP_Quantower/x64/AppVersions.xml)
and [Microsoft .NET](https://dotnet.microsoft.com/en-us/download/dotnet).
Application installation belongs under `C:\AMPQuantowerInstalled` inside the prefix.
After installing, check that the version in `application-version.json` agrees with
`TradingPlatform/v<version>/Starter.exe` and its runtime configuration.

## Configure and run

Use `launcher-config.example.json` as the configuration reference. Installation-local
`launcher-config.json` is ignored by Git. It controls virtual-desktop size, output
size, refresh rate and optional GPU selection; the public defaults do not select a
particular graphics card. The `desktop_width`/`desktop_height` defaults are
1920×1080; `output_width`/`output_height` set to `null` let the compositor choose
output size. `refresh_hz` and `idle_refresh_hz` control cadence. An optional
`gpu_device_filter` narrows DXVK device selection; leave it `null` for the runtime
default. Audio/display endpoint overrides are optional. Changing a setting affects
the next launch.

Independent host-managed windows are available through the experimental, explicit
`window_mode: "integrated"` option with the GPU rendering profile. The default
remains `nested`. `host_display` selects a local X11 endpoint and `wine_dpi` can
set an explicit integrated DPI through the guarded profile transaction; both are
`null` by default and must remain null in nested mode. Nested geometry/fullscreen
fields are inactive in integrated mode. Use an alternate private configuration
for trials and read [the integrated-window guide](docs/INTEGRATED-WINDOWS.md) for
preflight, DPI limits, physical acceptance and coherent rollback.

From the installed target:

```bash
python3 doctor.py --help
python3 doctor.py
./launch.sh
```

Launch through `launch.sh` for session locking and full diagnostics. The internal
render helper is not the application entry point. Profile selection is explicit:

```bash
python3 select-profile.py compatible
python3 select-profile.py gpu
```

Nested GPU mode requires its compositor and graphics dependencies. Integrated GPU
mode uses host X11/Xwayland directly while retaining complete manifest verification. The compatible
profile uses Wine's available rendering path and also needs a reachable desktop
X server. Neither profile establishes cross-GPU validation by configuration alone.
Profile selection is an external troubleshooting configuration scoped to this
dedicated Wine prefix. Compatible mode sets the WPF software-rendering option and
192 DPI; GPU mode enables its normal hardware path at 96 DPI unless an explicit
integrated `wine_dpi` is selected. Existing vendor
settings are updated only where the corresponding setting already exists; first
launch does not fabricate a vendor settings file. Host Windows registry settings
are never changed. See Microsoft's [WPF rendering settings](https://learn.microsoft.com/en-us/dotnet/desktop/wpf/graphics-multimedia/graphics-rendering-registry-settings).

## Optional local icon-font identities

Some separately installed icon sets can share an internal font identity. The
optional helper inspects only the selected application version in your own
installation. Its default is a dry run:

```bash
python3 tools/isolate-icon-fonts.py --base "$HOME/.local/share/quantower-linux"
```

Use this only with installed files you are permitted to modify. To apply a reviewed
plan, first close that installation and then add `--apply`. The helper requires the
same inactive-prefix and launch-lock protections as other maintenance. It changes
internal names while preserving glyph mappings, outlines and metrics, records
private backups and hash metadata, and updates the installation manifest. Restart
through the installed `launch.sh` after a successful change.

The repository ships the generic source and synthetic tests, with no vendor fonts,
font excerpts, artwork or rewritten assets. Internal identity fields follow the
[OpenType naming specification](https://learn.microsoft.com/en-us/typography/opentype/spec/name).
The helper does not grant permission to modify or redistribute third-party assets.

## Build and inspect the native patches

`runtime-lock.json`, `patches/`, `native/` and `third_party/` record pinned upstream
sources, local changes and notices. The build tool fetches and verifies the recorded
inputs, applies only the selected patches, and builds into a new output directory.
Use its plan/help output to inspect prerequisites and commands before building:

```bash
python3 tools/build-runtime.py --help
python3 tools/build-runtime.py --component all --check-deps
python3 tools/build-runtime.py \
  --component all \
  --work-dir "$HOME/.cache/quantower-linux/build-review" \
  --cache "$HOME/.cache/quantower-linux/source-downloads" \
  --prepare-only
```

`--check-deps` checks the compiler/tool prerequisites without downloading or
creating a build directory. `--prepare-only` verifies and applies the source
patchset without compiling. For a
full build, use another new work directory and omit that flag. `--offline` requires
the verified archives to be present in the selected cache; `--jobs` controls build
parallelism. Staged install trees are written under the work directory, not `/usr`.
See [native/README.txt](native/README.txt) for component dependencies and patch
validation limits. The pinned source recipe does not guarantee bit-identical
binaries across different host compilers and system libraries. Its staged paths
map to the installer as follows:

| Built component | Installer input |
| --- | --- |
| `stage/wine/opt/wine-devel` | `--wine-home` |
| `stage/gamescope/usr` | `--gamescope-home` |
| `stage/x11/usr` (including `lib/` and `share/X11/`) | `--x11-home` |
| Locally built assertion library | `--assert-trace` |

The optional tracer is built separately with `native/build-assert-trace.py`; it
records selected assertion failures and preserves the original fatal behavior.
Follow that tool's help and private-output-directory requirement.

These runtime builds are separate from an installed prefix. Do not replace a loaded
runtime or copy experimental binaries over a running installation. Retain the
matching manifest and original runtime as a rollback unit. Source-level regression
fixtures do not establish that a freshly rebased patch has passed every historical
integration test; follow the validation status recorded with that patchset.

## Logging and support

```bash
tail -F logs/launch.log
python3 -m json.tool logs/latest-session.json
python3 support-bundle.py
```

Logs are local and private by default. Support bundles can include raw application
output with account or trading information; inspect them before sharing. Nothing
is automatically uploaded. [AGENTS.md](AGENTS.md) describes paths, retention,
crash-capture limits, lifecycle semantics and safe maintenance commands.

## Development and release checks

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q .
bash -n launch.sh render-launch.sh tests/native/build-xid-stress.sh
python3 tools/check-public-release.py
```

The release checker inspects Git's indexed blobs, not just edited working files.
For an exported source archive, use `--source-tree`. It rejects installation state,
proprietary payload types, personal home paths and several high-confidence secret
patterns. It complements human review; it does not promise complete secret detection.
CI uses Python 3.11, 3.12 and 3.13 on Linux and checks a clean source archive. It does
not install Quantower, run a broker connection or execute a live-app teardown.

See [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md) and
[THIRD_PARTY.md](THIRD_PARTY.md). Original toolkit code uses the MIT license;
third-party source and patches retain their own terms. Quantower remains subject
to its [published terms](https://www.quantower.com/terms-of-use), including restrictions
on publishing platform benchmarking information. Keep vendor assets, platform
comparison reports, application logs and private settings out of public commits.

The v0.2.0 release completed a full modern WoW64 build and passed 224 isolated
tests on Python 3.11, 3.12 and 3.13. Validation also covered 32/64-bit native
runtime probes, taskbar minimize/restore, archive tamper refusal, relocation,
read-only installation and a fresh compatible-mode app workspace. See the
[release verification report](https://github.com/LukasMoore/quantower-linux-toolkit/releases/download/v0.2.0/quantower-linux-runtime-v0.2.0-verification.json)
and [source CI](https://github.com/LukasMoore/quantower-linux-toolkit/actions/runs/42100964478)
for evidence and limits. `patches/validation.json` preserves earlier per-patch
checks; it is not the complete validation record for the released bundle.
