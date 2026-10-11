#!/usr/bin/env bash
# Rust calls this linker only for the WebAssembly target.
set -euo pipefail
: "${SALMON_LINK_OUTPUT:?Set SALMON_LINK_OUTPUT to the distribution module path}"
SALMON_LINKER_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
arguments=("$@")
requested=''
for ((i=0; i<${#arguments[@]}; i++)); do
    if [[ ${arguments[i]} == -o ]]; then
        requested=${arguments[i+1]}
        arguments[i+1]=$SALMON_LINK_OUTPUT
        break
    fi
done
[[ -n "$requested" ]] || { echo 'Rust linker call has no output filename' >&2; exit 2; }
mkdir -p "$(dirname "$SALMON_LINK_OUTPUT")"
# Keep Rust release optimization; skip the old SDK's optional Binaryen
# optimization pass, which cannot parse Rust LLVM 23 feature metadata.
emcc "${arguments[@]}" -O0 -pthread \
    --js-library "$SALMON_LINKER_DIR/mailbox.js" \
    -sPROXY_TO_PTHREAD=1 -sMODULARIZE=1 -sEXPORT_ES6=1 -sEXPORT_NAME=createSalmon -sINVOKE_RUN=0 \
    -sEXPORTED_RUNTIME_METHODS=FS,callMain,PThread \
    -sINCOMING_MODULE_JS_API=preRun,noInitialRun,stdin,stdout,stderr,onExit,quit,locateFile,wasmBinary,print,printErr,onAbort,thisProgram,mainScriptUrlOrBlob,instantiateWasm \
    -sENVIRONMENT=web,worker,node -sALLOW_MEMORY_GROWTH=1 \
    -sINITIAL_MEMORY=268435456 -sMAXIMUM_MEMORY=2147483648 -sSTACK_SIZE=8388608 \
    -sDEFAULT_PTHREAD_STACK_SIZE=8388608 -sEXIT_RUNTIME=1 -sFORCE_FILESYSTEM=1 \
    -sPTHREAD_POOL_SIZE=12 -sPTHREAD_POOL_SIZE_STRICT=2 -sABORTING_MALLOC=1 -sASSERTIONS=1
# Cargo expects the filename it supplied. Keep its build bookkeeping valid;
# the distributable module and its relative worker/WASM URLs use stable names.
if [[ "$requested" != "$SALMON_LINK_OUTPUT" ]]; then cp "$SALMON_LINK_OUTPUT" "$requested"; fi
