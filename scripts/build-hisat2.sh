#!/usr/bin/env bash
# Pinned original-native, patched-native and WebAssembly HISAT2 builds.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
MODE=${1:-all}
case "$MODE" in all|native|patched-native|wasm) ;; *) echo 'Usage: scripts/build-hisat2.sh [all|native|patched-native|wasm]' >&2; exit 2;; esac
JOBS=${JOBS:-2}
BUILD_ROOT=${HISAT2_BUILD_ROOT:-"$ROOT/build/hisat2"}
LOCK="$ROOT/tools/hisat2/sources.lock.json"
mkdir -p "$ROOT/build/downloads" "$BUILD_ROOT" "$ROOT/dist/hisat2"
mkdir -p "$BUILD_ROOT/tmp"
export TMPDIR="$BUILD_ROOT/tmp"
lock_value() { python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))[sys.argv[2]])' "$LOCK" "$1"; }
ARCHIVE="$ROOT/build/downloads/hisat2-$(lock_value revision).tar.gz"
if [[ ! -f "$ARCHIVE" ]]; then
    curl --fail --location --retry 3 --max-time 300 "$(lock_value url)" -o "$ARCHIVE.tmp"
    mv "$ARCHIVE.tmp" "$ARCHIVE"
fi
python3 - "$ARCHIVE" "$(lock_value sha256)" <<'PY'
import hashlib, pathlib, sys
p=pathlib.Path(sys.argv[1]); actual=hashlib.sha256(p.read_bytes()).hexdigest()
if actual != sys.argv[2]: raise SystemExit(f'Checksum mismatch: {p}: {actual}')
PY

build_variant() {
    local variant=$1 work="$BUILD_ROOT/$1" cxx=${CXX:-clang++}
    local flags='-O3 -std=c++11 -msse2 -funroll-loops -fno-strict-aliasing -DNDEBUG -D_LARGEFILE_SOURCE -D_FILE_OFFSET_BITS=64 -D_GNU_SOURCE -DBOWTIE2 -DPOPCNT_CAPABILITY -I. -Ithird_party' link_flags='-lpthread' extension=''
    if [[ "$variant" != wasm ]] && ! command -v "$cxx" >/dev/null; then
        local sdk=${EMSDK:-"$ROOT/toolchain/emsdk"}
        if [[ -x "$sdk/upstream/bin/clang++" ]]; then cxx="$sdk/upstream/bin/clang++";
        else echo 'Install Clang or set CXX for native HISAT2 builds.' >&2; exit 1; fi
    fi
    if [[ "$variant" == wasm ]]; then
        if ! command -v em++ >/dev/null; then
            if [[ -n "${EMSDK:-}" && -f "$EMSDK/emsdk_env.sh" ]]; then
                set +u; source "$EMSDK/emsdk_env.sh"; set -u
            else
                echo 'Activate Emscripten 3.1.64 or set EMSDK to its directory.' >&2; exit 1
            fi
        fi
        if [[ $(em++ --version | head -n1) != *"$(lock_value emscripten)"* ]]; then
            echo "Expected Emscripten $(lock_value emscripten)" >&2; exit 1
        fi
        cxx=em++; extension='.mjs'; flags+=' -msimd128 -fexceptions'
        link_flags='-Wl,--fatal-warnings -sDISABLE_EXCEPTION_CATCHING=0 -sMODULARIZE=1 -sEXPORT_ES6=1 -sEXPORT_NAME=createHisat2 -sINVOKE_RUN=0 -sEXPORTED_RUNTIME_METHODS=FS,callMain -sINCOMING_MODULE_JS_API=preRun,noInitialRun,stdin,stdout,stderr,onExit,quit,locateFile,wasmBinary,print,printErr,onAbort,thisProgram -sENVIRONMENT=web,worker,node -sALLOW_MEMORY_GROWTH=1 -sINITIAL_MEMORY=33554432 -sMAXIMUM_MEMORY=2147483648 -sSTACK_SIZE=8388608 -sEXIT_RUNTIME=1 -sFORCE_FILESYSTEM=1'
    fi
    rm -rf -- "$work/source"
    mkdir -p "$work/source"
    tar --no-same-owner -xzf "$ARCHIVE" -C "$work/source" --strip-components=1
    if [[ "$variant" == native ]]; then
        make -C "$work/source" -j"$JOBS" CXX="$cxx" RELEASE_FLAGS='-O3 -msse2 -funroll-loops' hisat2-build-s hisat2-align-s hisat2-inspect-s
        mkdir -p "$ROOT/build/native/hisat2-bin"
        cp "$work/source/hisat2-build-s" "$work/source/hisat2-align-s" "$work/source/hisat2-inspect-s" "$ROOT/build/native/hisat2-bin/"
        cat > "$ROOT/build/native/hisat2" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/hisat2-bin" && pwd)
case "${1:-}" in
  build) shift; exec "$dir/hisat2-build-s" "$@";;
  align) shift; exec "$dir/hisat2-align-s" "$@";;
  inspect) shift; exec "$dir/hisat2-inspect-s" "$@";;
  -*) exec "$dir/hisat2-align-s" "$@";;
  *) echo 'Usage: hisat2 build|align|inspect [arguments]' >&2; exit 1;;
