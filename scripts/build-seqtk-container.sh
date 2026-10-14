#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
MODE=${1:-all}
case "$MODE" in all|image|wasm) ;; *) echo 'Usage: bash scripts/build-seqtk-container.sh [all|image|wasm]' >&2; exit 2;; esac
for tool in docker curl python3 tar; do
    command -v "$tool" >/dev/null || { echo "Build dependency missing: $tool (end users do not need it)." >&2; exit 1; }
done
docker info >/dev/null
docker buildx version >/dev/null
WORK="$ROOT/build/seqtk-container"
OUT="$ROOT/dist/seqtk-container"
mkdir -p "$WORK/context" "$WORK/bin" "$OUT/runtime" "$OUT/sources"
value() {
    python3 - "$1" "$2" <<'PY'
import json, sys
value = json.load(open(sys.argv[1]))
for part in sys.argv[2].split('.'):
    value = value[part]
print(value)
PY
}
download() {
    local url=$1 dest=$2 expected=$3
    if [[ ! -f "$dest" ]]; then
        curl --fail --location --retry 3 --max-time 600 "$url" -o "$dest.tmp"
        mv "$dest.tmp" "$dest"
    fi
    python3 - "$dest" "$expected" <<'PY'
import hashlib, pathlib, sys
p = pathlib.Path(sys.argv[1])
actual = hashlib.sha256(p.read_bytes()).hexdigest()
if actual != sys.argv[2]:
    raise SystemExit(f'Checksum mismatch for {p}: expected {sys.argv[2]}, got {actual}')
PY
}
LOCK="$ROOT/tools/seqtk-container/sources.lock.json"
SEQTK_LOCK="$ROOT/tools/seqtk/sources.lock.json"
IMAGE=$(value "$LOCK" imageTag)
if [[ "$MODE" != wasm ]]; then
    download "$(value "$SEQTK_LOCK" url)" "$WORK/context/seqtk.tar.gz" "$(value "$SEQTK_LOCK" sha256)"
    download "$(value "$SEQTK_LOCK" dependencies.zlib.url)" "$WORK/context/zlib.tar.gz" "$(value "$SEQTK_LOCK" dependencies.zlib.sha256)"
    cp "$ROOT/tools/seqtk-container/runner.sh" "$WORK/context/runner.sh"
    docker build --platform linux/amd64 --build-arg "BASE_IMAGE=$(value "$LOCK" baseImage)" \
        -f "$ROOT/tools/seqtk-container/Dockerfile" -t "$IMAGE" "$WORK/context"
fi
if [[ "$MODE" != image ]]; then
    if [[ $(uname -s) != Linux || $(uname -m) != x86_64 ]]; then
        echo 'This pinned converter build requires Linux x86_64; use the GitHub Actions workflow.' >&2; exit 1
    fi
    download "$(value "$LOCK" container2wasm.url)" "$WORK/converter.tar.gz" "$(value "$LOCK" container2wasm.sha256)"
    tar -xzf "$WORK/converter.tar.gz" -C "$WORK/bin"
    C2W="$WORK/bin/c2w"
    test -s "$C2W"
    chmod u+x "$C2W"
    "$C2W" --to-js --target-arch amd64 \
        --build-arg "VM_MEMORY_SIZE_MB=$(value "$LOCK" guestMemoryMiB)" \
        --build-arg VM_CORE_NUMS=1 \
        "$IMAGE" "$OUT/runtime/"
    for asset in out.js load.js arg-module.js qemu-system-x86_64.wasm qemu-system-x86_64.data; do
        test -s "$OUT/runtime/$asset" || { echo "Missing converter output: $asset" >&2; exit 1; }
    done
    cp "$WORK/context/seqtk.tar.gz" "$WORK/context/zlib.tar.gz" "$OUT/sources/"
    cp "$LOCK" "$OUT/sources/container.lock.json"
    cp "$SEQTK_LOCK" "$OUT/sources/seqtk.lock.json"
    cp "$ROOT/tools/seqtk-container/Dockerfile" "$ROOT/tools/seqtk-container/runner.sh" "$OUT/sources/"
    docker image inspect "$IMAGE" > "$OUT/sources/image-inspect.json"
    python3 - "$ROOT" <<'PY'
import hashlib, json, pathlib, sys, datetime
root = pathlib.Path(sys.argv[1]); out = root / 'dist/seqtk-container'
lock = json.loads((root / 'tools/seqtk-container/sources.lock.json').read_text())
manifest = {'builtAt': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'configuration': lock,
            'assets': {p.name: {'bytes': p.stat().st_size, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
                       for p in sorted((out / 'runtime').iterdir()) if p.is_file()}}
(out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
PY
fi
echo "seqtk container build completed ($MODE). Image: $IMAGE"
