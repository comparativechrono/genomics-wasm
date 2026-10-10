#!/usr/bin/env bash
# Build the upstream baseline, patched baseline and standalone WASM package.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
MODE=${1:-all}
case "$MODE" in all|native|patched-native|wasm) ;; *) echo 'Usage: scripts/build-minimap2.sh [all|native|patched-native|wasm]' >&2; exit 2;; esac
JOBS=${JOBS:-2}
BUILD_ROOT=${MINIMAP2_BUILD_ROOT:-"$ROOT/build/minimap2"}
LOCK="$ROOT/tools/minimap2/sources.lock.json"
mkdir -p "$ROOT/build/downloads" "$BUILD_ROOT" "$ROOT/dist/minimap2"

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

MINIMAP2_ARCHIVE="$ROOT/build/downloads/minimap2-$(lock_value revision).tar.gz"
ZLIB_ARCHIVE="$ROOT/build/downloads/zlib-$(lock_value dependencies.zlib.version).tar.gz"
download "$(lock_value url)" "$MINIMAP2_ARCHIVE" "$(lock_value sha256)"
download "$(lock_value dependencies.zlib.url)" "$ZLIB_ARCHIVE" "$(lock_value dependencies.zlib.sha256)"

build_variant() {
    local variant=$1 work="$BUILD_ROOT/$1" cc=${CC:-cc} ar=${AR:-ar} ranlib=${RANLIB:-ranlib}
    local compile_flags='-O3 -Wall -Wno-unused-function' defines='-DHAVE_KALLOC'
    local portable_simd=''
    local link_flags='' libraries='-lm -lpthread'
    if [[ "$variant" == wasm ]]; then
        if ! command -v emcc >/dev/null; then
            if [[ -n "${EMSDK:-}" && -f "$EMSDK/emsdk_env.sh" ]]; then
                # Emsdk environment setup is not compatible with nounset.
                set +u
                source "$EMSDK/emsdk_env.sh"
                set -u
            else
                echo 'Activate Emscripten 3.1.64 or set EMSDK to its directory.' >&2
                exit 1
            fi
        fi
        if [[ $(emcc --version | head -n 1) != *"$(lock_value emscripten)"* ]]; then
            echo "Expected Emscripten $(lock_value emscripten); found $(emcc --version | head -n 1)" >&2
            exit 1
        fi
        cc=emcc; ar=emar; ranlib=emranlib
        compile_flags+=' -msimd128'
        libraries='-lm'
        link_flags='-Wl,--fatal-warnings -sMODULARIZE=1 -sEXPORT_ES6=1 -sEXPORT_NAME=createMinimap2 -sINVOKE_RUN=0 -sEXPORTED_RUNTIME_METHODS=FS,callMain -sINCOMING_MODULE_JS_API=preRun,noInitialRun,stdin,stdout,stderr,onExit,quit,locateFile,wasmBinary,print,printErr,onAbort,thisProgram -sENVIRONMENT=web,worker,node -sALLOW_MEMORY_GROWTH=1 -sINITIAL_MEMORY=33554432 -sMAXIMUM_MEMORY=2147483648 -sSTACK_SIZE=8388608 -sEXIT_RUNTIME=1 -sFORCE_FILESYSTEM=1'
    fi
    # Start from pristine input on every invocation; never apply patches over
    # an earlier build tree or retain old configure results.
    rm -rf -- "$work/source" "$work/zlib" "$work/zlib-prefix"
    mkdir -p "$work/source" "$work/zlib" "$work/zlib-prefix"
    tar --no-same-owner -xzf "$MINIMAP2_ARCHIVE" -C "$work/source" --strip-components=1
    tar --no-same-owner -xzf "$ZLIB_ARCHIVE" -C "$work/zlib" --strip-components=1
    if [[ "$variant" != native ]]; then
        patch --batch --forward -d "$work/source" -p1 < "$ROOT/tools/minimap2/browser.patch"
        cp "$ROOT/tools/minimap2/port.h" "$work/source/port.h"
        # Keep the upstream SSE4.1 alignment kernels, compiled to WASM SIMD128.
        # sse2only selects the non-dispatch object names; -msse4.1 retains SSE4.1.
        portable_simd=1
        compile_flags+=' -msse4.1 -Werror=format -include port.h'
        libraries='-lm'
    fi
    if [[ "$variant" == wasm ]]; then
        python3 - "$work/source/Makefile" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
text = path.read_text()
old = '-o $@ -L. -lminimap2 $(LIBS)'
assert old in text
path.write_text(text.replace(old, '-o $@.mjs -L. -lminimap2 $(LDFLAGS) $(LIBS)'))
PY
    fi
    (
        cd "$work/zlib"
        CC="$cc" AR="$ar" RANLIB="$ranlib" CFLAGS='-O3' ./configure --static --prefix="$work/zlib-prefix"
        make clean
        make -j"$JOBS"
        make install
    )
    (
        cd "$work/source"
        make clean
        make -j"$JOBS" CC="$cc" AR="$ar" sse2only="${portable_simd:-}" CFLAGS="$compile_flags" CPPFLAGS="$defines" \
            INCLUDES="-I$work/zlib-prefix/include" LDFLAGS="$link_flags" \
            LIBS="$work/zlib-prefix/lib/libz.a $libraries"
    )
    if [[ "$variant" == wasm ]]; then
        cp "$work/source/minimap2.mjs" "$work/source/minimap2.wasm" "$ROOT/dist/minimap2/"
        cp "$ROOT/tools/minimap2/LICENSE.txt" "$ROOT/tools/minimap2/sources.lock.json" "$ROOT/dist/minimap2/"
        cp "$work/zlib/LICENSE" "$ROOT/dist/minimap2/LICENSE.zlib"
        # Exact upstream inputs plus our patch/build instructions accompany releases.
        cp "$MINIMAP2_ARCHIVE" "$ZLIB_ARCHIVE" "$ROOT/dist/minimap2/"
        cp "$ROOT/tools/minimap2/browser.patch" "$ROOT/tools/minimap2/port.h" "$ROOT/dist/minimap2/"
    else
        mkdir -p "$ROOT/build/$variant"
        cp "$work/source/minimap2" "$ROOT/build/$variant/minimap2"
    fi
}

if [[ "$MODE" == all ]]; then
    build_variant native
    build_variant patched-native
    build_variant wasm
else
    build_variant "$MODE"
fi
