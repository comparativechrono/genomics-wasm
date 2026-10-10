#!/usr/bin/env bash
set -euo pipefail
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
sdk_dir=${GENOMICS_EMSDK_DIR:-"$root/toolchain/emsdk"}
version=3.1.64
if [[ ! -d "$sdk_dir/.git" ]]; then
  git clone --depth 1 --branch "$version" https://github.com/emscripten-core/emsdk.git "$sdk_dir"
fi
"$sdk_dir/emsdk" install "$version"
"$sdk_dir/emsdk" activate "$version"
printf 'Activate this toolchain with: source %q\n' "$sdk_dir/emsdk_env.sh"
