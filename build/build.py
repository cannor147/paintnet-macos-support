#!/usr/bin/env python3
"""Pinned, relocatable x86_64 lavapipe/DXVK runtime for macOS (Python >=3.10).

Build tools: python3 -m venv .venv && .venv/bin/pip install -r build/requirements.txt
Add .venv/bin to PATH, install pkg-config and Bison >=2.7, then run build.py all.
This is a reproducible recipe, not a promise of byte-identical output across SDKs.
"""
import argparse
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

HERE = Path(__file__).resolve().parent
MIN_MACOS = '15.0'
ASSETS = {
    'mesa': {
        'version': '26.2.2', 'file': 'mesa-26.2.2.tar.xz',
        'url': 'https://archive.mesa3d.org/mesa-26.2.2.tar.xz',
        'sha256': 'eeb29ca7e56cfaa8e8a79538dcf834e3b18e501c31bef5145e959ea437cc4216',
        'checksum_source': 'https://docs.mesa3d.org/relnotes/26.2.2.html',
        'directory': 'mesa-26.2.2',
    },
    'llvm': {
        'version': '19.1.7', 'file': 'llvm-project-19.1.7.src.tar.xz',
        'url': 'https://github.com/llvm/llvm-project/releases/download/llvmorg-19.1.7/llvm-project-19.1.7.src.tar.xz',
        'sha256': '82401fea7b79d0078043f7598b835284d6650a75b93e64b6f761ea7b63097501',
        'checksum_source': 'https://github.com/Homebrew/homebrew-core/blob/main/Formula/l/llvm%4019.rb',
        'directory': 'llvm-project-19.1.7.src',
    },
    'dxvk': {
        'version': '3.1.1-macos.1', 'file': 'dxvk-macos-v3.1.1-macos.1-x86_64.tar.zst',
        'url': 'https://github.com/metalsharp/DXVK-MacOS/releases/download/v3.1.1-macos.1/dxvk-macos-v3.1.1-macos.1-x86_64.tar.zst',
        'sha256': '504ed79f9775ff944d4c9808ad20c45d634f6be074091e17ceb37c0a66e14726',
        'checksum_source': 'https://api.github.com/repos/metalsharp/DXVK-MacOS/releases/tags/v3.1.1-macos.1',
        'directory': 'dxvk-macos-v3.1.1-macos.1-x86_64',
    },
    'moltenvk': {
        'version': '1.4.3', 'release_tag': 'v3.1.1-macos.1',
        'source_commit': '8b62043bca33ab6044f542440211fb748ee59ccc',
        'file': 'moltenvk-v3.1.1-macos.1-universal.tar.zst',
        'url': 'https://github.com/metalsharp/DXVK-MacOS/releases/download/v3.1.1-macos.1/moltenvk-v3.1.1-macos.1-universal.tar.zst',
        'sha256': '85b28836349a735917db641a1bf2d3dd9e005b2281ee7b0583145ecd6f36cb4a',
        'checksum_source': 'https://api.github.com/repos/metalsharp/DXVK-MacOS/releases/tags/v3.1.1-macos.1',
        'directory': 'moltenvk-v3.1.1-macos.1-universal',
    },
}


def run(argv, *, env=None, capture=False):
    argv = [str(x) for x in argv]
    print('+ ' + ' '.join(argv), flush=True)
    result = subprocess.run(argv, env=env, check=True, text=True,
                            stdout=subprocess.PIPE if capture else None)
    return result.stdout.strip() if capture else ''


