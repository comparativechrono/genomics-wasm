#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
export EM_NODE_JS=${EM_NODE_JS:-$(command -v node)}
sdk=${EMSDK:-"$root/toolchain/emsdk"}
if ! command -v emcc >/dev/null && [[ -f "$sdk/emsdk_env.sh" ]]; then
  set +u
  source "$sdk/emsdk_env.sh"
  set -u
fi
exec python3 "$root/tools/hmmer/build.py" "${1:-all}"
