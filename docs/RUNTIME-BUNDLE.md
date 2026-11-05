# Versioned Debian runtime bundles

The prebuilt bundle contains the toolkit source and redistributable runtime
components. Quantower, Microsoft's Windows installers, accounts, settings and
Wine prefixes are not included. Original toolkit code and local patches were
largely AI-generated; inspect the source and test your own isolated demo setup
before relying on it. A test pass is not a production-readiness guarantee.

The first target is Debian 13 on Linux x86-64 with glibc 2.41 or newer. Host
graphics drivers and a working graphical session are still required. The runtime
does not replace the host kernel, libc, graphics driver or host service settings.
Other distributions or GPUs need their own validation. The host desktop also
supplies CA certificates, Fontconfig/system fonts, `/usr/bin/xkbcomp`
(`x11-xkb-utils`) and XKB keyboard data (`xkb-data`). Debian Xwayland uses
that compiler path directly; adding a helper to PATH does not replace it. The
reference fixture records these baseline package versions in the source companion;
it has no system Wine or compiler SDKs. Modern WoW64 supplies the Windows 32-bit
bootstrap support without requiring Linux i386 multilib packages.

Wine includes modern WoW64 (32-bit and 64-bit Windows modules), GnuTLS,
Fontconfig/FreeType, ALSA/PulseAudio, XInput2, OpenGL and Vulkan support. This
build omits OpenCL, smart cards, host VA/FFmpeg-library integration, OSSv4, ISDN,
UnixODBC and Samba NetAPI integration;
translations are not rebuilt because gettext tools are absent. Those optional
capabilities need a separate build if your workload uses them. Native Wine
Wayland support is disabled; the runtime uses X11/Xwayland.

## User setup

Verify the published archive SHA256 before extracting it. An archive checksum is
an integrity check, not a substitute for trusting and reviewing its publisher.
After extraction, run these commands inside its root:

```bash
./setup.sh --plan
./setup.sh --target "$HOME/.local/share/quantower-linux" --install
```

The default operation is a read-only plan. It verifies every declared file and
link, checks the target layout, reports the host baseline, and shows the pinned
publisher downloads. `--verify` only audits the extracted bundle. `--stage`
copies the runtime to a new target offline without downloading or running Wine.
`--install` explicitly downloads the checksum-pinned installers into a private
external cache and runs setup in a new prefix. An existing target is never
overwritten. No account, connection or broker agreement is configured for you.
The pinned application is unpacked with bundled `innoextract` rather than its
autorun hooks; setup does not start Quantower. The .NET Desktop Runtime installer
still executes inside the new Wine prefix. Optional `--repair-icons` explicitly
opts in to local icon-font identity changes after clean setup; use it only with
assets you are permitted to modify. Icon changes preserve private backups.
Setup also downloads the locked upstream CEF package and restores missing locale
and ANGLE resources only when its exact CEF engine matches the installed app.
Existing differing resource bytes are refused. It waits for confirmed guarded
setup cleanup before taking the separate inactive-prefix repair lock.

The runtime includes unmodified open DejaVu font families. Fresh-prefix setup
maps common Windows family names such as Verdana to these replacements through
Wine's font-substitution registry. Microsoft font files are not bundled. Text
metrics and layout can differ from Windows. Wine's optional Mono/Gecko downloads
are disabled only during first prefix bootstrap; Microsoft .NET and the app use
the normal DLL selection afterward.

`--profile auto` selects GPU mode only when a host Wayland socket and Vulkan loader
are discoverable; otherwise it selects compatible mode. This prerequisite check
does not prove a GPU is compatible. Choose `--profile compatible` explicitly for
an X11/Xvfb test. Windows installer setup needs a reachable `DISPLAY`, including
when the final app profile uses gamescope/Wayland. A normal desktop Xwayland
session can provide it. `--offline` uses only already-verified installer cache
entries. `--allow-unsupported-host` is an explicit experimental opt-in if baseline
checks fail; it does not make incompatible binaries work.

Use a short installation target for GPU mode: Linux Wayland socket paths
have a 107-byte limit. Setup and doctor report an overlong private socket path
before attempting the compositor. Spaces in a supported path are allowed.

Logs, the new prefix and application files are kept under the new installation,
separate from the downloaded bundle. Run `doctor.py` and `launch.sh` from that
target after setup. Keep the original bundle unchanged for later integrity checks.
Restrictive extraction permissions and a read-only runtime are supported as long
as required owner read/execute access remains and no extra permissions are added.

## Build and package

Build the runtime components in the documented isolated build environment first.
The ordered commands and exact image/package-inventory identities are recorded
in `packaging/build-environment.json`. Both Dockerfiles listed there are required:
the second layer supplies the extraction/X11 helpers and additional font/crypto
build dependencies used by the final image. The Debian base image is pinned by
digest. APT uses signed official repositories; exact installed binary/source
versions and repository metadata hashes are retained, but future APT resolution
can change. This is a recorded build environment, not a claim of identical future
package resolution or bit-for-bit compiler output.
Assemble any required private userspace-library closure under the Wine and
gamescope install prefixes. Do not include host GPU-driver libraries or libc.
Prepare complete per-component notices and a corresponding-source companion using
[BUNDLE-LICENSING.md](BUNDLE-LICENSING.md).

