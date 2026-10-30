#!/bin/bash
# macOS ships every command used here; Python is only a build-time dependency.
set -euo pipefail
export LC_ALL=C
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -P)"
APP_ZIP= APP_SHA256= RUNTIME= WINE= LAUNCHER=
DEST="$HOME/Library/Application Support/Paint.NET-on-Wine-macOS"
APP_BUNDLE="$HOME/Applications/Paint.NET.app"
CHECK_ONLY=false
UI_ACCELERATION=false
STAGE= CHECK_DIR= WINE_BIN= ACTIVE_PID=

fail() { printf 'Installation failed: %s\n' "$*" >&2; exit 1; }
usage() {
    cat <<'HELP'
Usage: ./install.sh --app-zip FILE --app-sha256 SHA256 --runtime DIRECTORY [options]
  --wine PATH                 Wine 11.20+ executable or Wine .app
  --dest DIRECTORY            New installation directory (must not exist)
  --app-bundle PATH            New .app (default: ~/Applications/Paint.NET.app)
  --launcher PATH              Optional additional .command launcher
  --check-only                 Validate inputs without initializing Wine/installing
  --enable-ui-acceleration     Experimental GPU UI rendering (default: CPU UI)
No sudo, Python, or developer tools are required. The app is not started.
HELP
}
while [ "$#" -gt 0 ]; do
    case "$1" in
        --app-zip|--app-sha256|--runtime|--wine|--dest|--launcher|--app-bundle)
            [ "$#" -ge 2 ] && [ -n "$2" ] || fail "Missing value for $1"
            case "$1" in
                --app-zip) APP_ZIP=$2;; --app-sha256) APP_SHA256=$2;;
                --runtime) RUNTIME=$2;; --wine) WINE=$2;; --dest) DEST=$2;;
                --launcher) LAUNCHER=$2;; --app-bundle) APP_BUNDLE=$2;;
            esac
            shift 2;;
        --check-only) CHECK_ONLY=true; shift;;
        --enable-ui-acceleration) UI_ACCELERATION=true; shift;;
        -h|--help) usage; exit 0;;
        *) fail "Unknown argument: $1";;
    esac
done
[ "$(uname -s)" = Darwin ] || fail 'This installer supports macOS only'
[ -n "$APP_ZIP" ] && [ -n "$APP_SHA256" ] && [ -n "$RUNTIME" ] || { usage >&2; exit 1; }

