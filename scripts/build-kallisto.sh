#!/usr/bin/env bash
# Build unchanged upstream, patched-native and standalone WebAssembly kallisto.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
MODE=${1:-all}
case "$MODE" in all|native|patched-native|wasm) ;; *) echo 'Usage: scripts/build-kallisto.sh [all|native|patched-native|wasm]' >&2; exit 2;; esac
JOBS=${JOBS:-2}
BUILD_ROOT=${KALLISTO_BUILD_ROOT:-"$ROOT/build/kallisto"}
LOCK="$ROOT/tools/kallisto/sources.lock.json"
mkdir -p "$ROOT/build/downloads" "$BUILD_ROOT/tmp" "$ROOT/dist/kallisto"
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
ARCHIVE="$ROOT/build/downloads/kallisto-$(lock_value revision).tar.gz"
ZLIB_ARCHIVE="$ROOT/build/downloads/zlib-$(lock_value dependencies.zlib.version).tar.gz"
download "$(lock_value url)" "$ARCHIVE" "$(lock_value sha256)"
download "$(lock_value dependencies.zlib.url)" "$ZLIB_ARCHIVE" "$(lock_value dependencies.zlib.sha256)"

build_variant() {
    local variant=$1 work="$BUILD_ROOT/$1"
    local cc=${CC:-cc} cxx=${CXX:-c++} ar=${AR:-ar} ranlib=${RANLIB:-ranlib}
    local flags='-O3 -DNDEBUG -DMAX_KMER_SIZE=32 -DMAX_GMER_SIZE=32 -DNO_HTSLIB=ON -DDISABLEAVX'
    local cxx_extra='' link_flags='-pthread -lm' extension='' zlib_flags='-O3'
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
        flags+=' -sMEMORY64=2'; zlib_flags+=' -sMEMORY64=2'
        cxx_extra='-fexceptions'
        link_flags='-sMEMORY64=2 -Wl,--fatal-warnings -fexceptions -sDISABLE_EXCEPTION_CATCHING=0 -sMODULARIZE=1 -sEXPORT_ES6=1 -sEXPORT_NAME=createKallisto -sINVOKE_RUN=0 -sEXPORTED_RUNTIME_METHODS=FS,callMain -sINCOMING_MODULE_JS_API=preRun,noInitialRun,stdin,stdout,stderr,onExit,quit,locateFile,wasmBinary,print,printErr,onAbort,thisProgram -sENVIRONMENT=web,worker,node -sALLOW_MEMORY_GROWTH=1 -sABORTING_MALLOC=1 -sINITIAL_MEMORY=67108864 -sMAXIMUM_MEMORY=2147483648 -sSTACK_SIZE=8388608 -sEXIT_RUNTIME=1 -sFORCE_FILESYSTEM=1'
    fi
    rm -rf -- "$work/source" "$work/zlib" "$work/zlib-prefix"
    mkdir -p "$work/source" "$work/zlib" "$work/zlib-prefix"
    if [[ "$variant" == wasm ]]; then
        python3 - "$ROOT" "$work/build-inputs.json" <<'PY'
from pathlib import Path
import hashlib, json, sys
root, output = Path(sys.argv[1]), Path(sys.argv[2])
suffixes = {'.patch', '.h', '.hpp', '.c', '.cc', '.cpp', '.rs', '.py', '.sh', '.json', '.lock'}
paths = {root/'scripts/build-kallisto.sh'}
paths.update(path for path in (root/'tools/kallisto').rglob('*')
             if path.is_file() and path.suffix in suffixes)
inputs = {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
          for path in sorted(paths)}
output.write_text(json.dumps(inputs, indent=2, sort_keys=True) + '\n')
PY
    fi
    tar --no-same-owner -xzf "$ARCHIVE" -C "$work/source" --strip-components=1
    tar --no-same-owner -xzf "$ZLIB_ARCHIVE" -C "$work/zlib" --strip-components=1
    if [[ "$variant" != native ]]; then
        patch --batch --forward -d "$work/source" -p1 < "$ROOT/tools/kallisto/browser.patch"
        cp "$ROOT/tools/kallisto/serial-thread.h" "$work/source/serial-thread.h"
        cxx_extra+=' -include serial-thread.h'
    fi
    (
        cd "$work/zlib"
        CC="$cc" AR="$ar" RANLIB="$ranlib" CFLAGS="$zlib_flags" ./configure --static --prefix="$work/zlib-prefix"
        make -j"$JOBS"
        make install
    )
    # Use exactly the translation units selected by upstream's src and Bifrost
    # CMakeLists.txt. A small makefile avoids an ExternalProject subprocess that
    # loses the cross-compilation toolchain, and does not alter native sources.
    python3 - "$work/source" <<'PY'
from pathlib import Path
import sys
root = Path(sys.argv[1])
sources = sorted(root.glob('src/*.cpp'))
sources += sorted(p for p in root.glob('ext/bifrost/src/*.cpp') if p.name != 'Bifrost.cpp')
sources += [root/'ext/bifrost/src/roaring.c']
objects = [p.relative_to(root).with_suffix('.o').as_posix() for p in sources]
(root/'port.mk').write_text('''all: kallisto$(EXTENSION)
OBJS = ''' + ' '.join(objects) + '''
CPPFLAGS += -Isrc -Iext/bifrost/src
%.o: %.cpp
\t$(CXX) $(CPPFLAGS) $(CXXFLAGS) -c $< -o $@
%.o: %.c
\t$(CC) $(CPPFLAGS) $(CFLAGS) -c $< -o $@
kallisto$(EXTENSION): $(OBJS)
\t$(CXX) $(CXXFLAGS) $(OBJS) $(LDFLAGS) $(LDLIBS) -o $@
''')
PY
    make -B -C "$work/source" -f port.mk -j"$JOBS" CC="$cc" CXX="$cxx" \
        CPPFLAGS="-I$work/zlib-prefix/include -Isrc -Iext/bifrost/src" \
        CFLAGS="$flags -std=c11" CXXFLAGS="$flags -std=c++17 $cxx_extra" \
        LDFLAGS="$link_flags" LDLIBS="$work/zlib-prefix/lib/libz.a" EXTENSION="$extension"
    if [[ "$variant" == wasm ]]; then
        cp "$work/source/kallisto.mjs" "$work/source/kallisto.wasm" "$ROOT/dist/kallisto/"
        cp "$ROOT/tools/kallisto/"{LICENSE,LICENSE.bifrost,THIRD_PARTY_LICENSES.txt,sources.lock.json,browser.patch,serial-thread.h} "$ROOT/dist/kallisto/"
        cp "$work/zlib/LICENSE" "$ROOT/dist/kallisto/LICENSE.zlib"
        cp "$ARCHIVE" "$ZLIB_ARCHIVE" "$ROOT/dist/kallisto/"
        python3 - "$ROOT" "$work/build-inputs.json" <<'PY'
from pathlib import Path
import hashlib, json, sys
root, record = Path(sys.argv[1]), Path(sys.argv[2])
inputs = json.loads(record.read_text())
for name, checksum in inputs.items():
    if hashlib.sha256((root/name).read_bytes()).hexdigest() != checksum:
        raise SystemExit(f'Build input changed during compilation: {name}')
dist = root/'dist/kallisto'
assets = {path.relative_to(dist).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
          for path in sorted(dist.rglob('*'))
          if path.is_file() and path.suffix in {'.mjs', '.js', '.wasm'}}
(dist/'build-manifest.json').write_text(json.dumps(
    {'schema': 1, 'tool': 'kallisto', 'inputs': inputs, 'assets': assets},
    indent=2, sort_keys=True) + '\n')
PY
    else
        mkdir -p "$ROOT/build/$variant"
        cp "$work/source/kallisto" "$ROOT/build/$variant/kallisto"
    fi
}
if [[ "$MODE" == all ]]; then
    build_variant native
    build_variant patched-native
    build_variant wasm
else
    build_variant "$MODE"
fi
