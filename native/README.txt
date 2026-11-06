Native source components

The public patch series starts from pinned official upstream sources. It includes
no Quantower binaries, fonts, markup, account data, settings, application traces,
screenshots or application performance comparisons.

tools/build-runtime.py fetches only SHA256-pinned HTTPS archives, refuses archive
links/path traversal, and applies hash-pinned patches with zero context fuzz.
It builds under a new work directory and stages installation with DESTDIR. It
never starts Wine/the app, modifies an existing prefix, installs system packages,
or overwrites an existing build directory. --offline uses the verified cache;
--prepare-only checks the sources and patch series without compiling.

Examples from the repository root:
  python3 tools/build-runtime.py --component all --check-deps
  python3 tools/build-runtime.py --component x11 --work-dir ../x11-build
  python3 tools/build-runtime.py --component wine --work-dir ../wine-build
  python3 tools/build-runtime.py --component gamescope --work-dir ../gamescope-build

Wine 11.20 uses modern WoW64: a 64-bit Unix host with both i386 and x86_64 PE
modules (--enable-archs=i386,x86_64), supporting common 32-bit installer bootstraps
as well as the 64-bit app. It includes the .NET AMD64 suspension fallback,
expected XInput button-map error recovery, GDI+ call-local text resources,
thread-owned implicit Uniscribe caches, virtual-desktop taskbar fixes, and separate
unsnapped pointer tracking for application-adjusted move/resize rectangles.
Native Wayland is disabled; the integration uses Wine's X11 driver. This is a
new upstream-based source build, excluding older local bitmap/profiling changes.
It is not asserted to reproduce a prior runtime binary or its app behavior.
The runtime build disables compilation of uninstalled upstream Wine test binaries;
separate synthetic runtime probes and fresh-prefix integration tests are retained.
The Debian 13 bundle configures GnuTLS, Fontconfig/FreeType, ALSA/PulseAudio,
XInput2, OpenGL and Vulkan support. Its configure summary reports no OpenCL,
smart-card, Unix VA/FFmpeg-library, OSSv4, ISDN or Samba NetAPI integration.
UnixODBC was not found; its fallback libodbc.so SONAME is explicitly recorded
as unavailable rather than treated as a collected library. Gettext tools
are absent, so translations are not rebuilt. These optional omissions do not
establish compatibility for workloads that require them; inspect the retained
configure summary when building for a different application.

The portable bundle also carries twelve unchanged DejaVu TrueType faces: four
styles each of Sans, Serif and Sans Mono. Wine's load_file_system_fonts() scans
its share/wine/fonts data directory, so these faces do not depend on host font
installation. Fresh-prefix setup uses Wine's FontSubstitutes registry support
for Verdana, Arial, Microsoft Sans Serif, Segoe UI, Times New Roman, Courier New
and Consolas. It also sets those names under the prefix's HKCU Software/Wine/
Fonts/Replacements key, which Wine DirectWrite reads for system collections;
GDI FontSubstitutes alone does not provide DirectWrite family aliases. These
resolve to open DejaVu families; their metrics differ from
the Microsoft fonts. Existing Wine Tahoma and the legacy raster MS Sans Serif
behavior remain in place. A separate no-app fixture with empty host Fontconfig
verified GDI+ family lookup and all four styles for these seven aliases.
An additional DirectWrite check confirmed the names were absent with GDI-only
aliases, then present with successful style matches and text layouts after the
DirectWrite replacements were registered. No host fonts were available in it.

The Xlib 1.8.14 correction moves sequence synchronization before final cached
resource-ID replenishment. Existing assertions and lock/error paths remain.
gamescope 3.16.25 includes the upstream static-black-buffer correction and a local
Vulkan resource shutdown correction. It preserves DRM, nested Wayland, PipeWire
and input emulation. OpenVR, SDL, AVIF screenshots and the Steam WSI layer are
disabled. Required vendored source archives are pinned; meson may not download
missing fallbacks. libdisplay-info and other host build dependencies are explicit.

Prerequisites (Debian-family names; install using your distribution's package
manager after reviewing its available versions):

