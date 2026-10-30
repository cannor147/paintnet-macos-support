# Paint.NET-on-Wine on macOS

Build and install an x86_64 Wine graphics runtime with both MoltenVK (GPU) and
lavapipe/llvmpipe (CPU). Paint.NET uses the software adapter for WARP-like roles,
avoiding the excessive hardware-device allocations observed without it.
The installer disables GPU UI acceleration by default: the tested Wine setup
otherwise opened an unresponsive window. GPU image processing remains available.

## Requirements

- Build target: macOS 15+, x86_64 Wine; Apple Silicon requires Rosetta.
- Tested on Apple M3 Pro / macOS 26.6.1 with Wine 11.20 and Paint.NET 5.2 build9793.
  Other Macs/macOS versions still require runtime testing.
- Installation: existing Wine11.20+ including its Vulkan loader,
  and an official x64 Wine Paint.NET ZIP with its expected SHA256.
- The app wrapper expects Wine's standard `lib/wine/x86_64-unix/wine` loader
  layout (tested with the Gcenx Wine Devel .app).
- Building: Python3.10+, Xcode Command Line Tools, pkg-config and Bison2.7+.
  LLVM builds need several GB of disk space. Wine/Paint.NET are downloaded separately.

## Build

```sh
brew install python@3.14 pkgconf bison
python3.14 -m venv .venv
.venv/bin/python -m pip install -r build/requirements.txt
export PATH="$PWD/.venv/bin:$(brew --prefix bison)/bin:$PATH"
.venv/bin/python build/build.py all --output "$PWD/dist/runtime"
```

The recipe pins and verifies LLVM19.1.7, Mesa26.2.2, DXVK-MacOS3.1.1-macos.1
and its matching MoltenVK. Mesa uses `platforms=macos` for CPU-to-Metal window
presentation and a persistent shader cache. Private libraries are relocated and
signed ad hoc; licenses, checksums and compiler/SDK provenance are bundled.
Pinned inputs make the recipe repeatable, but different Apple SDKs may produce
non-identical binaries. The bundle records its actual minimum macOS version.

## Install and launch

```sh
./install.sh \
  --app-zip /path/to/paint.net.portable.x64.wine.EXPERIMENTAL.zip \
  --app-sha256 EXPECTED_SHA256 \
  --runtime "$PWD/dist/runtime" \
  --wine "/path/to/Wine Devel.app" \
  --dest "$HOME/Library/Application Support/Paint.NET-on-Wine-macOS" \
  --app-bundle "$HOME/Applications/Paint.NET.app"
```

The destination and app bundle must not already exist. Installation checks
archive/runtime integrity and both Vulkan adapters, then creates a fresh prefix.
Existing installations remain available for rollback. Open `Paint.NET.app` from
Applications and keep its icon in the Dock. Finder's Open With and dropping files
on the app icon use the normal file-open path. The icon comes from the supplied
Paint.NET package. A private tiny Wine loader copy gives the running editor the
same app name/icon while sharing the original Wine libraries.
The shell installer uses macOS commands and the prebuilt native app helper;
Python and developer tools are not required to install or run the app.
The helper brands only the private loader's embedded metadata and signs the
result; it does not change the shared Wine installation. The wrapper uses the
normal unsaved-work quit handling rather than killing the editor.
`DEST/Paint.NET.command` remains available for command-line use; diagnostics are
at `DEST/bin/diagnose`. No sudo, global library edits or Gatekeeper changes are made.

`PDN_LLVMPIPE_THREADS` overrides the default4 workers per software device.
`--enable-ui-acceleration` is an experimental installer opt-in.
Logs are in `DEST/logs/launch.log`; `PDN_DXVK_LOG_LEVEL=info` enables adapter details.
There is no automatic memory cap or restart. Verify menus, drawing, effects and
save/reopen yourself; adapter enumeration alone does not establish usability.

Drag-and-drop into the canvas can freeze interaction. Use File → Open / Ctrl+O;
see [upstream issue59](https://github.com/paintdotnet/Paint.NET-on-Wine/issues/59).
Dropping files on the app icon instead bypasses that canvas path.
This package does not fix the upstream canvas path. Experimental Paint.NET packages
expire, so obtain a current official Wine package when installing later.

[Paint.NET-on-Wine](https://github.com/paintdotnet/Paint.NET-on-Wine) already
provides the alternate WARP lookup and `dxvk.deviceFilter` setting used here.
The contribution is macOS build/install integration using existing Mesa WSI and
[matching DXVK/MoltenVK assets](https://github.com/metalsharp/DXVK-MacOS/releases/tag/v3.1.1-macos.1).
