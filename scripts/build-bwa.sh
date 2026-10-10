#!/usr/bin/env bash
# Build the upstream baseline, patched baseline and standalone WASM package.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
MODE=${1:-all}
case "$MODE" in all|native|patched-native|wasm) ;; *) echo 'Usage: scripts/build-bwa.sh [all|native|patched-native|wasm]' >&2; exit 2;; esac
JOBS=${JOBS:-2}
BUILD_ROOT=${BWA_BUILD_ROOT:-"$ROOT/build/bwa"}
LOCK="$ROOT/tools/bwa/sources.lock.json"
mkdir -p "$ROOT/build/downloads" "$BUILD_ROOT" "$ROOT/dist/bwa"

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

BWA_ARCHIVE="$ROOT/build/downloads/bwa-$(lock_value revision).tar.gz"
ZLIB_ARCHIVE="$ROOT/build/downloads/zlib-$(lock_value dependencies.zlib.version).tar.gz"
download "$(lock_value url)" "$BWA_ARCHIVE" "$(lock_value sha256)"
download "$(lock_value dependencies.zlib.url)" "$ZLIB_ARCHIVE" "$(lock_value dependencies.zlib.sha256)"

build_variant() {
    local variant=$1 work="$BUILD_ROOT/$1" cc=${CC:-cc} ar=${AR:-ar} ranlib=${RANLIB:-ranlib}
    local compile_flags='-O3 -Wall -Wno-unused-function' defines='-DHAVE_PTHREAD -DUSE_MALLOC_WRAPPERS'
    local link_flags='' libraries='-lm -lpthread -lrt'
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
        libraries='-lm'
        link_flags='-Wl,--fatal-warnings -sMODULARIZE=1 -sEXPORT_ES6=1 -sEXPORT_NAME=createBwa -sINVOKE_RUN=0 -sEXPORTED_RUNTIME_METHODS=FS,callMain -sINCOMING_MODULE_JS_API=preRun,noInitialRun,stdin,stdout,stderr,onExit,quit,locateFile,wasmBinary,print,printErr,onAbort,thisProgram -sENVIRONMENT=web,worker,node -sALLOW_MEMORY_GROWTH=1 -sINITIAL_MEMORY=33554432 -sMAXIMUM_MEMORY=2147483648 -sSTACK_SIZE=8388608 -sEXIT_RUNTIME=1 -sFORCE_FILESYSTEM=1'
    fi
    # Start from pristine input on every invocation; never apply patches over
    # an earlier build tree or retain old configure results.
    rm -rf -- "$work/source" "$work/zlib" "$work/zlib-prefix"
    mkdir -p "$work/source" "$work/zlib" "$work/zlib-prefix"
    tar --no-same-owner -xzf "$BWA_ARCHIVE" -C "$work/source" --strip-components=1
    tar --no-same-owner -xzf "$ZLIB_ARCHIVE" -C "$work/zlib" --strip-components=1
    if [[ "$variant" != native ]]; then
        patch --batch --forward -d "$work/source" -p1 < "$ROOT/tools/bwa/browser.patch"
        for replacement in kthread.c kopen.c bwashm.c; do
            cmp "$work/source/$replacement" "$ROOT/tools/bwa/$replacement"
        done
        cp "$ROOT/tools/bwa/port.h" "$work/source/port.h"
        # Upstream's own scalar implementation is also exercised natively.
        compile_flags+=' -U__SSE2__ -U__ARM_NEON -U__ARM_NEON__ -include port.h'
        defines='-DUSE_MALLOC_WRAPPERS'
        libraries='-lm'
    fi
    if [[ "$variant" == wasm ]]; then
        python3 - "$work/source/Makefile" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
text = path.read_text()
old = '-o $@ -L. -lbwa'
assert old in text
path.write_text(text.replace(old, '-o $@.mjs -L. -lbwa'))
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
        make -j"$JOBS" CC="$cc" AR="$ar" CFLAGS="$compile_flags" DFLAGS="$defines" \
            INCLUDES="-I$work/zlib-prefix/include" LDFLAGS="$link_flags" \
            LIBS="$work/zlib-prefix/lib/libz.a $libraries"
    )
    if [[ "$variant" == wasm ]]; then
        cp "$work/source/bwa.mjs" "$work/source/bwa.wasm" "$ROOT/dist/bwa/"
        cp "$ROOT/tools/bwa/COPYING" "$ROOT/tools/bwa/sources.lock.json" "$ROOT/dist/bwa/"
        cp "$work/zlib/LICENSE" "$ROOT/dist/bwa/LICENSE.zlib"
        # Exact upstream inputs plus our patch/build instructions accompany releases.
        cp "$BWA_ARCHIVE" "$ZLIB_ARCHIVE" "$ROOT/dist/bwa/"
        cp "$ROOT/tools/bwa/browser.patch" "$ROOT/tools/bwa/port.h" "$ROOT/dist/bwa/"
    else
        mkdir -p "$ROOT/build/$variant"
        cp "$work/source/bwa" "$ROOT/build/$variant/bwa"
    fi
}

if [[ "$MODE" == all ]]; then
    build_variant native
    build_variant patched-native
    build_variant wasm
else
    build_variant "$MODE"
fi
