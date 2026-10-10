#!/usr/bin/env bash
# Pinned upstream, patched-native and WebAssembly builds of BWA-MEM2.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
MODE=${1:-all}
case "$MODE" in all|native|patched-native|wasm) ;; *) echo 'Usage: scripts/build-bwa-mem2.sh [all|native|patched-native|wasm]' >&2; exit 2;; esac
JOBS=${JOBS:-2}
BUILD_ROOT=${BWA_MEM2_BUILD_ROOT:-"$ROOT/build/bwa-mem2"}
LOCK="$ROOT/tools/bwa-mem2/sources.lock.json"
mkdir -p "$ROOT/build/downloads" "$BUILD_ROOT" "$ROOT/dist/bwa-mem2"
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
UPSTREAM_ARCHIVE="$ROOT/build/downloads/bwa-mem2-$(lock_value revision).tar.gz"
SAFE_ARCHIVE="$ROOT/build/downloads/safestringlib-$(lock_value dependencies.safestringlib.revision).tar.gz"
ZLIB_ARCHIVE="$ROOT/build/downloads/zlib-$(lock_value dependencies.zlib.version).tar.gz"
download "$(lock_value url)" "$UPSTREAM_ARCHIVE" "$(lock_value sha256)"
download "$(lock_value dependencies.safestringlib.url)" "$SAFE_ARCHIVE" "$(lock_value dependencies.safestringlib.sha256)"
download "$(lock_value dependencies.zlib.url)" "$ZLIB_ARCHIVE" "$(lock_value dependencies.zlib.sha256)"

