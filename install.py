#!/usr/bin/env python3
"""Install an isolated Paint.NET-on-Wine environment on macOS (no sudo)."""
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile


def run(argv, env=None, timeout=180):
    subprocess.run([str(x) for x in argv], env=env, check=True, timeout=timeout)


def version(value):
    return tuple(int(x) for x in value.split('.')[:2])


def find_wine(value):
    if value:
        candidate = Path(value).expanduser().resolve()
        if candidate.suffix == '.app':
            candidate /= 'Contents/Resources/wine/bin/wine'
    else:
        found = shutil.which('wine')
        if not found:
            raise ValueError('Wine not found; pass --wine /path/to/Wine.app or /path/to/bin/wine')
        candidate = Path(found).resolve()
    if not candidate.is_file() or not os.access(candidate, os.X_OK):
        raise ValueError('Wine executable not found: ' + str(candidate))
    result = subprocess.check_output([str(candidate), '--version'], text=True).strip()
    match = re.search(r'wine-(\d+)\.(\d+)', result)
    if not match or tuple(map(int, match.groups())) < (11, 20):
        raise ValueError('Wine 11.20 or newer required; found ' + result)
    return candidate, result


def extract_app(archive, target):
    with zipfile.ZipFile(archive) as package:
        for entry in package.infolist():
            # The official Windows ZIP uses backslashes in member names.
            name = entry.filename.replace('\\', '/')
            parts = Path(name).parts
            if not name or name.startswith('/') or '..' in parts or ':' in name:
                raise ValueError('Unsafe ZIP member: ' + name)
            out = target.joinpath(*parts)
            if entry.is_dir() or name.endswith('/'):
                out.mkdir(parents=True, exist_ok=True)
            else:
                out.parent.mkdir(parents=True, exist_ok=True)
                with package.open(entry) as source, out.open('wb') as dest:
                    shutil.copyfileobj(source, dest)
    if not (target / 'paintdotnet.exe').is_file():
        raise ValueError('ZIP must contain paintdotnet.exe at its root')
    config = json.loads((target / 'PaintDotNet.Configuration.json').read_text(encoding='utf-8-sig'))
    if config.get('EnableWineMode') is not True:
        raise ValueError('Use the official Wine package with EnableWineMode=true')


def write_launcher(root):
    templates = Path(__file__).resolve().parent / 'scripts'
    (root / 'bin').mkdir()
    for name in ('launch', 'diagnose'):
        shutil.copy2(templates / name, root / 'bin' / name)
        (root / 'bin' / name).chmod(0o755)
    (root / 'Paint.NET.command').write_text('#!/bin/bash\nexec "$(cd "$(dirname "$0")" && pwd)/bin/launch" "$@"\n')
    (root / 'Paint.NET.command').chmod(0o755)


def configure_app(app, enable_ui_acceleration=False):
    settings = app / 'PaintDotNet.AppSettings.json'
    values = json.loads(settings.read_text(encoding='utf-8-sig')) if settings.exists() else {}
    # On the tested macOS Wine stack the default GPU UI path opened a frozen
    # window. CPU UI rendering uses llvmpipe; image rendering may still use GPU.
    values['Graphics/EnableHardwareAccelerationForUI'] = 'True' if enable_ui_acceleration else 'False'
    settings.write_text(json.dumps(values, indent=2) + '\n', encoding='utf-8-sig')


def verify_runtime(runtime):
    sums = runtime / 'SHA256SUMS'
    entries = sums.read_text().splitlines()
    if not entries:
        raise ValueError('Runtime checksum list is empty')
    covered = set()
    for line in entries:
        digest, name = line.split(None, 1)
        name = name.lstrip('*')
        path = runtime / name
        if not path.resolve().is_relative_to(runtime.resolve()):
            raise ValueError('Unsafe runtime checksum path')
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('Runtime checksum mismatch: ' + name)
        covered.add(name)
    actual = {p.relative_to(runtime).as_posix() for p in runtime.rglob('*') if p.is_file() and p != sums}
    if actual != covered:
        raise ValueError('Runtime checksum list does not cover exactly the bundle files')


def commit_directory(stage, destination):
    # Darwin RENAME_EXCL: atomic commit that never replaces another installation.
    libc = ctypes.CDLL(None, use_errno=True)
    rename = libc.renamex_np
    rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(os.fsencode(stage), os.fsencode(destination), 0x00000004):
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(destination))


