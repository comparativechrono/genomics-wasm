#!/bin/sh
# File-based bridge for a single seqtk invocation. Browser input is mounted at
# /work by container2wasm; all files remain in its in-memory Emscripten filesystem.
# Never eval arguments or send data through the emulated terminal.
set -u

: > /work/stdout
: > /work/stderr
: > /work/started

set --
while IFS= read -r arg || [ -n "$arg" ]; do
    set -- "$@" "$arg"
done < /work/argv.txt

/usr/local/bin/seqtk "$@" </dev/null >/work/stdout 2>/work/stderr
status=$?
# Closing the output files precedes publishing the completion marker. The host
# reads this status rather than the QEMU shutdown status (which can be zero even
# when a process inside the container failed).
printf '{"exitCode":%s}\n' "$status" > /work/status.json.tmp
mv /work/status.json.tmp /work/status.json
exit "$status"
