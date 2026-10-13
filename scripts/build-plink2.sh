#!/usr/bin/env bash
# Build pinned original-source PLINK2 with its explicit NOLAPACK profile.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
MODE=${1:-all}
case "$MODE" in all|native|patched-native|wasm) ;; *) echo 'Usage: scripts/build-plink2.sh [all|native|patched-native|wasm]' >&2; exit 2;; esac
if [[ "$MODE" == wasm || "$MODE" == all ]]; then
    export EM_NODE_JS=${EM_NODE_JS:-$(command -v node)}
    if ! command -v emcc >/dev/null; then
        SDK=${EMSDK:-"$ROOT/toolchain/emsdk"}
        if [[ ! -f "$SDK/emsdk_env.sh" ]]; then
            echo 'Activate Emscripten 3.1.64 or set EMSDK to its directory.' >&2
            exit 1
        fi
        set +u; source "$SDK/emsdk_env.sh"; set -u
    fi
fi
exec python3 "$ROOT/tools/plink2/build.py" "$MODE"
