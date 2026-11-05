# Third-party code and dependencies

The root [MIT license](LICENSE) covers original toolkit scripts, tests and
documentation. It does not relicense upstream code, patches derived from that code,
proprietary applications, or separately installed runtime libraries.

| Component | Upstream terms and handling |
| --- | --- |
| Wine-derived patches and native source | LGPL-2.1-or-later where stated in upstream headers; preserve notices, modification history and the included license text |
| gamescope-derived patches | BSD-2-Clause core, with additional BSD-3-Clause notices in the pinned upstream license |
| X.Org libX11-derived patches | Upstream permissive notices; retain the complete pinned `COPYING` text |
| DXVK | zlib license for the pinned upstream runtime; bundled releases include its notice and matching source identity |
| innoextract | Upstream zlib/libpng license with preserved component notices; bundled releases include the exact Debian source package |
| DejaVu fonts | Original Bitstream/DejaVu-specific font notices retained; unmodified open font files and exact Debian sources accompany the runtime. The root MIT license does not relicense these fonts |
| Other private userspace libraries and Xwayland | Exact versions, component licenses, notices and Debian source-package identities are recorded in each runtime bundle |
| Quantower, Windows .NET Desktop Runtime installers and vendor fonts/artwork | Obtained separately from their publishers under applicable terms; these files are excluded from runtime and source bundles |

Pinned source identities and patch paths are in `runtime-lock.json`. Notices and
license texts are kept with the public native patchset under `third_party/`.
Inspect each source header for component-specific terms. Modified source must
identify local changes; do not describe local patches as accepted upstream.

If distributing modified runtime binaries separately, satisfy the actual component
license's source, notice and redistribution conditions for the exact build. The
source repository alone is not a complete corresponding-source offer for an
unrelated binary or an earlier private custom runtime.

Versioned runtime archives carry `licenses/manifest.json`, preserved component
notices and `source-offer.json`. These record the exact payload owners, source
artifact hashes and the companion source archive's name, hash and download URL.
The matching source companion includes the original source archives, local
patches and build recipes for the distributed build, including relevant Debian
source packages. Publish that companion alongside every binary release and
preserve its availability. See [the bundle licensing workflow](docs/BUNDLE-LICENSING.md).
The packager checks inventory and source bytes; it does not determine whether a
new dependency's licensing conditions have been fully satisfied.

Primary references:

- [Wine COPYING.LIB](https://github.com/wine-mirror/wine/blob/wine-11.20/COPYING.LIB)
- [Pinned gamescope license](https://github.com/ValveSoftware/gamescope/blob/17baf4abd1ab3353fb705e4d0d023f84e870f7e8/LICENSE)
- [libX11 upstream COPYING](https://gitlab.freedesktop.org/xorg/lib/libx11/-/blob/master/COPYING)
- [Upstream DXVK license](https://github.com/doitsujin/dxvk/blob/master/LICENSE)
- [Quantower terms](https://www.quantower.com/terms-of-use)

The repository uses neutral text branding and includes no vendor logo, icon-font
files, platform screenshots or application benchmarking reports. Public examples
and regression fixtures must use synthetic data and generic paths.
