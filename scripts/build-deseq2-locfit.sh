#!/usr/bin/env bash
# Build the genuine locfit dependency for webR 0.6.0.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
IMAGE='ghcr.io/r-wasm/webr@sha256:3fbf3dc3537c5b31bca94dd797dc25364693b18d71b1d0975f318fbfd6269d86'
SOURCE="$ROOT/build/downloads/locfit_1.5-9.12.tar.gz"
OUTPUT="$ROOT/build/deseq2/locfit"
mkdir -p "$(dirname "$SOURCE")" "$OUTPUT"
if [[ ! -f "$SOURCE" ]]; then
    curl --fail --location --retry 3 --max-time 300 \
        'https://cran.r-project.org/src/contrib/locfit_1.5-9.12.tar.gz' -o "$SOURCE.tmp"
    mv "$SOURCE.tmp" "$SOURCE"
fi
printf '%s  %s\n' 'fa31b9d407f3557a6fcb7105cbd7b16a32a6fb868a7c60bc38a3e33cb7a39343' "$SOURCE" | sha256sum --check --status
if [[ ${1:-} == --container ]]; then
    Rscript "$ROOT/tools/deseq2/locfit-build.R" "$SOURCE" "$OUTPUT"
    if [[ -x /opt/flang/host/bin/flang ]]; then
        /opt/flang/host/bin/flang --version | tee -a "$OUTPUT/locfit-build-environment.txt"
    fi
    for source_tree in /opt/flang /opt/flang/llvm-project /opt/flang/llvm /opt/webr /opt/emsdk; do
        if [[ -d "$source_tree/.git" || -f "$source_tree/.git" ]]; then
            revision=$(git -C "$source_tree" rev-parse HEAD)
            printf '%s %s\n' "$source_tree" "$revision" | tee -a "$OUTPUT/locfit-build-environment.txt"
        fi
    done
    python3 - "$OUTPUT/locfit_1.5-9.12.tgz" <<'PY'
import hashlib, pathlib, sys, tarfile
p = pathlib.Path(sys.argv[1])
with tarfile.open(p) as archive:
    library = archive.extractfile('locfit/libs/locfit.so').read()
    if library[:8] != b'\0asm\x01\0\0\0':
        raise SystemExit('locfit shared library is not a WebAssembly module')
print(hashlib.sha256(p.read_bytes()).hexdigest(), p.name)
PY
else
    docker run --rm -v "$ROOT:/project" -w /project "$IMAGE" \
        bash scripts/build-deseq2-locfit.sh --container
fi
