# seqtk in a browser with container2wasm

This experimental example runs the original **Linux x86_64 seqtk 1.5** executable
inside a container, Linux guest, and QEMU emulator compiled to WebAssembly. It is
separate from the existing direct seqtk WebAssembly port. There is no fallback
that silently substitutes the direct port or a JavaScript implementation.

The page accepts a local FASTA/FASTQ file (including gzip), offers conversion,
reverse complement, fixed trimming and quality-statistics presets, and also
accepts an editable argument array. It displays logs, supports cancellation and
a timeout, and lets users download standard output and standard error.

## What users need

- A desktop browser with WebAssembly threads and cross-origin isolation.
- Enough available memory: upstream container2wasm 0.8.4's x86_64 QEMU build
  allocates a **3000 MiB WebAssembly memory**. The guest has 256 MiB RAM. These are
  different quantities and neither is a measured peak browser working set.
- A small input: this prototype limits total input to 20 MiB and combined output
  to 64 MiB. It is not a whole-genome processing application.

Users of a hosted copy do **not** install Docker, WSL, Node.js, Python or seqtk.
Files are read into browser memory. The guest has no network bridge. The static
server receives asset requests, not uploaded sequence data or computation jobs.
Inputs and results disappear when the tab is reloaded. Download results to keep
them. The prototype does not yet stream large files or persist them in OPFS.

## Build on a developer machine

Use Linux x86_64 with Docker Engine, Docker Buildx, curl, tar and Python 3. The
converter build compiles a Linux kernel, QEMU and their dependencies; allow
substantial disk space and time for the first build. Docker caches subsequent
build layers. This setup is for the developer/build runner only.

```sh
bash scripts/build-seqtk-container.sh
node scripts/seqtk-container-server.mjs
```

Open <http://127.0.0.1:8000/examples/seqtk-container/>. The server only serves
static files and includes the required isolation headers. `python -m http.server`
alone does not add those headers and therefore is not sufficient here.

The build can be split into `image` and `wasm` modes. The latter expects the image
and the verified archives from the image build to be present. The generated
runtime is written to `dist/seqtk-container/runtime/`; build metadata and asset
hashes are in `dist/seqtk-container/manifest.json`.

The build reuses `tools/seqtk/sources.lock.json` for the unchanged seqtk revision
and zlib 1.3.1. The converter release and archive checksum are pinned in
`tools/seqtk-container/sources.lock.json`. The Alpine base uses a version tag;
the build records the resolved image and asset hashes. This is a repeatable
build recipe, not a claim of byte-for-byte reproducibility for every upstream
OS package. The source archives, lockfiles and native software licences are
retained with the build/container.

## GitHub Actions and validation

The **Build and validate seqtk container prototype** workflow builds:

1. The unchanged native seqtk baseline and existing direct WASM port.
2. The native Docker image and container2wasm browser assets.
3. Actual browser comparisons on the repository's human and yeast fixtures.

CI caches the compiled runtimes and native image using the source locks, build
scripts and container files as its key. Interface/runtime changes reuse those
builds and rerun browser validation; a cache hit does not imply a test pass.

The validator compares complete stdout bytes and process exit status for
conversion, reverse complement, trimming and quality statistics, including gzip,
missing-input, usage and repeated-run cases. It also checks cancellation,
timeouts and recovery. These are execution-equivalence tests, not a biological
accuracy assessment or a large-dataset benchmark.

To run the same checks locally after building:

```sh
npm ci
npx playwright install --with-deps chromium
bash scripts/setup-emsdk.sh
source toolchain/emsdk/emsdk_env.sh
bash scripts/build-seqtk.sh all
node --test tests/seqtk-container.test.mjs
node scripts/validate-seqtk-container.mjs
python3 scripts/package-seqtk-container.py
```

Reports are written to `reports/seqtk-container/`. A successful run produces a
`seqtk-container-prototype` Actions artifact containing the static site, runtime,
build recipe and validation report. Packaging requires a passing report; it does
not publish a website. Results should be interpreted using the browser version,
fixture hashes and build hashes in the report.

## Static hosting

Serve the packaged directory over HTTPS and set these response headers on the
page **and its assets**:

```text
Cross-Origin-Opener-Policy: same-origin
Cross-Origin-Embedder-Policy: require-corp
Cross-Origin-Resource-Policy: same-origin
```

Serve `.js` and `.mjs` as JavaScript, `.wasm` as `application/wasm`, and `.data`
as `application/octet-stream`. Keep the packaged directory layout, since runtime
URLs are relative. Ordinary GitHub Pages does not provide configurable response
headers; use hosting that supports these headers for this prototype.

The supplied preview server binds only to loopback. An example nginx location
for a hosted deployment is:

```nginx
location / {
    root /srv/seqtk-container;
    add_header Cross-Origin-Opener-Policy same-origin always;
    add_header Cross-Origin-Embedder-Policy require-corp always;
    add_header Cross-Origin-Resource-Policy same-origin always;
    try_files $uri $uri/ =404;
}
```

Configure the MIME types above in nginx's `types` configuration as well. The page
reports a clear error if the origin is not isolated or runtime assets are absent.

## JavaScript API and execution model

```js
import { SeqtkContainerRunner } from './runtime/container2wasm-seqtk.mjs';

const runner = new SeqtkContainerRunner();
const controller = new AbortController();
const result = await runner.run(['seq', '-A', '/work/reads.fq'], {
  files: [{ path: 'reads.fq', data: new Uint8Array(await file.arrayBuffer()) }],
  timeoutMs: 180000,
  signal: controller.signal,
  onLog: ({ stream, text }) => console.log(stream, text),
  onProgress: ({ phase, message }) => console.log(phase, message),
});
// result.exitCode is seqtk's exit code, not QEMU's shutdown status.
// result.stdout and result.stderr are Uint8Array values.
// controller.abort() or runner.cancel() stops an active job.
```

Each run has a fresh outer Worker, emulator and in-memory filesystem. Inputs
are copied into `/work`, which is mounted into the guest through container2wasm's
filesystem bridge. Arguments are passed as literal strings through `argv.txt`;
shell substitution, pipes and redirection are not interpreted. NUL and line
breaks in arguments are rejected. The runtime prevents uploaded filenames from
overwriting its control files.

The container entrypoint executes `/usr/local/bin/seqtk` and closes its stdout
and stderr files before atomically publishing `status.json`. The browser polls
these files instead of parsing terminal escape sequences or relying on QEMU's
exit status. A nonzero seqtk status is returned along with its output; a worker,
asset-loading, timeout or cancellation failure rejects the promise.

Measurements separate runtime initialization, guest startup and observed
execution. File polling is every 100 ms, so very short commands have coarse
timings. Every invocation boots a fresh VM; HTTP and WebAssembly compilation
caches may still affect later runs. No cache hit rate or peak process memory is
inferred from elapsed time or WebAssembly memory size.

## Sources

- <https://github.com/container2wasm/container2wasm/tree/v0.8.4>
- <https://github.com/container2wasm/container2wasm/tree/v0.8.4/examples/emscripten-simple>
- <https://github.com/lh3/seqtk>

container2wasm's generated runtime also contains Linux, QEMU, BusyBox and other
third-party software. Its source tree and Dockerfile identify the corresponding
versions and licences. This experimental artifact is not a general release
package for redistribution of the full emulator stack.
