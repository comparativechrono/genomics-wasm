#!/usr/bin/env bash
# Build unchanged refc native, patched ORC native and standalone WebAssembly.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
MODE=${1:-all}
case "$MODE" in all|native|patched-native|wasm) ;; *) echo 'Usage: scripts/build-mosdepth.sh [all|native|patched-native|wasm]' >&2; exit 2;; esac
JOBS=${JOBS:-2}
BUILD_ROOT=${MOSDEPTH_BUILD_ROOT:-"$ROOT/build/mosdepth"}
LOCK="$ROOT/tools/mosdepth/sources.lock.json"
mkdir -p "$ROOT/build/downloads" "$BUILD_ROOT/tmp" "$ROOT/dist/mosdepth"
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
python3 - "$LOCK" > "$BUILD_ROOT/archives.tsv" <<'PY'
import json, sys
lock = json.load(open(sys.argv[1]))
for record in [lock, *lock['dependencies'].values()]:
    print(record['archive'], record['url'], record['sha256'], sep='\t')
PY
while IFS=$'\t' read -r archive url checksum; do
    download "$url" "$ROOT/build/downloads/$archive" "$checksum"
done < "$BUILD_ROOT/archives.tsv"
extract() {
    local archive=$1 directory=$2
    rm -rf -- "$directory"
    mkdir -p "$directory"
    tar --no-same-owner -xf "$ROOT/build/downloads/$archive" -C "$directory" --strip-components=1
}
# The pinned official distribution includes the compiler and standard-library
# source as well as the Linux x86-64 bootstrap executable. No Nimble resolution.
[[ $(uname -s) == Linux && $(uname -m) == x86_64 ]] || { echo 'The pinned Nim bootstrap requires Linux x86-64.' >&2; exit 1; }
extract "$(lock_value dependencies.nim.archive)" "$BUILD_ROOT/nim"
NIM="$BUILD_ROOT/nim/bin/nim"
[[ $("$NIM" --version | head -n 1) == *"$(lock_value dependencies.nim.version)"* ]] || { echo 'Unexpected Nim compiler version' >&2; exit 1; }

