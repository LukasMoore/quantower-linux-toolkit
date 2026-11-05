# Third-party code and dependencies

The root [MIT license](LICENSE) covers original toolkit scripts, tests and
documentation. It does not relicense upstream code, patches derived from that code,
proprietary applications, or separately installed runtime libraries.

| Component | Upstream terms and handling |
| --- | --- |
| Wine-derived patches and native source | LGPL-2.1-or-later where stated in upstream headers; preserve notices, modification history and the included license text |
| gamescope-derived patches | BSD-2-Clause core, with additional BSD-3-Clause notices in the pinned upstream license |
| X.Org libX11-derived patches | Upstream permissive notices; retain the complete pinned `COPYING` text |
| DXVK, if supplied locally | Use the license of the exact runtime/fork obtained by the user; no DXVK binary is published here |
| Quantower, Windows .NET Desktop Runtime and vendor fonts/artwork | User-obtained proprietary dependencies; no redistribution grant is made by this project |

Pinned source identities and patch paths are in `runtime-lock.json`. Notices and
license texts are kept with the public native patchset under `third_party/`.
Inspect each source header for component-specific terms. Modified source must
identify local changes; do not describe local patches as accepted upstream.

If distributing modified runtime binaries separately, satisfy the actual component
license's source, notice and redistribution conditions for the exact build. The
source-only repository is not a complete corresponding-source offer for an
unrelated binary or an earlier private custom runtime.

Primary references:

- [Wine COPYING.LIB](https://github.com/wine-mirror/wine/blob/wine-11.20/COPYING.LIB)
- [Pinned gamescope license](https://github.com/ValveSoftware/gamescope/blob/17baf4abd1ab3353fb705e4d0d023f84e870f7e8/LICENSE)
- [libX11 upstream COPYING](https://gitlab.freedesktop.org/xorg/lib/libx11/-/blob/master/COPYING)
- [Upstream DXVK license](https://github.com/doitsujin/dxvk/blob/master/LICENSE)
- [Quantower terms](https://www.quantower.com/terms-of-use)

The repository uses neutral text branding and includes no vendor logo, icon-font
files, platform screenshots or application benchmarking reports. Public examples
and regression fixtures must use synthetic data and generic paths.
