#!/usr/bin/env bash
# Compile untouched upstream, patched native, and browser Salmon independently.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
MODE=${1:-all}
case "$MODE" in all|native|patched-native|wasm) ;; *) echo 'Usage: scripts/build-salmon.sh [all|native|patched-native|wasm]' >&2; exit 2;; esac
BUILD_ROOT=${SALMON_BUILD_ROOT:-"$ROOT/build/salmon"}
CARGO=${SALMON_CARGO:-cargo}
JOBS=${JOBS:-2}
export RUSTUP_TOOLCHAIN=nightly-2026-09-25
export CARGO_PROFILE_RELEASE_LTO=false
export CARGO_HOME=${SALMON_CARGO_HOME:-"$BUILD_ROOT/cargo-home"}
mkdir -p "$BUILD_ROOT" "$CARGO_HOME" "$ROOT/dist/salmon"
[[ $("$CARGO" --version) == cargo\ 1.100.0-nightly* ]] || { echo 'Expected Cargo from nightly-2026-09-25' >&2; exit 1; }
[[ $("${RUSTC:-rustc}" --version) == 'rustc 1.100.0-nightly (f7575a9da 2026-09-24)' ]] || { echo 'Expected Rust nightly-2026-09-25 (f7575a9da)' >&2; exit 1; }


# The rebuilt standard library must use the dependency lock recorded alongside
# the application lock, not a different rust-src component on the same PATH.
SALMON_RUST_SYSROOT=$("${RUSTC:-rustc}" --print sysroot)
python3 - "$ROOT" "$SALMON_RUST_SYSROOT" <<'PYLOCK'
from pathlib import Path
import hashlib, json, sys
lock = json.loads((Path(sys.argv[1]) / 'tools/salmon/sources.lock.json').read_text())
source_lock = Path(sys.argv[2]) / 'lib/rustlib/src/rust/library/Cargo.lock'
if not source_lock.is_file():
    raise SystemExit('Missing pinned rust-src component; install it for nightly-2026-09-25.')
actual = hashlib.sha256(source_lock.read_bytes()).hexdigest()
if actual != lock['rust']['standardLibraryCargoLockSha256']:
    raise SystemExit('Rust standard-library Cargo.lock does not match sources.lock.json.')
PYLOCK

build_variant() {
    local variant=$1 source
    source=$(python3 "$ROOT/tools/salmon/prepare.py" --mode "$variant" --build-root "$BUILD_ROOT")
    export CARGO_TARGET_DIR="$BUILD_ROOT/target-$variant"
    if [[ "$variant" != native ]]; then
        mkdir -p "$source/crates/salmon-cli/src/bin"
        cp "$ROOT/tools/salmon/port-check.rs" "$source/crates/salmon-cli/src/bin/port-check.rs"
    fi
    local -a options=(build --frozen --release --bin salmon -j "$JOBS")
    if [[ "$variant" != native ]]; then options+=(--features sysalloc); fi
    if [[ "$variant" == wasm ]]; then
        if ! command -v emcc >/dev/null; then
            if [[ -n "${EMSDK:-}" && -f "$EMSDK/emsdk_env.sh" ]]; then
                set +u
                source "$EMSDK/emsdk_env.sh"
                set -u
            else
                echo 'Activate Emscripten 3.1.64 or set EMSDK to its directory.' >&2
                exit 1
            fi
        fi
        [[ $(emcc --version | head -1) == *3.1.64* ]] || { echo 'Expected Emscripten 3.1.64' >&2; exit 1; }
        export CARGO_TARGET_WASM32_UNKNOWN_EMSCRIPTEN_LINKER="$ROOT/tools/salmon/linker.sh"
        export CARGO_TARGET_WASM32_UNKNOWN_EMSCRIPTEN_RUSTFLAGS='-C target-feature=+atomics,+bulk-memory,+mutable-globals,+simd128'
        export CFLAGS_wasm32_unknown_emscripten=-pthread
        export CXXFLAGS_wasm32_unknown_emscripten=-pthread
        export SALMON_LINK_OUTPUT="$ROOT/dist/salmon/salmon.mjs"
        options+=(--target wasm32-unknown-emscripten -Zbuild-std=std,panic_abort,panic_unwind,unwind)
        # Link each requested distribution, even if Cargo's compiled dependencies
        # are reusable, so linker flag changes cannot leave an old module behind.
        touch "$source/crates/salmon-cli/src/main.rs"
        python3 "$ROOT/tools/salmon/finish.py" --record-inputs "$BUILD_ROOT/build-inputs.json"
    fi
    (
        cd "$source"
        "$CARGO" "${options[@]}"
    )
    if [[ "$variant" != wasm ]]; then
        mkdir -p "$ROOT/build/$variant"
        cp "$CARGO_TARGET_DIR/release/salmon" "$ROOT/build/$variant/salmon"
        if [[ "$variant" == patched-native ]]; then
            (cd "$source"; "$CARGO" build --frozen --release --bin port-check --features sysalloc -j "$JOBS")
            "$CARGO_TARGET_DIR/release/port-check"
        fi
    else
        export SALMON_LINK_OUTPUT="$BUILD_ROOT/port-check/port-check.mjs"
        (
            cd "$source"
            "$CARGO" build --frozen --release --bin port-check --features sysalloc -j "$JOBS" \
                --target wasm32-unknown-emscripten -Zbuild-std=std,panic_abort,panic_unwind,unwind
        )
        node --input-type=module - "$ROOT" "$SALMON_LINK_OUTPUT" <<'JS'
import { pathToFileURL } from 'node:url';
const { ToolRunner } = await import(pathToFileURL(process.argv[2] + '/runtime/index.mjs'));
const result = await new ToolRunner({ moduleUrl: pathToFileURL(process.argv[3]), waitForExit: true }).run([], { timeoutMs: 120000 });
const stdout = new TextDecoder().decode(result.stdout);
if (result.exitCode !== 0 || !stdout.includes('SALMON_PORT_CHECK_OK pointer_bits=32')) {
    throw new Error(`Salmon portability checks failed (${result.exitCode}): ${stdout}\n${new TextDecoder().decode(result.stderr)}`);
}
process.stdout.write(stdout);
JS
        python3 "$ROOT/tools/salmon/finish.py" --verify-inputs "$BUILD_ROOT/build-inputs.json"
    fi
}

if [[ "$MODE" == all ]]; then
    build_variant native
    build_variant patched-native
    build_variant wasm
else
    build_variant "$MODE"
fi
