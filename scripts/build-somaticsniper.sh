#!/usr/bin/env bash
# Compile the same pinned SomaticSniper source for native and WebAssembly hosts.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
MODE=${1:-all}
case "$MODE" in all|native|patched-native|wasm) ;; *) echo 'Usage: scripts/build-somaticsniper.sh [all|native|patched-native|wasm]' >&2; exit 2;; esac
if [[ "$MODE" == wasm || "$MODE" == all ]]; then
    # Prefer the host Node executable to any SDK-bundled copy.
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
exec python3 "$ROOT/tools/somaticsniper/build.py" "$MODE"