The packaging steps are deliberately separate so the binary/source coverage is
checked against the actual staged payload:

```bash
python3 tools/package-runtime.py stage \
  --wine-home /path/to/wine-prefix \
  --gamescope-home /path/to/gamescope-prefix \
  --x11-home /path/to/matched-x11-libraries \
  --dxvk-d3d9 /path/to/dxvk/x64/d3d9.dll \
  --assert-trace /path/to/libquantower_assert_trace.so \
  --version 0.2.0 --platform debian13-x86_64 \
  --staging /path/to/new-staging

# Generate licenses/manifest.json and source-offer.json as documented in
# BUNDLE-LICENSING.md, covering every staged payload file and symlink.

python3 tools/package-runtime.py seal \
  --staging /path/to/new-staging \
  --source-archive /path/to/corresponding-sources.tar.gz \
  --output /path/to/new-runtime.tar.gz

python3 tools/package-runtime.py verify \
  --archive /path/to/new-runtime.tar.gz
```

Stage requires a new directory and contained relocatable component trees. The
Wine prefix includes the native `bin/innoextract` helper for one-command app
extraction. The gamescope prefix must include `bin/Xwayland`. The X11 input must contain both
`libX11.so.6` and `libX11-xcb.so.1` together at its root or under `lib/`.
Supply the complete install prefix to retain its `share/X11` locale data and
private library closure. An in-tree absolute library link is rewritten
as a relative link; an escaping link or private prefix/app state is rejected.
The source checkout is audited before copying and includes no compiled payload.

Seal normalizes public file modes, checks all payload ownership, notice bytes and
the actual source-companion artifact bytes, then inventories every archive entry.
Its source offer must identify the matching companion at a public HTTPS URL.
Archive ownership is normalized to UID/GID zero with no host user/group names.
Tar order and gzip headers are deterministic for identical inputs and epoch.
The tool writes a new `.tar.gz` plus `.sha256`; existing outputs are preserved.

An optional source-checkout extraction command verifies the expected outer digest
and complete inner inventory before creating a new destination:

```bash
python3 tools/package-runtime.py extract \
  --archive /path/to/runtime.tar.gz \
  --sha256 EXPECTED_ARCHIVE_SHA256 \
  --destination /path/to/new-extracted-runtime
```

Extraction rejects traversal, duplicates, hard links, special files, unexpected
metadata and escaping links. It keeps one open archive description through
verification/extraction and checks it again afterward. Links are created last;
an error removes only the newly created extraction directory. These checks do not
turn untrusted executables into safe software. Review and individually test the
bundle before allowing it to run installers or the application.

## Manual synthetic runtime check

`tests/native/run-bundle-smoke.py` is an explicit integration harness, separate
from the unit suite. Build both Windows architectures of
`native/runtime-probe.cpp` using the pinned build environment. The harness's
default is a read-only plan; `--run` requires a non-root disposable Docker
fixture and `QUANTOWER_ISOLATED_SMOKE=1`.

Mount the reference installation and probes read-only. Create an evidence parent
directory writable by the fixture's UID 1000. Run the harness as the new
container's main command, for example:

```bash
docker run --rm --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges --pids-limit 512 \
  --tmpfs /tmp --tmpfs /test-home:uid=1000,gid=1000,mode=700 \
  --mount type=bind,source=/path/to/toolkit,target=/toolkit,readonly \
  --mount type=bind,source=/path/to/reference-install,target=/installation,readonly \
  --mount type=bind,source=/path/to/probes,target=/probes,readonly \
  --mount type=bind,source=/path/to/evidence-parent,target=/evidence \
  --env QUANTOWER_ISOLATED_SMOKE=1 \
  quantower-toolkit-smoke:debian13-20261105 \
  python3 -B /toolkit/tests/native/run-bundle-smoke.py \
    --base /installation --probe32 /probes/runtime-probe-i386.exe \
    --probe64 /probes/runtime-probe-x86_64.exe --output /evidence/run-01 --run
```

This creates a fresh prefix and Xvfb display under the new evidence run; it
never uses the reference installation's application prefix. It checks both PE
architectures, threaded GDI+/Uniscribe drawing, builtin DLL module evidence,
private Xlib initialization and taskbar minimize/restore of synthetic windows.
The summary contains diagnostic counters, without application performance data
or screenshots. Raw loader logs remain private in the evidence directory.

The harness never performs global Wine cleanup. A deadline failure preserves
the process until the disposable container exits. Do not run it through
`docker exec` in a persistent container: returning from the main container
command is the fixture cleanup boundary. Xvfb checks do not establish GPU,
gamescope, market-data responsiveness or production trading compatibility.
