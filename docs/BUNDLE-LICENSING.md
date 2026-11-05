# Runtime bundle notices and corresponding source

The binary bundle has several component licenses. The toolkit's root MIT license
covers its original scripts; it does not replace Wine's LGPL or any bundled
dependency's copyright and license conditions. Review the actual source headers,
copyright notices and build configuration for every release.

Publish the matching source-companion archive beside the binary archive. Include
the exact upstream source archives, every local patch, the build/installation
scripts, source lock files and build-environment/package versions. For copied
Debian libraries, include each exact source package's `.dsc` and all tar/diff
artifacts referenced by its `Checksums-Sha256` field. The Debian source version may
differ from the binary version after a binary-only rebuild.

The source archive is actual accompanying source. The generated `source-offer.json`
is an inventory and download pointer, not a stand-alone written offer or a promise
that an unreviewed collection automatically meets every license condition. Keep
the source companion available with the binary release.

## Collect Debian dependency provenance

Record copied dependency origins as a JSON list:

```json
[
  {
    "path": "payload/wine/lib/libexample.so.1",
    "system_path": "/usr/lib/x86_64-linux-gnu/libexample.so.1"
  }
]
```

The `files` list in a `collect-runtime-libs.py` report is also accepted directly.

Use an exported build root containing its dpkg database and package notices. Fetch
source packages for the exact `source:Package` and `source:Version` values using
Debian's signed archive metadata; retain their `.dsc` and referenced files.

Inside the isolated builder container, `--rootfs /` uses that exact image's package
database and notices without exporting a separate root filesystem. Run:

```bash
python3 tools/collect-debian-notices.py \
  --rootfs /path/to/exported-build-root \
  --files-json /path/to/copied-dependencies.json \
  --debian-sources /path/to/exact-source-packages \
  --output /path/to/new-notice-inputs
```

This is offline and read-only to the build root. It resolves symlinks within that
root, queries exact package ownership and binary/source versions, copies complete
package copyright notices plus referenced common-license texts, and verifies
source files against each `.dsc`. Mapping entries may include `sha256`; when
present, the copied dependency must match its package input. Known equivalent
GPL/LGPL `.0` filename spellings are resolved to Debian's full license filename,
with the original copyright reference retained in the manifest. It emits
`components.json` and pinned notice files. `LicenseRef-Debian-copyright` identifies the accompanying authoritative
notice; it is not a substitute interpretation of all its terms. Review the recorded
license labels and notices before distribution.

## Validate a staged binary bundle

The inventory has `schema_version: 1`, an optional `source_archive_root`, and a
`components` list. Each component declares:

- `id`, `name`, `version` and reviewed `license` metadata;
- `covers`: exact payload filenames or directory prefixes ending with `/`;
- `notices`: `{path, sha256}` entries relative to the notice-input root;
- `sources` and `recipes`: `{path, sha256}` entries in the source archive;
- for Debian components, `debian_source: {package, version, dsc}`.

When Debian-packaged compiler/runtime code is statically incorporated into another
component, attach its notices and source artifacts to that component and record
`incorporated_debian_sources: [{package, version, dsc}]`. These source controls and
their complete artifact sets receive the same validation as copied shared libraries.

The most-specific cover wins, so a copied Debian library can override a broad
Wine directory declaration. Equal-specificity ownership conflicts and uncovered
payload files are rejected. Include static/vendored code notices and source too;
dynamic-library enumeration alone does not discover every incorporated component.

After creating the source companion and staging the binary payload, run:

```bash
python3 tools/bundle-notices.py \
  --root /path/to/staged-binary-bundle \
  --inventory /path/to/component-inventory.json \
  --notice-root /path/to/notice-inputs \
  --source-archive /path/to/runtime-sources.tar.gz \
  --output /path/to/staged-binary-bundle/licenses \
  --source-url https://example.org/releases/runtime-sources.tar.gz
```

The validator reads required source bytes from the actual companion archive and
checks their hashes; it does not accept a URL list as proof of source availability.
Debian source control identity and its complete SHA256 artifact set are checked.
It writes `licenses/manifest.json` with `payload_owners` and `source-offer.json`
with the source archive's filename, byte size, SHA256 and optional public URL.
The packager then includes these files in its complete integrity manifest.

Host glibc/loader, kernel and GPU drivers are documented platform requirements,
not copied from the development machine. Quantower, Microsoft Windows runtime
installers, account state and vendor font assets are outside this dependency
bundle; explicit setup obtains permitted installers from their publishers.

## Official DXVK binary and matching source inputs

`packaging/dxvk-lock.json` pins the official unmodified DXVK release archive, its
selected x64 D3D9 DLL, the matching source commit, and recursively referenced source
submodules. It also pins the source notice files. Maintainers can prepare these
inputs without executing the DLL:

```bash
python3 tools/fetch-dxvk.py \
  --cache /path/to/source-cache \
  --output /path/to/new-dxvk-output
```

Use `--offline` to require all pinned archives in the cache. Include the main source
archive and all `extra_sources` in the source companion, and include all recorded
notices. The source tag archive alone omits its git submodules. This recipe selects
only `x64/d3d9.dll`; it is not a claim to have reproduced upstream's release binary
with this build environment.

## Primary references

- [Wine LGPL-2.1 text](https://github.com/wine-mirror/wine/blob/wine-11.20/COPYING.LIB)
- [Debian source-package composition](https://www.debian.org/doc/debian-policy/ch-source.html)
- [Debian copyright policy](https://www.debian.org/doc/debian-policy/ch-docs.html#copyright-information)
- [Debian machine-readable copyright format](https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/)
- [Pinned gamescope license](https://github.com/ValveSoftware/gamescope/blob/17baf4abd1ab3353fb705e4d0d023f84e870f7e8/LICENSE)
- [DXVK v3.1.1 license](https://github.com/doitsujin/dxvk/blob/v3.1.1/LICENSE)

Mechanical checks support maintainer review; they do not establish rights to
redistribute arbitrary inputs or prove that every licensing obligation is met.
