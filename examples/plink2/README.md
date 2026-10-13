# PLINK 2 browser example

Run original PLINK 2 commands on local genotype files, inspect text outputs and
download complete results. Analysis stays in your browser. Example data are
served from this package; no external service receives your files.

## Start

Build PLINK 2 and prepare the fixtures from the repository root, or extract the
standalone PLINK 2 package, which already contains these files. Then run:

```sh
python3 examples/plink2/serve.py
```

Open `http://localhost:8000/examples/plink2/`. The included server supplies the
headers needed by threaded WebAssembly. Use `--port 8080` to select another port.
Opening `index.html` directly as a local file does not work.

Select **Load example data**, then run the conversion and quality-control commands
in order. The real input is a 1000 Genomes chromosome 21 subset with 2,504 samples
and 600 variants. The linear-regression preset uses a separate, explicitly
synthetic control. Its phenotypes and covariates are not observations from the
1000 Genomes participants. See the [data documentation](../../datasets/plink2/README.md).

Uploads accept multiple files or a folder. Folder paths are preserved. PLINK 2
datasets often consist of several files: keep `.pgen/.pvar/.psam` or
`.bed/.bim/.fam` together and use the common prefix in `--pfile` or `--bfile`.
Choose a preset and edit its argument array to use your filenames. Each array
entry is one literal argument; shell pipes, expansion and redirection do not run.

Successful output files remain available to subsequent commands. Files requested
explicitly as outputs are regenerated, preventing an older output from being
mistaken for a successful new result. A failed or cancelled command leaves the
previous successful workspace unchanged. Reloading clears the workspace.

## Hosting and limits

Serve this directory and the package's `runtime`, `dist/plink2` and
`datasets/plink2` paths together. Use HTTPS or localhost and send:

```text
Cross-Origin-Opener-Policy: same-origin
Cross-Origin-Embedder-Policy: require-corp
```

The page must report `crossOriginIsolated === true`; WebAssembly threads and SIMD
must be available. Keep every generated JavaScript worker asset with the main
module. Ordinary GitHub Pages hosting does not supply these headers. This example
does not install a service worker or alter other pages on the host.

Presets use `--threads 1 --memory 640 require`. The runner accepts an explicit arena between 640 and 1024 MiB and one or two
compute threads; input and compression helpers still use real worker threads. `--script` and
`--rerun` are unavailable; supply the argument array directly.
The 640 MiB value is PLINK 2's minimum analysis arena, not total browser memory.
Input copies, output files, WebAssembly stacks and other allocations consume
additional memory. The WebAssembly heap cannot exceed 2 GiB, and a browser may
run out of memory earlier. This example keeps complete inputs and outputs in
memory; it does not stream large files from disk.

This build omits LAPACK. PCA and other LAPACK-dependent commands are unavailable;
their upstream errors are retained. See [build details](../../tools/plink2/README.md)
for the exact supported scope. Browser validation covers the listed fixture
workflows; it is not a claim of native biobank-scale capacity.

## Browser checks

With the repository or standalone package's development dependencies installed:

```sh
npm ci
npx playwright install chromium firefox webkit
BROWSER=chromium node scripts/test-plink2-demo.mjs
```

Repeat with `BROWSER=firefox` and `BROWSER=webkit`. The test runs actual local
WebAssembly, conversion/QC and synthetic regression, checks file downloads and
command chaining, and verifies cancellation, errors and hosting diagnostics.
Set `PLINK2_DEMO_REPORT` to choose the report base directory; results are written
under its `browser-demo` subdirectory.