def tool(name):
    found = shutil.which(name)
    if not found:
        raise RuntimeError(f'Missing build tool: {name}; see build/requirements.txt')
    return found


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def fetch(work, key):
    spec = ASSETS[key]
    cache = work / 'cache'
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / spec['file']
    if not archive.exists():
        partial = archive.with_name(archive.name + '.part')
        run([tool('curl'), '--fail', '--location', '--retry', '3', '--connect-timeout',
             '30', '--max-time', '1800', '--output', partial, spec['url']])
        if sha256(partial) != spec['sha256']:
            partial.unlink()
            raise RuntimeError(f'Checksum mismatch for downloaded {spec["file"]}')
        partial.rename(archive)
    if sha256(archive) != spec['sha256']:
        raise RuntimeError(f'Checksum mismatch: {archive}; remove this invalid cache file')
    source = work / 'sources' / spec['directory']
    marker = source / '.paintnet-verified-source'
    if marker.exists() and marker.read_text().strip() == spec['sha256']:
        return source
    if source.exists():
        raise RuntimeError(f'Unverified or incomplete source directory: {source}; remove it first')
    source.parent.mkdir(parents=True, exist_ok=True)
    # macOS bsdtar supports .xz and .zst; only verified immutable archives are unpacked.
    run([tool('tar'), '-xf', archive, '-C', source.parent])
    marker.write_text(spec['sha256'] + '\n')
    return source


def clean_env():
    env = dict(os.environ)
    for name in ('CFLAGS', 'CXXFLAGS', 'CPPFLAGS', 'LDFLAGS', 'CPATH', 'LIBRARY_PATH',
                 'DYLD_LIBRARY_PATH', 'DYLD_FALLBACK_LIBRARY_PATH', 'PKG_CONFIG_PATH',
                 'PKG_CONFIG_SYSROOT_DIR', 'CC', 'CXX', 'SDKROOT', 'ARCHFLAGS',
                 'CMAKE_PREFIX_PATH', 'CMAKE_LIBRARY_PATH', 'CMAKE_INCLUDE_PATH',
                 'CMAKE_FRAMEWORK_PATH', 'PYTHONPATH', 'PYTHONHOME'):
        env.pop(name, None)
    env['MACOSX_DEPLOYMENT_TARGET'] = MIN_MACOS
    env['PKG_CONFIG_LIBDIR'] = ''  # Never accidentally link Homebrew arm64 libraries.
    return env


def sdk_tools():
    return {name: run(['xcrun', '--find', name], capture=True)
            for name in ('clang', 'clang++', 'ar', 'strip')}


def provenance():
    return {
        'compiler': run(['xcrun', 'clang', '--version'], capture=True),
        'sdk_version': run(['xcrun', '--show-sdk-version'], capture=True),
        'host_architecture': platform.machine(),
        'host_macos': platform.mac_ver()[0],
        'deployment_target_requested': MIN_MACOS,
        'python_version': platform.python_version(),
        'reproducibility': 'Pinned inputs and recipe; not bit-for-bit reproducible across Apple SDK/compiler versions',
    }


def build_llvm(work, jobs):
    source = fetch(work, 'llvm')
    build = work / 'llvm-build'
    prefix = work / 'llvm-install'
    cc = sdk_tools()
    sdk = run(['xcrun', '--show-sdk-path'], capture=True)
    options = [
        '-DCMAKE_BUILD_TYPE=Release', f'-DCMAKE_INSTALL_PREFIX={prefix}',
        '-DCMAKE_OSX_ARCHITECTURES=x86_64', f'-DCMAKE_OSX_DEPLOYMENT_TARGET={MIN_MACOS}',
        f'-DCMAKE_OSX_SYSROOT={sdk}', f'-DCMAKE_C_COMPILER={cc["clang"]}',
        f'-DCMAKE_CXX_COMPILER={cc["clang++"]}', '-DLLVM_TARGETS_TO_BUILD=X86',
        '-DLLVM_HOST_TRIPLE=x86_64-apple-darwin', '-DLLVM_DEFAULT_TARGET_TRIPLE=x86_64-apple-darwin',
        '-DLLVM_ENABLE_PROJECTS=', '-DLLVM_ENABLE_RUNTIMES=', '-DLLVM_BUILD_LLVM_DYLIB=ON',
        '-DLLVM_LINK_LLVM_DYLIB=ON', '-DLLVM_DYLIB_COMPONENTS=all',
        '-DLLVM_ENABLE_ASSERTIONS=OFF', '-DLLVM_ENABLE_RTTI=OFF', '-DLLVM_INCLUDE_TESTS=OFF',
        '-DLLVM_INCLUDE_EXAMPLES=OFF', '-DLLVM_INCLUDE_BENCHMARKS=OFF',
        '-DLLVM_INCLUDE_DOCS=OFF', '-DLLVM_ENABLE_BINDINGS=OFF',
        '-DLLVM_ENABLE_ZLIB=OFF', '-DLLVM_ENABLE_ZSTD=OFF', '-DLLVM_ENABLE_LIBXML2=OFF',
        '-DLLVM_ENABLE_LIBEDIT=OFF', '-DLLVM_ENABLE_FFI=OFF',
        '-DLLVM_ENABLE_CURL=OFF', '-DLLVM_ENABLE_HTTPLIB=OFF',
        '-DCMAKE_INSTALL_RPATH=@loader_path/../lib', '-DCMAKE_BUILD_WITH_INSTALL_RPATH=OFF',
        '-DCMAKE_SHARED_LINKER_FLAGS=-Wl,-headerpad_max_install_names',
    ]
    cmake = tool('cmake')
    run([cmake, '-G', 'Ninja', '-S', source / 'llvm', '-B', build] + options, env=clean_env())
    run([cmake, '--build', build, '--parallel', jobs, '--target', 'LLVM', 'llvm-config'], env=clean_env())
    run([cmake, '--build', build, '--parallel', jobs, '--target', 'install-LLVM',
         'install-llvm-config', 'install-llvm-headers', 'install-cmake-exports'], env=clean_env())
    record = provenance()
    record['cmake_version'] = run([cmake, '--version'], capture=True).splitlines()[0]
    record['source_sha256'] = ASSETS['llvm']['sha256']
    record['configuration'] = [x for x in options if not any(y in x for y in ('PREFIX=', 'SYSROOT=', 'COMPILER='))]
    write_json(prefix / 'paintnet-build-provenance.json', record)


