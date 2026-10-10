#!/usr/bin/env bash
# Build pinned adapterremoval sources for native and WebAssembly comparison.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
MODE=${1:-all}
case "$MODE" in all|native|patched-native|wasm) ;; *) echo 'Usage: scripts/build-adapterremoval.sh [all|native|patched-native|wasm]' >&2; exit 2;; esac
JOBS=${JOBS:-2}
BUILD_ROOT=${ADAPTERREMOVAL_BUILD_ROOT:-"$ROOT/build/adapterremoval"}
LOCK="$ROOT/tools/adapterremoval/sources.lock.json"
mkdir -p "$ROOT/build/downloads" "$BUILD_ROOT" "$ROOT/dist/adapterremoval"
lock_value() {
    python3 - "$LOCK" "$1" <<'PY'
import json, sys
value = json.load(open(sys.argv[1]))
for key in sys.argv[2].split('.'):
    value = value[key]
print(value)
PY
}
download() {
    local url=$1 destination=$2 expected=$3
    if [[ ! -f "$destination" ]]; then
        curl --fail --location --retry 3 --max-time 300 "$url" -o "$destination.tmp"
        mv "$destination.tmp" "$destination"
    fi
    python3 - "$destination" "$expected" <<'PY'
import hashlib, pathlib, sys
path = pathlib.Path(sys.argv[1])
actual = hashlib.sha256(path.read_bytes()).hexdigest()
if actual != sys.argv[2]:
    raise SystemExit(f'Checksum mismatch: {path}: expected {sys.argv[2]}, got {actual}')
PY
}
UPSTREAM_ARCHIVE="$ROOT/build/downloads/adapterremoval-$(lock_value revision).tar.gz"
ZLIB_ARCHIVE="$ROOT/build/downloads/zlib-$(lock_value dependencies.zlib.version).tar.gz"
BZIP2_ARCHIVE="$ROOT/build/downloads/bzip2-$(lock_value dependencies.bzip2.version).tar.gz"
download "$(lock_value dependencies.bzip2.url)" "$BZIP2_ARCHIVE" "$(lock_value dependencies.bzip2.sha256)"
download "$(lock_value url)" "$UPSTREAM_ARCHIVE" "$(lock_value sha256)"
download "$(lock_value dependencies.zlib.url)" "$ZLIB_ARCHIVE" "$(lock_value dependencies.zlib.sha256)"
build_variant() {
    local variant=$1 work="$BUILD_ROOT/$1" cc=${CC:-cc} cxx=${CXX:-c++} ar=${AR:-ar} ranlib=${RANLIB:-ranlib}
    local flags='-O3 -pipe -std=c++11 -msse2' link_flags='-pthread' extension=''
    if [[ "$variant" == wasm ]]; then
        if ! command -v emcc >/dev/null; then
            if [[ -n "${EMSDK:-}" && -f "$EMSDK/emsdk_env.sh" ]]; then
                set +u; source "$EMSDK/emsdk_env.sh"; set -u
            else
                echo 'Activate Emscripten 3.1.64 or set EMSDK to its directory.' >&2; exit 1
            fi
        fi
        if [[ $(emcc --version | head -n 1) != *"$(lock_value emscripten)"* ]]; then
            echo "Expected Emscripten $(lock_value emscripten)" >&2; exit 1
        fi
        cc=emcc; cxx=em++; ar=emar; ranlib=emranlib; extension='.mjs'
        flags+=' -msimd128 -fexceptions'
        link_flags='-fexceptions -Wl,--fatal-warnings -sMODULARIZE=1 -sEXPORT_ES6=1 -sEXPORT_NAME=createAdapterRemoval -sINVOKE_RUN=0 -sEXPORTED_RUNTIME_METHODS=FS,callMain -sINCOMING_MODULE_JS_API=preRun,noInitialRun,stdin,stdout,stderr,onExit,quit,locateFile,wasmBinary,print,printErr,onAbort,thisProgram -sENVIRONMENT=web,worker,node -sALLOW_MEMORY_GROWTH=1 -sINITIAL_MEMORY=33554432 -sMAXIMUM_MEMORY=2147483648 -sSTACK_SIZE=8388608 -sEXIT_RUNTIME=1 -sFORCE_FILESYSTEM=1'
    fi
    rm -rf -- "$work/source" "$work/zlib" "$work/zlib-prefix" "$work/bzip2" "$work/objects"
    mkdir -p "$work/source" "$work/zlib" "$work/zlib-prefix" "$work/bzip2"
    tar --no-same-owner -xzf "$UPSTREAM_ARCHIVE" -C "$work/source" --strip-components=1
    tar --no-same-owner -xzf "$ZLIB_ARCHIVE" -C "$work/zlib" --strip-components=1
    tar --no-same-owner -xzf "$BZIP2_ARCHIVE" -C "$work/bzip2" --strip-components=1
    if [[ "$variant" != native ]]; then
        patch --batch --forward -d "$work/source" -p1 < "$ROOT/tools/adapterremoval/browser.patch"
        flags+=' -Werror=format -Werror=shift-count-overflow'
    fi
    (
        cd "$work/zlib"
        CC="$cc" AR="$ar" RANLIB="$ranlib" CFLAGS='-O3 -pipe' ./configure --static --prefix="$work/zlib-prefix"
        make -j"$JOBS"
        make install
    )
    make -C "$work/bzip2" -j"$JOBS" libbz2.a CC="$cc" AR="$ar" RANLIB="$ranlib" CFLAGS='-O3 -pipe -D_FILE_OFFSET_BITS=64'
    mkdir -p "$work/objects"
    # These are exactly the application translation units in the upstream Makefile.
    for source in "$work/source"/src/*.cpp; do
        "$cxx" $flags -I"$work/zlib-prefix/include" -I"$work/bzip2" -c "$source" -o "$work/objects/$(basename "${source%.cpp}").o"
    done
    "$cxx" $flags "$work/objects"/*.o "$work/zlib-prefix/lib/libz.a" "$work/bzip2/libbz2.a" -lm $link_flags -o "$work/source/adapterremoval$extension"
    if [[ "$variant" == wasm ]]; then
        cp "$work/source/adapterremoval.mjs" "$work/source/adapterremoval.wasm" "$ROOT/dist/adapterremoval/"
        cp "$ROOT/tools/adapterremoval/LICENSE" "$ROOT/tools/adapterremoval/sources.lock.json" "$ROOT/tools/adapterremoval/browser.patch" "$ROOT/dist/adapterremoval/"
        cp "$work/zlib/LICENSE" "$ROOT/dist/adapterremoval/LICENSE.zlib"
        cp "$UPSTREAM_ARCHIVE" "$ZLIB_ARCHIVE" "$BZIP2_ARCHIVE" "$ROOT/dist/adapterremoval/"
        cp "$work/bzip2/LICENSE" "$ROOT/dist/adapterremoval/LICENSE.bzip2"
    else
        mkdir -p "$ROOT/build/$variant"
        cp "$work/source/adapterremoval" "$ROOT/build/$variant/adapterremoval"
    fi
}
if [[ "$MODE" == all ]]; then
    build_variant native
    build_variant patched-native
    build_variant wasm
else
    build_variant "$MODE"
fi
