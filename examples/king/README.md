# KING 2.3.2 local browser demo

Build KING locally using the [build instructions](../../tools/king/README.md).
The upstream source headers permit personal compilation and modification
while restricting redistribution. This demo is for a local personal-use
build; it is not a published KING binary distribution.

From the repository root:

```sh
python3 -m http.server 8000 --bind 127.0.0.1
```

Open <http://127.0.0.1:8000/examples/king/>. Ordinary static HTTP is sufficient;
cross-origin isolation is not needed. Loading the HTML directly through
`file://` does not support the required modules and workers.

Select **Load synthetic example**, choose an analysis, then **Run analysis**.
The example contains 105 simulated samples with a trio, siblings, an exact
duplicate and unrelated samples. Its 32,809 variants and missing genotypes
are generated deterministically. It contains no human-derived genotype
data. See the [fixture description](../../datasets/king/README.md) for the
independent truth and intended validation scope.

The close-relative preset includes `--noscreen 1`, which skips KING's initial
subset screening. The upstream default screening can miss cross-family
relationships in cohorts smaller than 100 samples. The option evaluates
candidate pairs directly; it can take longer on large cohorts. The larger
example also exercises the original screening path in scientific validation.

To use your own data, upload matching PLINK BED/BIM/FAM files. A folder
upload preserves relative paths. When exactly one BED file is present, the
BED filename field is filled automatically. Changing the BED filename,
output folder or analysis refreshes the preset arguments. Edit the JSON
array for custom commands; each entry is passed directly to KING.

The default output prefix is `results/king`. KING appends different suffixes
for different analyses, so the demo collects all files in the selected
output folder. For custom commands, keep `--prefix` inside that folder.
The folder must be separate from all input paths. A private empty marker
creates the folder and is excluded from analysis outputs.

Each run starts with fresh program state. Starting a new command, changing
input files or clearing the workspace removes previous result links. Failed
and cancelled commands cannot display stale successful outputs. Standard
output and error remain downloadable after a completed command; nonzero
exit status prevents its analysis files being presented as successful results.
Text previews are limited to 100,000 bytes. Downloads contain the complete,
unaltered bytes, including compressed outputs.

**Cancel** terminates the worker. The time limit includes loading the module
and running the analysis. You can run another command immediately after
cancellation, timeout or a failed analysis. Reloading clears all files held
by the page. Input genotypes are not uploaded to a remote service.

This serial build runs one command per worker. KING's external R and
`ps2pdf` plotting commands cannot run in a browser. Input copies, outputs
and WebAssembly memory all consume browser resources; start with small
cohorts. Passing the synthetic fixtures does not establish biobank-scale
capacity or clinical validity.

The UI tests exercise the real locally built WASM module on ordinary HTTP:

```sh
npm ci
npx playwright install chromium firefox webkit
node scripts/test-king-demo.mjs --engine chromium
node scripts/test-king-demo.mjs --engine firefox
node scripts/test-king-demo.mjs --engine webkit
```

Set `PLAYWRIGHT_BROWSERS_PATH` when using a custom browser installation
directory. Reports are written to `reports/king/<browser>/demo/`. The
scientific native/WASM comparison is a separate check. See the
[JavaScript API](../../runtime/KING.md) for using KING outside this demo.