def find_bison():
    # Homebrew's Bison is keg-only. Discover its installation prefix.
    brew = shutil.which('brew')
    if brew:
        result = subprocess.run([brew, '--prefix', 'bison'], text=True, capture_output=True)
        candidate = Path(result.stdout.strip()) / 'bin/bison'
        if result.returncode == 0 and candidate.exists():
            return str(candidate)
    return tool('bison')


def build_mesa(work, llvm_prefix, jobs):
    source = fetch(work, 'mesa')
    llvm_config = llvm_prefix / 'bin/llvm-config'
    if not llvm_config.is_file():
        raise RuntimeError(f'Build LLVM first: missing {llvm_config}')
    env = clean_env()
    sdk = Path(run(['xcrun', '--show-sdk-path'], capture=True))
    env['SDKROOT'] = str(sdk)
    # Expose only Apple's SDK zlib to target pkg-config. Do not let CMake or
    # pkg-config choose an arm64 Homebrew zlib while cross-compiling x86_64.
    zlib_header = (sdk / 'usr/include/zlib.h').read_text()
    zlib_version = re.search(r'#define ZLIB_VERSION "([^"]+)"', zlib_header).group(1)
    pkgconfig = work / 'target-pkgconfig'
    pkgconfig.mkdir(exist_ok=True)
    (pkgconfig / 'zlib.pc').write_text(
        'Name: zlib\nDescription: macOS SDK system zlib\nVersion: ' + zlib_version +
        '\nLibs: -lz\nCflags:\n')
    env['PKG_CONFIG_LIBDIR'] = str(pkgconfig)
    # Executing x86_64 llvm-config on Apple Silicon also verifies Rosetta availability.
    version = run([llvm_config, '--version'], env=env, capture=True)
    if version != '19.1.7':
        raise RuntimeError(f'Expected LLVM 19.1.7, found {version}')
    cc = sdk_tools()
    cross = work / 'mesa-x86_64.ini'
    def literal(value):
        return "'" + str(value).replace('\\', '\\\\').replace("'", "\\'") + "'"
    flags = ['-arch', 'x86_64', '-mmacosx-version-min=' + MIN_MACOS]
    lines = ['[binaries]']
    for lang, compiler in (('c', 'clang'), ('cpp', 'clang++'), ('objc', 'clang'), ('objcpp', 'clang++')):
        lines.append(lang + ' = [' + ', '.join(literal(x) for x in [cc[compiler]] + flags) + ']')
    lines.extend(['ar = ' + literal(cc['ar']), 'strip = ' + literal(cc['strip']),
                  'pkg-config = ' + literal(tool('pkg-config')), 'bison = ' + literal(find_bison()),
                  'llvm-config = ' + literal(llvm_config),
                  '[host_machine]', "system = 'darwin'", "cpu_family = 'x86_64'",
                  "cpu = 'x86_64'", "endian = 'little'", '[properties]', 'needs_exe_wrapper = false',
                  '[built-in options]', "c_link_args = ['-Wl,-headerpad_max_install_names']",
                  "cpp_link_args = ['-Wl,-headerpad_max_install_names']"])
    cross.write_text('\n'.join(lines) + '\n')
    build = work / 'mesa-build'
    prefix = work / 'mesa-install'
    meson = tool('meson')
    options = ['--buildtype=release', '--wrap-mode=nofallback', '--prefix=' + str(prefix),
               '--libdir=lib', '-Dcpp_rtti=false', '-Dgallium-drivers=llvmpipe', '-Dvulkan-drivers=swrast',
               '-Dllvm=enabled', '-Dshared-llvm=enabled', '-Dspirv-tools=disabled',
               '-Dplatforms=macos', '-Dglx=disabled', '-Dgbm=disabled', '-Dgallium-va=disabled',
               '-Dopengl=false', '-Dgles1=disabled', '-Dgles2=disabled', '-Degl=disabled',
               '-Dxmlconfig=disabled', '-Dexpat=disabled', '-Dzstd=disabled',
               '-Dshader-cache=enabled', '-Dlmsensors=disabled', '-Ddisplay-info=disabled']
    cmd = [meson, 'setup', build, source, '--cross-file', cross] + options
    if (build / 'meson-private/coredata.dat').exists():
        cmd.append('--reconfigure')
    run(cmd, env=env)
    run([meson, 'compile', '-C', build, '-j', jobs], env=env)
    run([meson, 'install', '-C', build, '--no-rebuild'], env=env)
    record = provenance()
    record['meson_version'] = run([meson, '--version'], capture=True)
    record['options'] = [x for x in options if not x.startswith('--prefix=')]
    record['llvm_config_version'] = version
    record['system_zlib_version'] = zlib_version
    record['llvm_config_sha256'] = sha256(llvm_config)
    record['source_sha256'] = ASSETS['mesa']['sha256']
    write_json(prefix / 'paintnet-build-provenance.json', record)


