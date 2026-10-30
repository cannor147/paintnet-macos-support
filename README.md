# Paint.NET for macOS

Install Paint.NET as a macOS app with its own icon and Dock shortcut.

## Requirements

- macOS 15+; Apple Silicon requires Rosetta. Tested on an M3 Pro with macOS 26; Intel Macs and older macOS versions are not yet verified.
- [Wine Devel 11.20+](https://github.com/Gcenx/macOS_Wine_builds/releases).
- The **x64 Wine ZIP** from [Paint.NET-on-Wine](https://github.com/paintdotnet/Paint.NET-on-Wine/releases) and its SHA256.

## Install

Download and unpack the [ready-to-install release](https://github.com/cannor147/paintnet-macos-support/releases/tag/v0.1.0).
Run from the unpacked `paintnet-macos-support` folder, using the SHA256 published for your Paint.NET ZIP:

```sh
./install.sh \
  --app-zip /path/to/paint.net.portable.x64.wine.EXPERIMENTAL.zip \
  --app-sha256 EXPECTED_SHA256 \
  --runtime "$PWD/runtime" \
  --wine "/path/to/Wine Devel.app"
```

Open **`~/Applications/Paint.NET.app`** and keep it in the Dock.
No Python, Homebrew or developer tools are needed for installation.
Existing installations are preserved; use `--dest` and `--app-bundle` to choose new paths.

<details>
<summary>Build from source (developers)</summary>

Requires Homebrew and Xcode Command Line Tools. Run from a source checkout:

```sh
brew install python@3.14 pkgconf bison
python3.14 -m venv .venv
.venv/bin/python -m pip install -r build/requirements.txt
export PATH="$PWD/.venv/bin:$(brew --prefix bison)/bin:$PATH"
.venv/bin/python build/build.py all
```

The first build takes time and needs several GB of free space.
Use `--runtime "$PWD/dist/runtime"` when installing a source-built runtime.

</details>