# Expand quoted ~/ paths without eval and preserve spaces/apostrophes.
absolute_path() {
    local path=$1
    case "$path" in '~') path=$HOME;; '~/'*) path="$HOME/${path:2}";; esac
    case "$path" in /*) ;; *) path="$PWD/$path";; esac
    case "$path" in *$'\n'*|*$'\r'*) fail 'Paths containing newlines are not supported';; esac
    while [ "${path%/}" != "$path" ]; do path=${path%/}; done
    [ -n "$path" ] || fail 'The filesystem root cannot be an installation path'
    printf '%s\n' "$path"
}
APP_ZIP=$(absolute_path "$APP_ZIP")
RUNTIME=$(absolute_path "$RUNTIME")
DEST=$(absolute_path "$DEST")
APP_BUNDLE=$(absolute_path "$APP_BUNDLE")
[ -z "$LAUNCHER" ] || LAUNCHER=$(absolute_path "$LAUNCHER")
for path in "$DEST" "$APP_BUNDLE" "$LAUNCHER"; do
    [ -z "$path" ] || { [ ! -e "$path" ] && [ ! -L "$path" ]; } || fail "Path already exists; choose a new path: $path"
done
case "$APP_BUNDLE" in *.app) ;; *) fail '--app-bundle must end in .app';; esac
[[ "$APP_SHA256" =~ ^[[:xdigit:]]{64}$ ]] || fail '--app-sha256 must be a 64-character SHA256 digest'
APP_SHA256=$(printf '%s' "$APP_SHA256" | /usr/bin/tr 'A-F' 'a-f')
[ -f "$APP_ZIP" ] || fail "Archive not found: $APP_ZIP"
actual=$(/usr/bin/shasum -a 256 "$APP_ZIP")
[ "${actual%% *}" = "$APP_SHA256" ] || fail 'Paint.NET archive checksum mismatch'
[ -d "$RUNTIME" ] || fail "Runtime not found: $RUNTIME"
RUNTIME=$(cd "$RUNTIME" && pwd -P)

safe_relative_path() {
    local name=$1
    case "$name" in ''|/*|*:*|*\\*|*$'\n'*|*$'\r'*) return 1;; esac
    case "/$name/" in */../*|*/./*|*//*) return 1;; esac
}
# Every regular runtime file must occur exactly once; symlinks and special
# files are rejected, including symlinked parent directories.
COVERED=()
ACTUAL_COUNT=0
check_runtime_tree() {
    local directory=$1 path relative item found
    for path in "$directory"/* "$directory"/.[!.]* "$directory"/..?*; do
        [ -e "$path" ] || [ -L "$path" ] || continue
        [ ! -L "$path" ] || fail "Runtime symlink is not allowed: $path"
        if [ -d "$path" ]; then
            check_runtime_tree "$path"
        elif [ -f "$path" ]; then
            relative=${path#"$RUNTIME/"}
            [ "$relative" != SHA256SUMS ] || continue
            safe_relative_path "$relative" || fail "Unsafe runtime path: $relative"
            found=false
            for item in "${COVERED[@]+${COVERED[@]}}"; do [ "$item" != "$relative" ] || found=true; done
            $found || fail "Runtime checksum list does not cover: $relative"
            ACTUAL_COUNT=$((ACTUAL_COUNT + 1))
        else
            fail "Runtime special file is not allowed: $path"
        fi
    done
}
verify_runtime() {
    local line digest name item result component parent
    [ -f "$RUNTIME/SHA256SUMS" ] && [ ! -L "$RUNTIME/SHA256SUMS" ] || fail 'Runtime SHA256SUMS missing or symlinked'
    COVERED=()
    ACTUAL_COUNT=0
    while IFS= read -r line || [ -n "$line" ]; do
        digest=${line%% *}
        [[ "$digest" =~ ^[0-9a-f]{64}$ ]] || fail 'Malformed runtime checksum digest'
        name=${line#"$digest"}
        case "$name" in '  '*) name=${name:2};; ' *'*) name=${name:2};; *) fail 'Malformed runtime checksum line';; esac
        safe_relative_path "$name" || fail "Unsafe runtime checksum path: $name"
        [ "$name" != SHA256SUMS ] || fail 'SHA256SUMS must not cover itself'
        for item in "${COVERED[@]+${COVERED[@]}}"; do [ "$item" != "$name" ] || fail "Duplicate runtime checksum: $name"; done
        parent="$RUNTIME/$name"
        while [ "$parent" != "$RUNTIME" ]; do
            [ ! -L "$parent" ] || fail "Runtime symlink is not allowed: $parent"
            parent=${parent%/*}
        done
        [ -f "$RUNTIME/$name" ] || fail "Runtime checksum file missing: $name"
        result=$(/usr/bin/shasum -a 256 "$RUNTIME/$name")
        [ "${result%% *}" = "$digest" ] || fail "Runtime checksum mismatch: $name"
        COVERED[${#COVERED[@]}]=$name
    done < "$RUNTIME/SHA256SUMS"
    [ "${#COVERED[@]}" -gt 0 ] || fail 'Runtime checksum list is empty'
    check_runtime_tree "$RUNTIME"
    [ "$ACTUAL_COUNT" -eq "${#COVERED[@]}" ] || fail 'Runtime checksum coverage differs from bundle files'
}
verify_runtime
minimum=$(/usr/bin/plutil -extract minimum_macos raw -expect string -o - "$RUNTIME/versions.json")
host_macos=$(/usr/bin/sw_vers -productVersion)
[[ "$minimum" =~ ^[0-9]+\.[0-9]+(\.[0-9]+)?$ ]] || fail 'Invalid runtime minimum_macos'
version_at_least() {
    local left=$1 right=$2 lm ln lp rm rn rp
    IFS=. read -r lm ln lp <<< "$left"
    IFS=. read -r rm rn rp <<< "$right"
    lm=$((10#$lm)); ln=$((10#${ln:-0})); lp=$((10#${lp:-0}))
    rm=$((10#$rm)); rn=$((10#${rn:-0})); rp=$((10#${rp:-0}))
    (( lm > rm || (lm == rm && ln > rn) || (lm == rm && ln == rn && lp >= rp) ))
}
version_at_least "$host_macos" "$minimum" || fail "This runtime requires macOS $minimum or newer"
if [ "$(uname -m)" = arm64 ]; then
    /usr/bin/arch -x86_64 /usr/bin/true || fail 'Rosetta 2 is required to run this x86_64 runtime'
fi
if [ -z "$WINE" ]; then WINE=$(command -v wine) || fail 'Wine not found; pass --wine /path/to/Wine.app or /path/to/bin/wine'; fi
WINE=$(absolute_path "$WINE")
case "$WINE" in *.app) WINE="$WINE/Contents/Resources/wine/bin/wine";; esac
# Resolve Homebrew-style executable symlinks before locating sibling helpers.
for ((hops=0; hops<40; hops++)); do
    [ -L "$WINE" ] || break
    target=$(/usr/bin/readlink "$WINE")
    case "$target" in /*) WINE=$target;; *) WINE="$(dirname "$WINE")/$target";; esac
done
[ ! -L "$WINE" ] && [ -f "$WINE" ] && [ -x "$WINE" ] || fail "Wine executable not found: $WINE"
WINE_BIN=$(cd "$(dirname "$WINE")" && pwd -P)
WINE="$WINE_BIN/$(basename "$WINE")"
wine_version=$("$WINE" --version)
[[ "$wine_version" =~ wine-([0-9]+)\.([0-9]+) ]] || fail "Unrecognized Wine version: $wine_version"
version_at_least "${BASH_REMATCH[1]}.${BASH_REMATCH[2]}" 11.20 || fail "Wine 11.20 or newer required; found $wine_version"
for name in wineboot winecfg wineserver; do [ -x "$WINE_BIN/$name" ] || fail "Wine helper missing: $name"; done
[ -f "$WINE_BIN/../lib/libvulkan.dylib" ] || fail "Wine Vulkan loader missing: $WINE_BIN/../lib/libvulkan.dylib"
for name in lib/libvulkan_lvp.dylib lib/libMoltenVK.dylib bin/vulkan-probe bin/paintnet-launcher icd/lavapipe.json icd/moltenvk.json dxvk/dxgi.dll dxvk/d3d11.dll dxvk/d3d10core.dll; do
    [ -f "$RUNTIME/$name" ] || fail "Runtime missing $name"
done
[ -x "$RUNTIME/bin/vulkan-probe" ] && [ -x "$RUNTIME/bin/paintnet-launcher" ] || fail 'Runtime native executables are not executable'
[ -x "$SCRIPT_DIR/scripts/create-app" ] || fail 'Missing scripts/create-app'

# Bounded child waits also work on stock macOS, which has no timeout command.
run_bounded() {
    local limit=$1 pid started status
    shift
    "$@" & pid=$!
    ACTIVE_PID=$pid
    started=$SECONDS
    while kill -0 "$pid" 2>/dev/null; do
        if [ $((SECONDS - started)) -ge "$limit" ]; then
            kill -TERM "$pid" 2>/dev/null || true
            /bin/sleep 1
            kill -KILL "$pid" 2>/dev/null || true
            wait "$pid" 2>/dev/null || true
            ACTIVE_PID=
            printf 'Command timed out: %s\n' "$1" >&2
            return 124
        fi
        /bin/sleep 1
    done
    if wait "$pid"; then status=0; else status=$?; fi
    ACTIVE_PID=
    return "$status"
}
cleanup() {
    local status=$? keep=false
    trap - EXIT INT TERM
    set +e
    if [ -n "$ACTIVE_PID" ]; then
        kill -TERM "$ACTIVE_PID" 2>/dev/null
        /bin/sleep 1
        kill -KILL "$ACTIVE_PID" 2>/dev/null
        wait "$ACTIVE_PID" 2>/dev/null
        ACTIVE_PID=
    fi
    if [ -n "$STAGE" ] && [ -d "$STAGE" ]; then
        if [ -d "$STAGE/wineprefix" ]; then
            run_bounded 10 /usr/bin/env WINEPREFIX="$STAGE/wineprefix" "$WINE_BIN/wineserver" -k >/dev/null 2>&1 || keep=true
            run_bounded 30 /usr/bin/env WINEPREFIX="$STAGE/wineprefix" "$WINE_BIN/wineserver" -w >/dev/null 2>&1 || keep=true
        fi
        if $keep; then printf 'Could not stop staged Wine; retained failed installation at %s\n' "$STAGE" >&2
        else /bin/rm -rf "$STAGE"; fi
    fi
    [ -z "$CHECK_DIR" ] || /bin/rm -rf "$CHECK_DIR"
    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
CHECK_DIR=$(/usr/bin/mktemp -d "${TMPDIR:-/tmp}/paintnet-check.XXXXXX")
# libarchive normalizes Windows ZIP backslashes on macOS. Validate the listing
# before extraction, and reject all link/special members via the verbose type.
/usr/bin/tar -tf "$APP_ZIP" > "$CHECK_DIR/members"
/usr/bin/tar -tvf "$APP_ZIP" > "$CHECK_DIR/types"
[ -s "$CHECK_DIR/members" ] || fail 'Paint.NET archive is empty'
while IFS= read -r name || [ -n "$name" ]; do
    name=${name//\\//}
    name=${name%/}
    safe_relative_path "$name" || fail "Unsafe ZIP member: $name"
done < "$CHECK_DIR/members"
while IFS= read -r entry || [ -n "$entry" ]; do
    case "$entry" in -?????????\ *|d?????????\ *) ;; *) fail "ZIP links and special files are not allowed: $entry";; esac
done < "$CHECK_DIR/types"
/bin/mkdir "$CHECK_DIR/app"
/usr/bin/tar -xf "$APP_ZIP" --no-same-owner --no-same-permissions -C "$CHECK_DIR/app"
[ -f "$CHECK_DIR/app/paintdotnet.exe" ] || fail 'ZIP must contain paintdotnet.exe at its root'
mode=$(/usr/bin/plutil -extract EnableWineMode raw -expect bool -o - "$CHECK_DIR/app/PaintDotNet.Configuration.json")
[ "$mode" = true ] || fail 'Use the official Wine package with EnableWineMode=true'
printf 'Wine: %s\nRuntime: %s\nDestination: %s\nApp: %s\n' "$wine_version" "$RUNTIME" "$DEST" "$APP_BUNDLE"
if $CHECK_ONLY; then printf 'Input validation passed; nothing installed.\n'; exit 0; fi

/bin/mkdir -p "$(dirname "$DEST")"
STAGE=$(/usr/bin/mktemp -d "$(dirname "$DEST")/.paintnet-install.XXXXXX")
/usr/bin/ditto --noextattr --noqtn "$RUNTIME" "$STAGE/runtime"
RUNTIME="$STAGE/runtime"
verify_runtime
/usr/bin/ditto --noextattr --noqtn "$CHECK_DIR/app" "$STAGE/app"
settings="$STAGE/app/PaintDotNet.AppSettings.json"
[ -f "$settings" ] || printf '{}\n' > "$settings"
/usr/bin/plutil -convert xml1 "$settings"
ui_value=False
$UI_ACCELERATION && ui_value=True
/usr/bin/plutil -replace 'Graphics/EnableHardwareAccelerationForUI' -string "$ui_value" "$settings"
/usr/bin/plutil -convert json -r "$settings"
printf '%s\n' "$WINE" > "$STAGE/wine-path"
/bin/mkdir "$STAGE/bin"
/bin/cp "$SCRIPT_DIR/scripts/launch" "$SCRIPT_DIR/scripts/diagnose" "$STAGE/bin/"
/bin/chmod 755 "$STAGE/bin/launch" "$STAGE/bin/diagnose"
printf '#!/bin/bash\nexec "$(cd "$(dirname "$0")" && pwd)/bin/launch" "$@"\n' > "$STAGE/Paint.NET.command"
/bin/chmod 755 "$STAGE/Paint.NET.command"
run_bounded 180 "$STAGE/bin/diagnose"
run_bounded 180 /usr/bin/env WINEPREFIX="$STAGE/wineprefix" WINEDEBUG=-all WINEDLLOVERRIDES='mshtml=' "$WINE_BIN/wineboot" --init
run_bounded 180 /usr/bin/env WINEPREFIX="$STAGE/wineprefix" WINEDEBUG=-all WINEDLLOVERRIDES='mshtml=' "$WINE_BIN/winecfg" -v win11
system32="$STAGE/wineprefix/drive_c/windows/system32"
[ -d "$system32" ] || fail 'Wine did not create the expected system32 directory'
for name in dxgi.dll d3d11.dll d3d10core.dll; do /bin/cp "$STAGE/runtime/dxvk/$name" "$system32/$name"; done
printf '\n# macOS: expose llvmpipe alongside hardware Vulkan adapters\ndxvk.deviceFilter = " "\n' >> "$STAGE/app/dxvk.conf"
receipt="$STAGE/install-receipt.json"
/usr/bin/plutil -create xml1 "$receipt"
/usr/bin/plutil -insert app_archive_sha256 -string "$APP_SHA256" "$receipt"
/usr/bin/plutil -insert wine -string "$wine_version" "$receipt"
/usr/bin/plutil -insert host_macos -string "$host_macos" "$receipt"
/usr/bin/plutil -insert runtime -json "$(cat "$STAGE/runtime/versions.json")" "$receipt"
/usr/bin/plutil -insert ui_hardware_acceleration -bool "$UI_ACCELERATION" "$receipt"
/usr/bin/plutil -convert json -r "$receipt"
run_bounded 10 /usr/bin/env WINEPREFIX="$STAGE/wineprefix" "$WINE_BIN/wineserver" -k
run_bounded 30 /usr/bin/env WINEPREFIX="$STAGE/wineprefix" "$WINE_BIN/wineserver" -w
# The bundled native helper uses Darwin renamex_np(RENAME_EXCL): an atomic
# commit that also refuses a destination created concurrently by another job.
"$STAGE/runtime/bin/paintnet-launcher" --rename-exclusive "$STAGE" "$DEST"
STAGE=
"$SCRIPT_DIR/scripts/create-app" "$DEST" "$APP_BUNDLE"
if [ -n "$LAUNCHER" ]; then
    /bin/mkdir -p "$(dirname "$LAUNCHER")"
    ( set -C; printf '#!/bin/bash\nexec %q "$@"\n' "$DEST/bin/launch" > "$LAUNCHER" ) || fail "Could not create launcher without overwriting: $LAUNCHER"
    /bin/chmod 755 "$LAUNCHER"
fi
printf 'Installed. Open: %s\nCommand-line launcher: %s\n' "$APP_BUNDLE" "$DEST/Paint.NET.command"
