#!/usr/bin/env bash
# Build pinned unchanged native, serial patched-native, and browser StringTie.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
MODE=${1:-all}
case "$MODE" in all|native|patched-native|wasm) ;; *) echo 'Usage: scripts/build-stringtie.sh [all|native|patched-native|wasm]' >&2; exit 2;; esac
JOBS=${JOBS:-2}
BUILD_ROOT=${STRINGTIE_BUILD_ROOT:-"$ROOT/build/stringtie"}
LOCK="$ROOT/tools/stringtie/sources.lock.json"
mkdir -p "$ROOT/build/downloads" "$BUILD_ROOT/tmp" "$ROOT/dist/stringtie"
export TMPDIR="$BUILD_ROOT/tmp"
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
path = pathlib.Path(sys.argv[1]); actual = hashlib.sha256(path.read_bytes()).hexdigest()
if actual != sys.argv[2]:
    raise SystemExit(f'Checksum mismatch: {path}: expected {sys.argv[2]}, got {actual}')
PY
}
ARCHIVE="$ROOT/build/downloads/stringtie-$(lock_value version).offline.tar.gz"
ZLIB_ARCHIVE="$ROOT/build/downloads/zlib-$(lock_value dependencies.zlib.version).tar.gz"
download "$(lock_value url)" "$ARCHIVE" "$(lock_value sha256)"
download "$(lock_value dependencies.zlib.url)" "$ZLIB_ARCHIVE" "$(lock_value dependencies.zlib.sha256)"

