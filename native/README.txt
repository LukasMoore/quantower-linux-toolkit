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
thread-owned implicit Uniscribe caches, and virtual-desktop taskbar fixes.
Native Wayland is disabled; the integration uses Wine's X11 driver. This is a
new upstream-based source build, excluding older local bitmap/profiling changes.
It is not asserted to reproduce a prior runtime binary or its app behavior.

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
success as a guarantee of every feature. clang/lld and the MinGW headers are
needed for Wine's PE modules. Both i686-w64-mingw32 and x86_64-w64-mingw32 C/C++
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
https://xorg.freedesktop.org/archive/individual/lib/libX11-1.8.14.tar.xz
https://github.com/ValveSoftware/gamescope/tree/3.16.25
https://github.com/ValveSoftware/gamescope/commit/175a67258952ae1c54a85183927b09328d20be2e