Common: build-essential, pkg-config, python3, patch.
Wine: bison, flex, clang, lld, mingw-w64, gcc-mingw-w64-i686,
gcc-mingw-w64-x86-64, g++-mingw-w64-i686, g++-mingw-w64-x86-64,
libfreetype-dev, libfontconfig-dev,
libx11-dev, libxext-dev, libxrender-dev, libxrandr-dev, libxi-dev, libxcursor-dev,
libxinerama-dev, libxxf86vm-dev, libvulkan-dev, libgl-dev, libgnutls28-dev,
libasound2-dev, libpulse-dev, libdbus-1-dev, libudev-dev. Wine configure reports
optional missing support; examine its summary instead of treating configure
success as a guarantee of every feature. A supported PE cross-toolchain and
the MinGW headers are needed for Wine's PE modules. The Debian bundle uses
GCC 14 MinGW cross-compilers. Both i686-w64-mingw32 and x86_64-w64-mingw32 C/C++
cross-compilers are required; llvm-mingw target compilers are also accepted.
Modern WoW64 does not require 32-bit Linux multilib development libraries.
The tool does not install a Windows .NET runtime. Executable prerequisites and
Meson >=1.3 are checked before downloads; configure checks the actual headers,
libraries and compiler capabilities afterward.
Xlib: libxcb1-dev, xtrans-dev, x11proto-dev, xorg-sgml-doctools.
gamescope: meson, ninja-build, cmake, libpipewire-0.3-dev, hwdata, libwayland-dev,
wayland-protocols, libvulkan-dev, libdrm-dev, libx11-xcb-dev, libxdamage-dev,
libxcomposite-dev, libxcursor-dev, libxxf86vm-dev, libxtst-dev, libxres-dev,
libxmu-dev, libxkbcommon-dev, libcap-dev, libpixman-1-dev, liblcms2-dev,
libseat-dev, libinput-dev, libdisplay-info-dev, libdecor-0-dev, libeis-dev,
libxfixes-dev, libxrender-dev, libxext-dev, libxi-dev, libudev-dev,
libxcb-composite0-dev, libxcb-ewmh-dev, libxcb-icccm4-dev, libxcb-res0-dev,
libxcb-render-util0-dev, glslang-tools, libluajit-5.1-dev, xwayland. Consult the
pinned upstream meson files for minimum versions; older distributions may need
newer development dependencies. These are host-ABI builds, not universal binaries.

Optional assertion tracing

  python3 native/build-assert-trace.py --output ../native-build/libquantower_assert_trace.so

The original MIT source interposes only glibc __assert_fail. It takes one bounded
native PC/module-basename/symbol trace for selected Xlib assertions, then calls
the original fatal assertion. It does not suppress the assertion, install signal
handlers or dump environment, assertion text, file names, application strings or
memory. The destination must be a private current-user directory (mode 0700).
The launcher enables it only when the installed runtime manifest pins its hash.
It is not a full crash dump facility and does not capture all managed exceptions.
tests/test_runtime_build.py exercises a synthetic child, never the live app.

Synthetic Windows probes

  python3 native/build-runtime-probes.py --output-dir ../runtime-probes

This compiles original MIT probes for both Windows architectures and records
source/binary hashes, compiler versions and audited DLL imports. The builder
does not execute them. Run them only in your isolated test prefix using the
runtime being evaluated. The default probe renders synthetic GDI+ text and
runs four concurrent Arabic/Hebrew Uniscribe workers with cold per-run caches.
It reports JSON booleans, counts, text metrics and a pixel checksum. Its expected
result is passed=true, 2048 analysed strings, matched positive shaped/placed
counts and zero unexpected errors. The checksum depends on the test fonts;
it is not a platform-independent reference image or a performance benchmark.
The optional --desktop AMP-Quantower --taskbar mode tests only its own fixture
windows and their linked Wine shell button. The probe's compiled startup/runtime
code requires the matching MinGW/GCC notices; integration PE files are not part
of the source checkout or prebuilt runtime payload.