build_variant() {
    local variant=$1 work="$BUILD_ROOT/$1"
    local cc=${CC:-cc} cxx=${CXX:-c++} ar=${AR:-ar} ranlib=${RANLIB:-ranlib}
    local flags='-O3 -DNDEBUG' link_flags='' extension='' serial='' pthread_flags='-pthread' configure_host=()
    if [[ "$variant" == wasm ]]; then
        if ! command -v em++ >/dev/null; then
            local sdk=${EMSDK:-"$ROOT/toolchain/emsdk"}
            if [[ -f "$sdk/emsdk_env.sh" ]]; then
                set +u; source "$sdk/emsdk_env.sh"; set -u
            else
                echo 'Activate Emscripten 3.1.64 or set EMSDK to its directory.' >&2; exit 1
            fi
        fi
        if [[ $(em++ --version | head -n 1) != *"$(lock_value emscripten)"* ]]; then
            echo "Expected Emscripten $(lock_value emscripten)" >&2; exit 1
        fi
        cc=emcc; cxx=em++; ar=emar; ranlib=emranlib; extension='.mjs'
        configure_host=(--host=wasm32-unknown-emscripten)
        link_flags='-Wl,--fatal-warnings -sMODULARIZE=1 -sEXPORT_ES6=1 -sEXPORT_NAME=createStringTie -sINVOKE_RUN=0 -sEXPORTED_RUNTIME_METHODS=FS,callMain -sINCOMING_MODULE_JS_API=preRun,noInitialRun,stdin,stdout,stderr,onExit,quit,locateFile,wasmBinary,print,printErr,onAbort,thisProgram -sENVIRONMENT=web,worker,node -sALLOW_MEMORY_GROWTH=1 -sABORTING_MALLOC=1 -sINITIAL_MEMORY=67108864 -sMAXIMUM_MEMORY=2147483648 -sSTACK_SIZE=8388608 -sEXIT_RUNTIME=1 -sFORCE_FILESYSTEM=1'
    fi
    rm -rf -- "$work/source" "$work/zlib" "$work/prefix"
    mkdir -p "$work/source" "$work/zlib" "$work/prefix"
    python3 "$ROOT/tools/stringtie/provenance.py" record "$ROOT" "$work/build-inputs.json"
    tar --no-same-owner -xzf "$ARCHIVE" -C "$work/source" --strip-components=1
    tar --no-same-owner -xzf "$ZLIB_ARCHIVE" -C "$work/zlib" --strip-components=1
    python3 "$ROOT/tools/stringtie/provenance.py" verify-trees "$LOCK" "$work/source"
    if [[ "$variant" != native ]]; then
        patch --batch --forward -d "$work/source" -p1 < "$ROOT/tools/stringtie/browser.patch"
        serial=1
        pthread_flags=''
    fi
    (
        cd "$work/zlib"
        CC="$cc" AR="$ar" RANLIB="$ranlib" CFLAGS="$flags" ./configure --static --prefix="$work/prefix"
        make -j"$JOBS"
        make install
    )
    # The upstream offline archive contains these exact compression sources.
    # Build them directly so all toolchain variables reach the sub-builds.
    make -C "$work/source/htslib/libdeflate" -f ../Makefile.libdeflate -j"$JOBS" \
        libdeflate.a CC="$cc" CXX="$cxx" AR="$ar" CFLAGS="$flags"
    cp "$work/source/htslib/libdeflate/"{libdeflate.h,libdeflate.a} "$work/prefix/"
    cp "$work/source/htslib/libdeflate/libdeflate.h" "$work/prefix/include/"
    cp "$work/source/htslib/libdeflate/libdeflate.a" "$work/prefix/lib/"
    make -C "$work/source/htslib/bzip2" -j"$JOBS" libbz2.a \
        CC="$cc" AR="$ar" RANLIB="$ranlib" CFLAGS="$flags -D_FILE_OFFSET_BITS=64"
    cp "$work/source/htslib/bzip2/bzlib.h" "$work/prefix/include/"
    cp "$work/source/htslib/bzip2/libbz2.a" "$work/prefix/lib/"
    (
        cd "$work/source/htslib/lzma"
        CC="$cc" CXX="$cxx" AR="$ar" RANLIB="$ranlib" CFLAGS="$flags" \
            ./configure "${configure_host[@]}" --disable-shared --disable-xz --disable-xzdec \
            --disable-lzmadec --disable-lzmainfo --disable-nls --disable-scripts --disable-doc \
            --disable-threads --prefix="$work/prefix"
        make -j"$JOBS"
        make install
    )
    make -C "$work/source/htslib" -j"$JOBS" lib-static \
        CC="$cc" CXX="$cxx" AR="$ar" RANLIB="$ranlib" \
        CFLAGS="$flags -fvisibility=hidden" CPPFLAGS="-I$work/prefix/include" \
        LDFLAGS="-L$work/prefix/lib"
    # Reuse upstream's complete StringTie translation-unit list and serial path.
    # Command-line variables remove CPU-specific flags from dependency builds.
    make -C "$work/source" -j"$JOBS" release \
        CXX="$cxx" LINKER="$cxx" NOTHREADS="$serial" EXE="$extension" \
        CXXFLAGS="$flags -Wall -Wextra -I. -Igclib -Ihtslib -I$work/prefix/include -fsigned-char -D_FILE_OFFSET_BITS=64 -D_LARGEFILE_SOURCE -std=c++11 -fno-strict-aliasing -fno-exceptions -fno-rtti ${serial:+-DNOTHREADS}" \
        LDFLAGS="$link_flags" \
        LIBS="htslib/libhts.a $work/prefix/lib/libbz2.a $work/prefix/lib/liblzma.a $work/prefix/lib/libdeflate.a $work/prefix/lib/libz.a -lm $pthread_flags"
    if [[ "$variant" == wasm ]]; then
        cp "$work/source/stringtie.mjs" "$work/source/stringtie.wasm" "$ROOT/dist/stringtie/"
        cp "$ROOT/tools/stringtie/"{LICENSE*,THIRD_PARTY_LICENSES.txt,sources.lock.json,browser.patch} "$ROOT/dist/stringtie/"
        cp "$work/zlib/LICENSE" "$ROOT/dist/stringtie/LICENSE.zlib"
        cp "$ARCHIVE" "$ZLIB_ARCHIVE" "$ROOT/dist/stringtie/"
        python3 "$ROOT/tools/stringtie/provenance.py" finish "$ROOT" "$work/build-inputs.json"
    else
        mkdir -p "$ROOT/build/$variant"
        cp "$work/source/stringtie" "$ROOT/build/$variant/stringtie"
    fi
}
if [[ "$MODE" == all ]]; then
    build_variant native
    build_variant patched-native
    build_variant wasm
else
    build_variant "$MODE"
fi
