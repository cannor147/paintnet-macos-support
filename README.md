# Paint.NET for macOS

Install Paint.NET as a macOS app with its own icon and Dock shortcut.

## Requirements

- macOS 15+; Apple Silicon requires Rosetta. Tested on an M3 Pro with macOS 26.
- [Wine Devel 11.20+](https://github.com/Gcenx/macOS_Wine_builds/releases).
- The **x64 Wine ZIP** from [Paint.NET-on-Wine](https://github.com/paintdotnet/Paint.NET-on-Wine/releases) and its SHA256.

## Build

Requires Homebrew and Xcode Command Line Tools. Run from the repository directory:

```sh
brew install python@3.14 pkgconf bison
python3.14 -m venv .venv
.venv/bin/python -m pip install -r build/requirements.txt
export PATH="$PWD/.venv/bin:$(brew --prefix bison)/bin:$PATH"
.venv/bin/python build/build.py all
```

The first build takes time and needs several GB of free space.
Python is only needed for building.

## Install

Use the SHA256 published for the downloaded Paint.NET ZIP:

```sh
./install.sh \
  --app-zip /path/to/paint.net.portable.x64.wine.EXPERIMENTAL.zip \
  --app-sha256 EXPECTED_SHA256 \
  --runtime "$PWD/dist/runtime" \
  --wine "/path/to/Wine Devel.app"
```

Open **`~/Applications/Paint.NET.app`** and keep it in the Dock.

Existing installations are preserved. To install another copy, choose new paths
with `--dest` and `--app-bundle`. See `./install.sh --help` for options.
