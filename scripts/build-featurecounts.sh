#!/usr/bin/env bash
# Build unchanged upstream, patched-native and threaded WebAssembly featureCounts.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
MODE=${1:-all}
case "$MODE" in all|native|patched-native|wasm) ;; *) echo 'Usage: scripts/build-featurecounts.sh [all|native|patched-native|wasm]' >&2; exit 2;; esac
JOBS=${JOBS:-2}
BUILD_ROOT=${FEATURECOUNTS_BUILD_ROOT:-"$ROOT/build/featurecounts"}
LOCK="$ROOT/tools/featurecounts/sources.lock.json"
mkdir -p "$ROOT/build/downloads" "$BUILD_ROOT/tmp" "$ROOT/dist/featurecounts"
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
ARCHIVE="$ROOT/build/downloads/subread-$(lock_value version)-source.tar.gz"
ZLIB_ARCHIVE="$ROOT/build/downloads/zlib-$(lock_value dependencies.zlib.version).tar.gz"
download "$(lock_value url)" "$ARCHIVE" "$(lock_value sha256)"
download "$(lock_value dependencies.zlib.url)" "$ZLIB_ARCHIVE" "$(lock_value dependencies.zlib.sha256)"

build_variant() {
    local variant=$1 work="$BUILD_ROOT/$1" cc=${CC:-cc} ar=${AR:-ar} ranlib=${RANLIB:-ranlib}
    local flags='-O3 -pthread -DMAKE_FOR_EXON -DMAKE_STANDALONE -D_FILE_OFFSET_BITS=64'
    local link_flags='-O3 -pthread' extension='' zlib_flags='-O3'
    if [[ "$variant" == wasm ]]; then
        if ! command -v emcc >/dev/null; then
            local sdk=${EMSDK:-"$ROOT/toolchain/emsdk"}
            if [[ -f "$sdk/emsdk_env.sh" ]]; then
                set +u; source "$sdk/emsdk_env.sh"; set -u
            else
                echo 'Activate Emscripten3.1.64 or set EMSDK to its directory.' >&2; exit 1
            fi
        fi
        [[ $(emcc --version | head -n 1) == *"$(lock_value emscripten)"* ]] || { echo "Expected Emscripten $(lock_value emscripten)" >&2; exit 1; }
        cc=emcc; ar=emar; ranlib=emranlib; extension='.mjs'; zlib_flags+=' -pthread'
        link_flags="-O3 -pthread -Wl,--fatal-warnings --js-library $ROOT/tools/featurecounts/mailbox.js -sPROXY_TO_PTHREAD=1 -sMODULARIZE=1 -sEXPORT_ES6=1 -sEXPORT_NAME=createFeatureCounts -sINVOKE_RUN=0 -sEXPORTED_RUNTIME_METHODS=FS,callMain,PThread -sINCOMING_MODULE_JS_API=preRun,noInitialRun,stdin,stdout,stderr,onExit,quit,locateFile,wasmBinary,print,printErr,onAbort,thisProgram,mainScriptUrlOrBlob,instantiateWasm -sENVIRONMENT=web,worker,node -sALLOW_MEMORY_GROWTH=1 -sABORTING_MALLOC=1 -sINITIAL_MEMORY=268435456 -sMAXIMUM_MEMORY=2147483648 -sSTACK_SIZE=8388608 -sDEFAULT_PTHREAD_STACK_SIZE=8388608 -sEXIT_RUNTIME=1 -sFORCE_FILESYSTEM=1 -sPTHREAD_POOL_SIZE=8 -sPTHREAD_POOL_SIZE_STRICT=2 -sASSERTIONS=1"
    fi
    rm -rf -- "$work/source" "$work/zlib" "$work/zlib-prefix"
    mkdir -p "$work/source" "$work/zlib" "$work/zlib-prefix"
    if [[ "$variant" == wasm ]]; then
        python3 - "$ROOT" "$work/build-inputs.json" <<'PY'
from pathlib import Path
import hashlib, json, sys
root, output = Path(sys.argv[1]), Path(sys.argv[2])
suffixes = {'.patch','.h','.hpp','.c','.cc','.cpp','.py','.sh','.json','.js','.lock'}
paths = {root/'scripts/build-featurecounts.sh'}
paths.update(path for path in (root/'tools/featurecounts').rglob('*')
             if path.is_file() and path.suffix in suffixes)
inputs = {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
          for path in sorted(paths)}
output.write_text(json.dumps(inputs, indent=2, sort_keys=True)+'\n')
PY
    fi
    tar --no-same-owner -xzf "$ARCHIVE" -C "$work/source" --strip-components=1
    tar --no-same-owner -xzf "$ZLIB_ARCHIVE" -C "$work/zlib" --strip-components=1
    if [[ "$variant" != native ]]; then
        patch --batch --forward -d "$work/source" -p1 < "$ROOT/tools/featurecounts/browser.patch"
        cp "$ROOT/tools/featurecounts/count-guard.h" "$work/source/src/count-guard.h"
        flags+=' -include count-guard.h'
    fi
    if [[ "$variant" == wasm ]]; then
        python3 - "$work/source/src/Makefile.Linux" <<'PY'
from pathlib import Path
import sys
path=Path(sys.argv[1]); text=path.read_text()
old='-o featureCounts readSummary.c'
assert text.count(old)==1
path.write_text(text.replace(old,'-o featurecounts.mjs readSummary.c'))
PY
    fi
    (
        cd "$work/zlib"
        CC="$cc" AR="$ar" RANLIB="$ranlib" CFLAGS="$zlib_flags" ./configure --static --prefix="$work/zlib-prefix"
        make -j"$JOBS" libz.a
        make install
    )
    flags+=" -DSUBREAD_VERSION=\\\"$(lock_value version)\\\" -I$work/zlib-prefix/include"
    # Upstream's target selects readSummary.c and the complete Subread object list.
    # No upstream source is altered in the native baseline.
    make -B -C "$work/source/src" -f Makefile.Linux featureCounts -j"$JOBS" \
        CC="$cc $flags" LDFLAGS="$link_flags $work/zlib-prefix/lib/libz.a -lm"
    if [[ "$variant" != native ]]; then
        local -a check_command
        if [[ "$variant" == wasm ]]; then
            "$cc" -O2 "$ROOT/tools/featurecounts/count-check.c" -sENVIRONMENT=node \
                -sSINGLE_FILE=1 -sEXIT_RUNTIME=1 -o "$work/count-check.cjs"
            check_command=(node "$work/count-check.cjs")
        else
            "$cc" -O2 "$ROOT/tools/featurecounts/count-check.c" -o "$work/count-check"
            check_command=("$work/count-check")
        fi
        "${check_command[@]}"
        for check_case in overflow merge-overflow; do
            local check_status=0
            "${check_command[@]}" "$check_case" || check_status=$?
            [[ "$check_status" == 2 ]] || { echo "Count guard failed: $check_case exited $check_status" >&2; exit 1; }
        done
    fi
    if [[ "$variant" == wasm ]]; then
        cp "$work/source/src/featurecounts.mjs" "$work/source/src/featurecounts.wasm" "$ROOT/dist/featurecounts/"
        # Older Emscripten revisions generate a separate pthread worker; the
        # pinned SDK may also emit a transition placeholder with an .mjs suffix.
        for worker in "$work/source/src/featurecounts.worker.js" "$work/source/src/featurecounts.worker.mjs"; do
            if [[ -f "$worker" ]]; then cp "$worker" "$ROOT/dist/featurecounts/"; fi
        done
        cp "$ROOT/tools/featurecounts/"{LICENSE,LICENSE.emscripten,THIRD_PARTY_LICENSES.txt,sources.lock.json,browser.patch,mailbox.js,count-guard.h,count-check.c} "$ROOT/dist/featurecounts/"
        cp "$work/zlib/LICENSE" "$ROOT/dist/featurecounts/LICENSE.zlib"
        cp "$ARCHIVE" "$ZLIB_ARCHIVE" "$ROOT/dist/featurecounts/"
        python3 - "$ROOT" "$work/build-inputs.json" <<'PY'
from pathlib import Path
import hashlib, json, sys
root, record = Path(sys.argv[1]), Path(sys.argv[2])
inputs=json.loads(record.read_text())
for name, checksum in inputs.items():
    if hashlib.sha256((root/name).read_bytes()).hexdigest()!=checksum:
        raise SystemExit(f'Build input changed during compilation: {name}')
dist=root/'dist/featurecounts'
assets={path.relative_to(dist).as_posix():hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(dist.rglob('*')) if path.is_file() and path.suffix in {'.mjs','.js','.wasm'}}
(dist/'build-manifest.json').write_text(json.dumps(
    {'schema':1,'tool':'featurecounts','inputs':inputs,'assets':assets,'countGuardSelfTest':'passed'},indent=2,sort_keys=True)+'\n')
PY
    else
        mkdir -p "$ROOT/build/$variant"
        cp "$work/source/src/featureCounts" "$ROOT/build/$variant/featurecounts"
    fi
}
if [[ "$MODE" == all ]]; then
    build_variant native
    build_variant patched-native
    build_variant wasm
else
    build_variant "$MODE"
fi