def cleanup_stage(stage, wine):
    if (stage / 'wineprefix').exists():
        env = os.environ.copy()
        env['WINEPREFIX'] = str(stage / 'wineprefix')
        try:
            subprocess.run([str(wine.parent / 'wineserver'), '-k'], env=env,
                           capture_output=True, timeout=10)
            subprocess.run([str(wine.parent / 'wineserver'), '-w'], env=env,
                           capture_output=True, timeout=30, check=True)
        except subprocess.SubprocessError:
            print('Could not stop staged Wine; retaining failed installation at ' + str(stage), file=sys.stderr)
            return
    shutil.rmtree(stage)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app-zip', required=True, type=Path)
    parser.add_argument('--app-sha256', required=True, help='Expected checksum from the selected official release')
    parser.add_argument('--runtime', required=True, type=Path, help='Directory produced by build/build.py')
    parser.add_argument('--wine', help='Existing Wine .app or wine executable (11.20+)')
    parser.add_argument('--dest', type=Path, default=Path.home() / 'Library/Application Support/Paint.NET-on-Wine-macOS')
    parser.add_argument('--launcher', type=Path, help='Optional new .command path; existing files are never overwritten')
    parser.add_argument('--check-only', action='store_true')
    parser.add_argument('--enable-ui-acceleration', action='store_true',
                        help='Experimental: enable GPU UI rendering (froze the tested macOS Wine setup)')
    args = parser.parse_args()
    if platform.system() != 'Darwin':
        parser.error('This installer supports macOS only')
    if platform.machine() == 'arm64':
        run(['/usr/bin/arch', '-x86_64', '/usr/bin/true'])
    dest = args.dest.expanduser().resolve()
    runtime = args.runtime.expanduser().resolve()
    if dest.exists():
        parser.error('--dest already exists; choose a new path to preserve the previous installation')
    if args.launcher and os.path.lexists(args.launcher.expanduser()):
        parser.error('--launcher already exists; choose a new path')
    expected = args.app_sha256.lower()
    if not re.fullmatch('[0-9a-f]{64}', expected):
        parser.error('--app-sha256 must be a 64-character SHA256 digest')
    actual = hashlib.sha256(args.app_zip.read_bytes()).hexdigest()
    if actual != expected:
        parser.error('Paint.NET archive checksum mismatch')
    manifest = json.loads((runtime / 'versions.json').read_text())
    verify_runtime(runtime)
    minimum = manifest['minimum_macos']
    if version(platform.mac_ver()[0]) < version(minimum):
        parser.error('This runtime requires macOS ' + minimum + ' or newer')
    wine, wine_version = find_wine(args.wine)
    for name in ('wineboot', 'winecfg', 'wineserver'):
        if not os.access(wine.parent / name, os.X_OK):
            parser.error('Wine helper missing: ' + name)
    loader = wine.parent.parent / 'lib/libvulkan.dylib'
    for required in ('lib/libvulkan_lvp.dylib', 'lib/libMoltenVK.dylib', 'bin/vulkan-probe',
                     'dxvk/dxgi.dll', 'dxvk/d3d11.dll', 'dxvk/d3d10core.dll'):
        if not (runtime / required).is_file():
            parser.error('Runtime missing ' + required)
    if not loader.is_file():
        parser.error('Wine must include the Vulkan loader at ' + str(loader))
    print('Wine:', wine_version, '\nRuntime:', runtime, '\nDestination:', dest, flush=True)
    if args.check_only:
        return
    # Stage on the destination filesystem, then atomically rename after setup.
    dest.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.paintnet-install-', dir=dest.parent))
    try:
        shutil.copytree(runtime, stage / 'runtime')
        extract_app(args.app_zip, stage / 'app')
        configure_app(stage / 'app', args.enable_ui_acceleration)
        (stage / 'wine-path').write_text(str(wine) + '\n')
        write_launcher(stage)
        # Verify adapters BEFORE creating the prefix or launching the editor.
        run([stage / 'bin/diagnose'], timeout=180)
        env = os.environ.copy()
        env.update(WINEPREFIX=str(stage / 'wineprefix'), WINEDEBUG='-all', WINEDLLOVERRIDES='mshtml=')
        run([wine.parent / 'wineboot', '--init'], env)
        run([wine.parent / 'winecfg', '-v', 'win11'], env)
        system32 = stage / 'wineprefix/drive_c/windows/system32'
        for name in ('dxgi.dll', 'd3d11.dll', 'd3d10core.dll'):
            shutil.copy2(stage / 'runtime/dxvk' / name, system32 / name)
        # Keep the app's alternate WARP lookup enabled and expose CPU devices.
        with (stage / 'app/dxvk.conf').open('a') as config:
            config.write('\n# macOS: expose llvmpipe alongside hardware Vulkan adapters\ndxvk.deviceFilter = " "\n')
        (stage / 'install-receipt.json').write_text(json.dumps({
            'app_archive_sha256': actual, 'wine': wine_version,
            'host_macos': platform.mac_ver()[0], 'runtime': manifest,
            'ui_hardware_acceleration': args.enable_ui_acceleration}, indent=2))
        run([wine.parent / 'wineserver', '-k'], env)
        run([wine.parent / 'wineserver', '-w'], env)
        commit_directory(stage, dest)
        if args.launcher:
            launcher = args.launcher.expanduser()
            launcher.parent.mkdir(parents=True, exist_ok=True)
            import shlex
            with launcher.open('x') as output:
                output.write('#!/bin/bash\nexec ' + shlex.quote(str(dest / 'bin/launch')) + ' "$@"\n')
            launcher.chmod(0o755)
        print('Installed. Launch:', dest / 'Paint.NET.command', flush=True)
    finally:
        if stage.exists():
            cleanup_stage(stage, wine)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        sys.exit('Installation failed: ' + str(error))
