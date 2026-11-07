# Original WPF/CefSharp fixture

`WpfCefFixture.cs` is original MIT source containing its own WPF controls and inline
HTML/canvas. It contains no vendor UI, signed-application modification, startup
hook or application inspection. It leaves GPU rendering available and uses the
existing full-update WPF switch for the GPU candidate.

The compile-only driver requires existing Linux .NET/Roslyn, official Windows
Microsoft shared-framework assemblies and matching public CefSharp dependencies.
It does not download or copy dependencies, execute Wine, connect to a display or
modify a prefix. Keep output in a new private directory outside source/prefixes:

```bash
python3 tests/native/integrated-windows/wpf-cef-build.py \
  --dotnet /absolute/compiler-host/dotnet \
  --compiler /absolute/roslyn/csc.dll \
  --framework-root /absolute/windows-dotnet/shared \
  --framework-version 10.0.13 \
  --cefsharp-home /absolute/matching-public-cef-dependencies \
  --output /absolute/private/new-managed-build
```

`compile.log` preserves compiler warnings. Older CefSharp dependency references can
produce framework-identity compatibility warnings; compilation does not establish
runtime compatibility. The private build report records input/output hashes.

Run only through a reviewed disposable-prefix fixture harness using the exact
candidate Wine/private-Xlib/DXVK closure and explicit X11 route. Do not point it at
the application prefix or clone a running prefix. Provide existing matching public
CEF native/resource dependencies and official Windows .NET runtime in that
separate environment; never copy vendor HTML, assemblies or settings as fixtures.
The fixture's three arguments are its explicit CEF-dependency directory, a NEW
private result directory and `unaware`, `system` or `pmv2` for its own process.
It requests that context before WPF initialization and records whether the API
accepted it plus the actual HWND context and DPI. No vendor context is changed.

The bounded self-test checks:

- A known WPF visual-tree marker pixel and a programmatic WPF callback counter.
- Three complete own CEF window create/load/close cycles with original HTML.
- A CEF-host mouse event followed by its actual HTML click-counter callback.
- Known independent green/magenta canvas pixels through browser readback.
- Window minimize/restore plus actual HWND rectangles, awareness/DPI and WPF-to-device
  transforms, with units identified in the private report.

Its 35-second timer requests a normal own-application shutdown on deadline. The
fixture does not force-kill anything, retry launch, change host monitor settings,
operate vendor actions or manipulate the host pointer. The caller must keep a
surviving failed fixture explicit and apply the existing ownership guards rather
than automatically restarting it.

Results distinguish visual-tree/DOM/canvas evidence from final composed window
pixels. The WPF callback stimulus is programmatic; CEF input is local to the own
browser host. These checks do not prove physical input, monitor crossing, focus
with Linux applications, host taskbar recovery, mixed-DPI behavior, proprietary
panel actions or GPU stability. Complete those controlled demo acceptance gates
separately and preserve any failed/refused trials as evidence.

`managed-run-container.py` is the corresponding opt-in networkless/non-root
container runner. It requires only the explicitly mounted runtime/public dependency
inputs shown in its `/runtime` paths, compiled own fixture under `/probe`, source
under `/source` and a new private writable `/test` directory. It starts its own
Xvfb and fresh prefix, records exact own process birth/executable/backend evidence,
and runs 96 DPI by default. `QTK_FIXTURE_DPI=192` is a separate explicit disposable
trial after the 96 baseline. The runner has no GPU-device mount setup; record the
actual rendering device separately. Container software graphics success/failure
cannot establish NVIDIA/host-Xwayland behavior. Do not disable GPU rendering or
remove a canvas assertion to manufacture a pass.