build_variant() {
    local variant=$1 work="$BUILD_ROOT/$1" cc=${CC:-clang} cxx=${CXX:-clang++} ar=${AR:-ar} ranlib=${RANLIB:-ranlib}
    local flags='-O3 -pipe -std=gnu++11 -msse4.1' link_flags='' extension='' libraries='-lm -lpthread'
    if [[ "$variant" != wasm ]] && ! command -v "$cxx" >/dev/null; then
        local sdk=${EMSDK:-"$ROOT/toolchain/emsdk"}
        if [[ -x "$sdk/upstream/bin/clang++" ]]; then
            cc="$sdk/upstream/bin/clang"; cxx="$sdk/upstream/bin/clang++"
        else
            echo 'Install Clang for the unmodified native BWA-MEM2 baseline (GCC 13 has an upstream intrinsic collision).' >&2; exit 1
        fi
    fi
    if [[ "$variant" == wasm ]]; then
        if ! command -v emcc >/dev/null; then
            if [[ -n "${EMSDK:-}" && -f "$EMSDK/emsdk_env.sh" ]]; then
                set +u; source "$EMSDK/emsdk_env.sh"; set -u
            else
                echo 'Activate Emscripten 3.1.64 or set EMSDK to its directory.' >&2; exit 1
            fi
        fi
        if [[ $(emcc --version | head -n 1) != *"$(lock_value emscripten)"* ]]; then
            echo "Expected Emscripten $(lock_value emscripten); found $(emcc --version | head -n 1)" >&2; exit 1
        fi
        cc=emcc; cxx=em++; ar=emar; ranlib=emranlib
        flags+=' -msimd128'
        extension='.mjs'; libraries='-lm'
        link_flags='-Wl,--fatal-warnings -sMODULARIZE=1 -sEXPORT_ES6=1 -sEXPORT_NAME=createBwaMem2 -sINVOKE_RUN=0 -sEXPORTED_RUNTIME_METHODS=FS,callMain -sINCOMING_MODULE_JS_API=preRun,noInitialRun,stdin,stdout,stderr,onExit,quit,locateFile,wasmBinary,print,printErr,onAbort,thisProgram -sENVIRONMENT=web,worker,node -sALLOW_MEMORY_GROWTH=1 -sABORTING_MALLOC=1 -sINITIAL_MEMORY=33554432 -sMAXIMUM_MEMORY=2147483648 -sSTACK_SIZE=8388608 -sEXIT_RUNTIME=1 -sFORCE_FILESYSTEM=1'
    fi
    rm -rf -- "$work/source" "$work/zlib" "$work/zlib-prefix"
    mkdir -p "$work/source" "$work/zlib" "$work/zlib-prefix"
    tar --no-same-owner -xzf "$UPSTREAM_ARCHIVE" -C "$work/source" --strip-components=1
    tar --no-same-owner -xzf "$SAFE_ARCHIVE" -C "$work/source/ext/safestringlib" --strip-components=1
    tar --no-same-owner -xzf "$ZLIB_ARCHIVE" -C "$work/zlib" --strip-components=1
    if [[ "$variant" != native ]]; then
        # Upstream mixes CRLF and LF; normalize only the patch target tree.
        python3 - "$work/source/src" <<'PY'
from pathlib import Path
import sys
for path in Path(sys.argv[1]).iterdir():
    if path.suffix in ('.cpp', '.h'):
        path.write_bytes(path.read_bytes().replace(b'\r\n', b'\n'))
PY
        patch --batch --forward -d "$work/source" -p1 < "$ROOT/tools/bwa-mem2/browser.patch"
        cp "$ROOT/tools/bwa-mem2/port.h" "$work/source/port.h"
        flags+=' -include port.h -Werror=format'
    fi
    (
        cd "$work/zlib"
        CC="$cc" AR="$ar" RANLIB="$ranlib" CFLAGS='-O3 -pipe' ./configure --static --prefix="$work/zlib-prefix"
        make -j"$JOBS"
        make install
    )
    # Use the exact translation units and defines from the pinned upstream
    # Makefile. Explicit compiler/archive variables also work with Emscripten.
    python3 - "$work/source" <<'PY'
from pathlib import Path
import re, sys
root = Path(sys.argv[1])
makefile = (root / 'Makefile').read_text().replace('\\\n', ' ')
match = re.search(r'^OBJS=\s*(.*)$', makefile, re.M)
assert match, 'Pinned upstream object list is missing'
objects = match.group(1).split() + ['src/main.o']
safe = sorted(p.relative_to(root).with_suffix('.o').as_posix() for p in (root/'ext/safestringlib/safeclib').glob('*.c'))
(root/'port.mk').write_text('''CXXFLAGS += -DV17=1 -DMATE_SORT=0 -DSAIS=1 -DENABLE_PREFETCH -Isrc -Iext/safestringlib/include
CFLAGS += -O3 -pipe -include stdlib.h -include ctype.h -Iext/safestringlib/include
all: bwa-mem2$(EXTENSION)
OBJS = ''' + ' '.join(objects) + '''
SAFE_OBJS = ''' + ' '.join(safe) + '''
%.o: %.cpp
\t$(CXX) $(CXXFLAGS) -c $< -o $@
%.o: %.c
\t$(CC) $(CFLAGS) -c $< -o $@
libsafestring.a: $(SAFE_OBJS)
\t$(AR) rcs $@ $^
bwa-mem2$(EXTENSION): $(OBJS) libsafestring.a
\t$(CXX) $(CXXFLAGS) $(LDFLAGS) $(OBJS) libsafestring.a $(LIBS) -o $@
''')
PY
    (
        cd "$work/source"
        make -f port.mk -j"$JOBS" CC="$cc" CXX="$cxx" AR="$ar" \
          CXXFLAGS="$flags -I$work/zlib-prefix/include -DV17=1 -DMATE_SORT=0 -DSAIS=1 -DENABLE_PREFETCH -Isrc -Iext/safestringlib/include" \
          LDFLAGS="$link_flags" EXTENSION="$extension" LIBS="$work/zlib-prefix/lib/libz.a $libraries"
    )
    if [[ "$variant" == wasm ]]; then
        cp "$work/source/bwa-mem2.mjs" "$work/source/bwa-mem2.wasm" "$ROOT/dist/bwa-mem2/"
        cp "$ROOT/tools/bwa-mem2/LICENSE" "$ROOT/tools/bwa-mem2/sources.lock.json" "$ROOT/dist/bwa-mem2/"
        cp "$work/source/ext/safestringlib/LICENSE" "$ROOT/dist/bwa-mem2/LICENSE.safestringlib"
        cp "$work/source/ext/safestringlib/LICENSE&COPYING.txt" "$ROOT/dist/bwa-mem2/LICENSE-COPYING.safestringlib.txt"
        cp "$work/zlib/LICENSE" "$ROOT/dist/bwa-mem2/LICENSE.zlib"
        cp "$UPSTREAM_ARCHIVE" "$SAFE_ARCHIVE" "$ZLIB_ARCHIVE" "$ROOT/dist/bwa-mem2/"
        cp "$ROOT/tools/bwa-mem2/browser.patch" "$ROOT/tools/bwa-mem2/port.h" "$ROOT/dist/bwa-mem2/"
    else
        mkdir -p "$ROOT/build/$variant"
        cp "$work/source/bwa-mem2" "$ROOT/build/$variant/bwa-mem2"
    fi
}
if [[ "$MODE" == all ]]; then
    build_variant native
    build_variant patched-native
    build_variant wasm
else
    build_variant "$MODE"
fi