def dependencies(path):
    lines = run(['otool', '-arch', 'x86_64', '-L', path], capture=True).splitlines()[1:]
    return [line.strip().split(' (compatibility version')[0] for line in lines if line.strip()]


def rpaths(path):
    out = run(['otool', '-arch', 'x86_64', '-l', path], capture=True)
    return re.findall(r'cmd LC_RPATH\s+cmdsize \d+\s+path (.*?) \(offset', out)


def is_system(path):
    return path.startswith('/usr/lib/') or path.startswith('/System/Library/')


def minimum_os(path):
    out = run(['otool', '-arch', 'x86_64', '-l', path], capture=True)
    versions = re.findall(r'\bminos ([\d.]+)', out)
    versions += re.findall(r'cmd LC_VERSION_MIN_MACOSX\s+cmdsize \d+\s+version ([\d.]+)', out)
    if not versions:
        raise RuntimeError(f'Cannot establish deployment target for {path}')
    return max(versions, key=version_tuple)


def version_tuple(value):
    return tuple(int(x) for x in value.split('.')) + (0,) * (3 - len(value.split('.')))


def bundle(work, output, llvm_prefix, external_llvm):
    if output.exists():
        raise RuntimeError(f'Output already exists: {output}; choose a fresh --output (never overwrite installed runtime)')
    mesa_source = fetch(work, 'mesa')
    llvm_source = fetch(work, 'llvm')
    dxvk = fetch(work, 'dxvk')
    molten = fetch(work, 'moltenvk')
    mesa_record_path = work / 'mesa-install/paintnet-build-provenance.json'
    mesa_record = json.loads(mesa_record_path.read_text())
    if mesa_record['llvm_config_sha256'] != sha256(llvm_prefix / 'bin/llvm-config'):
        raise RuntimeError('Mesa was compiled against a different LLVM prefix; rebuild mesa with this --llvm-prefix')
    roots = list((work / 'mesa-install/lib').glob('libvulkan_lvp*.dylib'))
    if len(roots) != 1:
        raise RuntimeError('Expected exactly one installed libvulkan_lvp dylib; build Mesa first')
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=output.name + '.staging-', dir=output.parent))
    try:
        lib = stage / 'lib'
        for name in ('lib', 'dxvk', 'licenses', 'bin', 'icd'):
            (stage / name).mkdir()
        # Build complete non-system dependency closure; relocation never modifies source binaries.
        queued = roots + [molten / 'libMoltenVK.dylib']
        copied = {}
        while queued:
            original = queued.pop(0).resolve()
            name = original.name
            if name in copied:
                if copied[name] != original:
                    raise RuntimeError(f'Dylib basename collision: {name}')
                continue
            arches = run(['lipo', '-archs', original], capture=True).split()
            if 'x86_64' not in arches:
                raise RuntimeError(f'Not an x86_64 library: {original}')
            dest = lib / name
            if len(arches) > 1:
                run(['lipo', original, '-thin', 'x86_64', '-output', dest])
            else:
                shutil.copy2(original, dest)
            dest.chmod(0o755)
            copied[name] = original
            links = dependencies(original)
            # A dylib's first LC_LOAD listing is its own LC_ID_DYLIB.
            for dep in links[1:]:
                if is_system(dep):
                    continue
                candidates = [Path(dep)] if dep.startswith('/') else []
                if dep.startswith('@loader_path/'):
                    candidates.append(original.parent / dep[len('@loader_path/'):])
                if dep.startswith('@rpath/'):
                    tail = dep[len('@rpath/'):]
                    candidates += [Path(p.replace('@loader_path', str(original.parent))) / tail for p in rpaths(original)]
                    candidates += [original.parent / tail, llvm_prefix / 'lib' / tail]
                resolved = next((p.resolve() for p in candidates if p.is_file()), None)
                if resolved is None:
                    raise RuntimeError(f'Unresolved runtime dependency {dep} in {original}')
                queued.append(resolved)
                run(['install_name_tool', '-change', dep, '@loader_path/' + resolved.name, dest])
            run(['install_name_tool', '-id', '@rpath/' + name, dest])
            for rpath in rpaths(dest):
                if not rpath.startswith('@loader_path'):
                    run(['install_name_tool', '-delete_rpath', rpath, dest])
        for path in lib.iterdir():
            for dep in dependencies(path)[1:]:
                if not is_system(dep) and (not dep.startswith('@loader_path/') or not (lib / dep.split('/')[-1]).is_file()):
                    raise RuntimeError(f'Nonportable dependency remains: {dep}')
            run(['codesign', '--force', '--sign', '-', path])
            run(['codesign', '--verify', '--strict', path])
        for name in ('dxgi.dll', 'd3d11.dll', 'd3d10core.dll'):
            shutil.copy2(dxvk / 'x86_64-windows' / name, stage / 'dxvk' / name)
        manifests = list((work / 'mesa-install/share/vulkan/icd.d').glob('*lvp*.json'))
        if len(manifests) != 1:
            raise RuntimeError('Missing or ambiguous installed lavapipe manifest')
        for name, source, binary in [('lavapipe', manifests[0], roots[0].resolve().name),
                                     ('moltenvk', molten / 'MoltenVK_icd.json', 'libMoltenVK.dylib')]:
            value = json.loads(source.read_text())
            value['ICD']['library_path'] = '../lib/' + binary
            write_json(stage / 'icd' / (name + '.json'), value)
        run(['xcrun', 'clang', '-arch', 'x86_64', '-mmacosx-version-min=' + MIN_MACOS,
             '-O2', '-Wall', '-Wextra', '-Werror', '-I', mesa_source / 'include',
             HERE / 'vulkan_probe.c', '-o', stage / 'bin/vulkan-probe'])
        run(['codesign', '--force', '--sign', '-', stage / 'bin/vulkan-probe'])
        shutil.copytree(mesa_source / 'licenses', stage / 'licenses/mesa')
        shutil.copy2(mesa_source / 'docs/license.rst', stage / 'licenses/Mesa-license.rst')
        shutil.copy2(llvm_source / 'llvm/LICENSE.TXT', stage / 'licenses/LLVM.txt')
        for filename in ('LICENSE', 'LICENSE.dxbc-spirv'):
            shutil.copy2(dxvk / filename, stage / 'licenses' / ('DXVK-' + filename))
        shutil.copy2(molten / 'LICENSE.MoltenVK', stage / 'licenses/MoltenVK.txt')
        shutil.copy2(molten / 'SOURCE', stage / 'licenses/MoltenVK-source.txt')
        extra = [name for name in copied if not (name.startswith('libLLVM') or name.startswith('libvulkan_lvp') or name == 'libMoltenVK.dylib')]
        if extra:
            raise RuntimeError('Custom LLVM brought unlicensed extra dependencies: ' + ', '.join(extra) + '. Build the pinned LLVM from source instead.')
        llvm_record_path = llvm_prefix / 'paintnet-build-provenance.json'
        llvm_record = json.loads(llvm_record_path.read_text()) if llvm_record_path.exists() else {
            'origin': 'external-development-prefix', 'version': run([llvm_prefix / 'bin/llvm-config', '--version'], capture=True),
            'compiler_and_sdk': 'unknown; minimum OS derived from bundled Mach-O load commands'}
        binaries = list(lib.iterdir()) + [stage / 'bin/vulkan-probe']
        minimum = max([MIN_MACOS] + [minimum_os(p) for p in binaries], key=version_tuple)
        record = {'schema_version': 1, 'architecture': 'x86_64', 'minimum_macos': minimum,
                  'llvm_origin': 'external-development-prefix' if external_llvm else 'pinned-source-build',
                  'components': ASSETS, 'build': provenance(), 'llvm_build': llvm_record,
                  'mesa_build': mesa_record, 'signature': 'ad-hoc (not Apple notarized)',
                  'files': {str(p.relative_to(stage)): sha256(p) for p in sorted(stage.rglob('*')) if p.is_file()}}
        write_json(stage / 'versions.json', record)
        (stage / 'SHA256SUMS').write_text(''.join(
            sha256(p) + '  ' + str(p.relative_to(stage)) + '\n'
            for p in sorted(stage.rglob('*')) if p.is_file() and p.name != 'SHA256SUMS'))
        stage.rename(output)
        print(f'Bundle ready: {output} (minimum macOS {minimum})', flush=True)
    except BaseException:
        shutil.rmtree(stage)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('stage', choices=('fetch', 'llvm', 'mesa', 'bundle', 'all'), nargs='?', default='all')
    parser.add_argument('--work-dir', type=Path, default=HERE.parent / '.build-work')
    parser.add_argument('--output', type=Path, default=HERE.parent / 'dist/runtime')
    parser.add_argument('--jobs', type=int, default=min(os.cpu_count() or 2, 8))
    parser.add_argument('--llvm-prefix', type=Path, help='Development override: existing x86_64 LLVM 19.1.7 (must have no external non-system dependencies)')
    args = parser.parse_args()
    if sys.platform != 'darwin':
        parser.error('Build requires macOS and Xcode Command Line Tools')
    if sys.version_info < (3, 10):
        parser.error('Mesa requires Python >=3.10; create the build venv with a newer Python')
    if args.jobs < 1:
        parser.error('--jobs must be positive')
    work = args.work_dir.expanduser().resolve()
    work.mkdir(parents=True, exist_ok=True)
    llvm_prefix = args.llvm_prefix.expanduser().resolve() if args.llvm_prefix else work / 'llvm-install'
    if args.stage in ('fetch', 'all'):
        for key in ASSETS:
            fetch(work, key)
    if args.stage in ('llvm', 'all') and not args.llvm_prefix:
        build_llvm(work, args.jobs)
    if args.stage in ('mesa', 'all'):
        build_mesa(work, llvm_prefix, args.jobs)
    if args.stage in ('bundle', 'all'):
        bundle(work, args.output.expanduser().resolve(), llvm_prefix, bool(args.llvm_prefix))


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, subprocess.CalledProcessError, OSError, KeyError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        sys.exit(1)