esac
SH
        chmod +x "$ROOT/build/native/hisat2"
        return
    fi
    patch --batch --forward -d "$work/source" -p1 < "$ROOT/tools/hisat2/browser.patch"
    cp "$ROOT/tools/hisat2/dispatch.cpp" "$work/source/port_dispatch.cpp"
    flags+=' -DHISAT2_WASM_PORT -Werror=format'
    python3 - "$work/source" <<'PY'
from pathlib import Path
import re,sys
root=Path(sys.argv[1]); mf=(root/'Makefile').read_text().replace('\\\n',' ')
def sources(var):
    m=re.search(r'^'+var+r'\s*=\s*(.*)$',mf,re.M)
    assert m,var
    return m.group(1).split()
src=list(dict.fromkeys(sources('SHARED_CPPS')+sources('SEARCH_CPPS')+sources('BUILD_CPPS')+['hisat2.cpp','hisat2_build.cpp','hisat2_inspect.cpp','port_dispatch.cpp']))
objects=' '.join(s[:-4]+'.o' for s in src)
(root/'port.mk').write_text('''DEFS = -DHISAT2_VERSION='"'''+(root/'VERSION').read_text().strip()+'''"' -DBUILD_HOST='"portable"' -DBUILD_TIME='"pinned source build"' -DCOMPILER_VERSION='"C++11"' -DCOMPILER_OPTIONS='"small index, SSE2, single worker"'
all: hisat2$(EXTENSION)
OBJS = '''+objects+'''
hisat2_build.o: hisat2_build.cpp
\t$(CXX) $(CXXFLAGS) $(DEFS) -DMASSIVE_DATA_RLCSA -c $< -o $@
%.o: %.cpp
\t$(CXX) $(CXXFLAGS) $(DEFS) -c $< -o $@
hisat2$(EXTENSION): $(OBJS)
\t$(CXX) $(CXXFLAGS) $(OBJS) $(LDFLAGS) -o $@
''')
PY
    make -C "$work/source" -f port.mk -j"$JOBS" CXX="$cxx" CXXFLAGS="$flags" LDFLAGS="$link_flags" EXTENSION="$extension"
    if [[ "$variant" == wasm ]]; then
        cp "$work/source/hisat2.mjs" "$work/source/hisat2.wasm" "$ROOT/dist/hisat2/"
        cp "$ROOT/tools/hisat2/LICENSE" "$ROOT/tools/hisat2/sources.lock.json" "$ROOT/tools/hisat2/browser.patch" "$ROOT/tools/hisat2/dispatch.cpp" "$ROOT/dist/hisat2/"
        cp "$ROOT/tools/hisat2/THIRD_PARTY_LICENSES.txt" "$ROOT/dist/hisat2/"
        cp "$ARCHIVE" "$ROOT/dist/hisat2/"
    else
        mkdir -p "$ROOT/build/patched-native"
        cp "$work/source/hisat2" "$ROOT/build/patched-native/hisat2"
    fi
}
if [[ "$MODE" == all ]]; then
    build_variant native
    build_variant patched-native
    build_variant wasm
else
    build_variant "$MODE"
fi