build_variant() {
    local variant=$1 work="$BUILD_ROOT/$1" cc=${CC:-cc} ar=${AR:-ar} ranlib=${RANLIB:-ranlib}
    local cflags='-O3' link_flags='-lm -lpthread' extension='' memory_manager=refc
    local -a nim_options=() configure_prefix=() configure_options=()
    if [[ "$variant" != native ]]; then memory_manager=orc; nim_options+=(-d:useMalloc); fi
    if [[ "$variant" == wasm ]]; then
        # Keep a caller's known-good Node override when the SDK's bundled binary
        # is unavailable (for example after restoring a workspace cache).
        export EM_NODE_JS=${EM_NODE_JS:-$(command -v node)}
        if ! command -v emcc >/dev/null; then
            local sdk=${EMSDK:-"$ROOT/toolchain/emsdk"}
            if [[ -f "$sdk/emsdk_env.sh" ]]; then
                set +u; source "$sdk/emsdk_env.sh"; set -u
            else
                echo 'Activate Emscripten 3.1.64 or set EMSDK to its directory.' >&2; exit 1
            fi
        fi
        [[ $(emcc --version | head -n 1) == *"$(lock_value emscripten)"* ]] || { echo "Expected Emscripten $(lock_value emscripten)" >&2; exit 1; }
        cc=emcc; ar=emar; ranlib=emranlib; extension='.mjs'; cflags+=' -sMEMORY64=2'
        nim_options+=(--cpu:wasm64 --os:linux -d:emscripten)
        configure_prefix=(emconfigure)
        configure_options=(--host=wasm64-unknown-emscripten)
        link_flags='-O3 -sMEMORY64=2 -Wl,--fatal-warnings -sMODULARIZE=1 -sEXPORT_ES6=1 -sEXPORT_NAME=createMosdepth -sINVOKE_RUN=0 -sEXPORTED_RUNTIME_METHODS=FS,callMain -sINCOMING_MODULE_JS_API=preRun,noInitialRun,stdin,stdout,stderr,onExit,quit,locateFile,wasmBinary,print,printErr,onAbort,thisProgram -sENVIRONMENT=web,worker,node -sALLOW_MEMORY_GROWTH=1 -sABORTING_MALLOC=1 -sINITIAL_MEMORY=67108864 -sMAXIMUM_MEMORY=2147483648 -sSTACK_SIZE=8388608 -sEXIT_RUNTIME=1 -sFORCE_FILESYSTEM=1 -lm'
    fi
    mkdir -p "$work"
    if [[ "$variant" == wasm ]]; then
        python3 - "$ROOT" "$work/build-inputs.json" <<'PY'
from pathlib import Path
import hashlib, json, sys
root, output = Path(sys.argv[1]), Path(sys.argv[2])
suffixes = {'.patch','.h','.hpp','.c','.cc','.cpp','.rs','.py','.sh','.js','.json','.lock','.nim','.nims','.nimble','.cfg'}
paths = {root/'scripts/build-mosdepth.sh'}
paths.update(path for path in (root/'tools/mosdepth').rglob('*') if path.is_file() and path.suffix in suffixes)
inputs = {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(paths)}
output.write_text(json.dumps(inputs, indent=2, sort_keys=True)+'\n')
PY
    fi
    extract "$(lock_value archive)" "$work/source"
    for dep in hts-nim docopt regex unicodedb htslib zlib; do
        extract "$(lock_value dependencies.$dep.archive)" "$work/$dep"
    done
    rm -rf -- "$work/nimcache" "$work/width-cache" "$work/zlib-prefix"
    if [[ "$variant" != native ]]; then
        patch --batch --forward -d "$work/source" -p1 < "$ROOT/tools/mosdepth/browser.patch"
        patch --batch --forward -d "$work/hts-nim" -p1 < "$ROOT/tools/mosdepth/hts-nim.patch"
    fi
    (
        cd "$work/zlib"
        CC="$cc" AR="$ar" RANLIB="$ranlib" CFLAGS="$cflags" ./configure --static --prefix="$work/zlib-prefix"
        make -j"$JOBS" libz.a
        make install
    )
    (
        cd "$work/htslib"
        CC="$cc" AR="$ar" RANLIB="$ranlib" CFLAGS="$cflags" \
            CPPFLAGS="-I$work/zlib-prefix/include" LDFLAGS="$cflags -L$work/zlib-prefix/lib" \
            "${configure_prefix[@]}" ./configure "${configure_options[@]}" \
            --disable-libcurl --disable-bz2 --disable-lzma --without-libdeflate --disable-plugins
        make -j"$JOBS" libhts.a
    )
    local -a common=(c --skipUserCfg --skipParentCfg -d:release --mm:"$memory_manager" --threads:off
        "${nim_options[@]}" --path:"$work/hts-nim/src" --path:"$work/docopt/src"
        --path:"$work/regex/src" --path:"$work/unicodedb/src" --path:"$work/source"
        --dynlibOverride:hts --passC:"$cflags"
        --passL:"$work/htslib/libhts.a $work/zlib-prefix/lib/libz.a $link_flags")
    "$NIM" "${common[@]}" --nimcache:"$work/nimcache" -o:"$work/mosdepth$extension" "$work/source/mosdepth.nim"
    "$NIM" "${common[@]}" --nimcache:"$work/width-cache" -o:"$work/width-check$extension" "$ROOT/tools/mosdepth/width_check.nim"
    if [[ "$variant" == wasm ]]; then
        node --input-type=module - "$ROOT" "$work" <<'JS'
import {pathToFileURL} from 'node:url';
const [root, work] = process.argv.slice(2);
const {ToolRunner} = await import(pathToFileURL(`${root}/runtime/index.mjs`));
const runner = new ToolRunner({moduleUrl:pathToFileURL(`${work}/width-check.mjs`)});
const result = await runner.run([], {timeoutMs:30000});
if (result.exitCode !== 0 || new TextDecoder().decode(result.stdout) !== 'MOSDEPTH_WIDTH_OK 4294966000 8589934000\n') {
  throw new Error('mosdepth 64-bit arithmetic check failed');
}
console.log('mosdepth WebAssembly 64-bit arithmetic check passed');
JS
        cp "$work/mosdepth.mjs" "$work/mosdepth.wasm" "$ROOT/dist/mosdepth/"
        python3 - "$ROOT" "$work/build-inputs.json" <<'PY'
from pathlib import Path
import hashlib, json, shutil, sys
root, record = Path(sys.argv[1]), Path(sys.argv[2])
inputs = json.loads(record.read_text())
for name, checksum in inputs.items():
    if hashlib.sha256((root/name).read_bytes()).hexdigest() != checksum:
        raise SystemExit(f'Build input changed during compilation: {name}')
dist = root/'dist/mosdepth'; tool = root/'tools/mosdepth'
lock = json.loads((tool/'sources.lock.json').read_text())
for name in [*lock['requiredNotices'], 'sources.lock.json', 'browser.patch', 'hts-nim.patch']:
    shutil.copyfile(tool/name, dist/name)
for source in [lock, *lock['dependencies'].values()]:
    shutil.copyfile(root/'build/downloads'/source['archive'], dist/source['archive'])
assets = {path.relative_to(dist).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
          for path in sorted(dist.rglob('*')) if path.is_file() and path.suffix in {'.mjs','.js','.wasm'}}
(dist/'build-manifest.json').write_text(json.dumps({'schema':1,'tool':'mosdepth','inputs':inputs,'assets':assets},indent=2,sort_keys=True)+'\n')
PY
    else
        "$work/width-check"
        mkdir -p "$ROOT/build/$variant"
        cp "$work/mosdepth" "$ROOT/build/$variant/mosdepth"
    fi
}
if [[ "$MODE" == all ]]; then
    build_variant native
    build_variant patched-native
    build_variant wasm
else
    build_variant "$MODE"
fi