The separate font-probe.cpp checks only the seven setup aliases above and Wine
Tahoma, each with regular, bold, italic and bold-italic styles. It prints fixed
requested names and GDI+ status codes as JSON, exits nonzero on a missing family
(status 14) or style, and never prints a user's font inventory or starts the
application. Run it manually after setting up a NEW isolated test installation;
the setup's prefix-scoped aliases must already be registered. Example commands
from the source checkout (use new private paths):

  TOOLKIT_FONT_PROBE_BUILD=/absolute/path/to/new-private-probe-directory
  mkdir -m 700 "$TOOLKIT_FONT_PROBE_BUILD"
  x86_64-w64-mingw32-g++ -std=gnu++17 -O2 -Wall -Wextra -Werror \
    -fno-exceptions -fno-rtti -static-libgcc -static-libstdc++ \
    -Wl,--no-insert-timestamp native/font-probe.cpp \
    -o "$TOOLKIT_FONT_PROBE_BUILD/font-probe.exe" -lgdiplus -lgdi32 -luser32

  TOOLKIT_FONT_TEST_BASE=/absolute/path/to/new-test-installation
  WINEPREFIX="$TOOLKIT_FONT_TEST_BASE/prefix" \
    LD_LIBRARY_PATH="$TOOLKIT_FONT_TEST_BASE/runtime-components/x11:$TOOLKIT_FONT_TEST_BASE/runner-quantower/opt/wine-devel/lib" \
    XLOCALEDIR="$TOOLKIT_FONT_TEST_BASE/runtime-components/x11/share/X11/locale" \
    "$TOOLKIT_FONT_TEST_BASE/runner-quantower/opt/wine-devel/bin/wine" \
    "$TOOLKIT_FONT_PROBE_BUILD/font-probe.exe"

This needs a reachable test DISPLAY. Success reports passed=true and zero family
and style statuses for all eight fixed names. The compiled probe remains private;
the same MinGW/GCC startup-runtime notice terms apply if it is redistributed.

Window snapping regression

The source build recipe includes Wine patch 0006 for incremental move/resize
tracking. WM_MOVING and WM_SIZING callbacks may change the proposed rectangle.
When a callback snaps an edge and each subsequent mouse delta starts from that
snapped rectangle, a slow drag can repeatedly lose its accumulated distance.
The correction updates a separate tracking rectangle and continues applying the
callback's rectangle to the window. Snapping remains enabled.

See ../docs/WINDOW-SNAPPING.md and
../tests/native/window-snapping/README.md for the original source-only fixtures,
compile commands and isolated fresh-prefix container runs. Initial validation
accepted 169 candidate geometry cases: 61 for each of two Wine runtime variants,
nine snap-retention endpoints and 38 child/outline/keyboard cases at 96 DPI,
using 64-bit Windows probes.
Invalid historical injection attempts are excluded. This is synthetic native
correctness evidence, with no application workload or performance measurement.
Actual physical corner dragging in the application still needs individual testing;
a nested compositor prevented reliable automated cursor placement during that check.

This correction is included in the current source recipe. The v0.2.0 prebuilt
runtime and source-companion assets remain the original release. Build a fresh
runtime and test installation to evaluate the later patch; do not assume that
extracting the older bundle installs it or that a module from another Wine build
can be copied safely into an existing loaded runtime.

Validation boundaries

The source preparation checks and offline unit tests establish archive integrity,
path safety and exact patch application. Synthetic native tests can exercise their
specific regression. A successful build alone does not establish compatibility
with every GPU, distribution, Windows app, font, language or chart workload.
The local patches are not represented as upstream-accepted releases. No platform
benchmark or production-trading suitability claim is included.

Primary references:
https://gitlab.winehq.org/wine/wine/-/wikis/Building-Wine
https://github.com/wine-mirror/wine/tree/wine-11.20
https://github.com/wine-mirror/wine/blob/wine-11.20/dlls/win32u/font.c
https://github.com/wine-mirror/wine/blob/wine-11.20/dlls/gdiplus/font.c
https://github.com/wine-mirror/wine/blob/wine-11.20/dlls/dwrite/font.c
https://xorg.freedesktop.org/archive/individual/lib/libX11-1.8.14.tar.xz
https://github.com/ValveSoftware/gamescope/tree/3.16.25
https://github.com/ValveSoftware/gamescope/commit/175a67258952ae1c54a85183927b09328d20be2e

Window tracking references:
https://github.com/wine-mirror/wine/blob/wine-11.20/dlls/win32u/defwnd.c
https://learn.microsoft.com/en-us/windows/win32/winmsg/wm-sizing
https://learn.microsoft.com/en-us/windows/win32/winmsg/wm-moving
