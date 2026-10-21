# ExpansionHunter and REViewer browser workflow

This example runs ExpansionHunter 5.0.0, SAMtools 1.21 and REViewer 0.2.7
entirely in the browser. ExpansionHunter calls the repeat; SAMtools sorts and
indexes the generated BAMlet; REViewer produces the alignment plot and its
metrics and phasing tables.

From a prepared checkout or an extracted release ZIP, serve its top-level
directory and open the demo:

```sh
python3 -m http.server 8000
```

Open <http://localhost:8000/examples/repeats/>. An extracted release already
includes the compiled programs. For a source checkout, follow the
[ExpansionHunter build](../../tools/expansionhunter/README.md),
[REViewer build](../../tools/reviewer/README.md) and
[HTS build](../../tools/hts/README.md) instructions first. Ordinary static
HTTP is sufficient; cross-origin isolation is not required. Opening the HTML
directly through `file://` does not provide module and worker loading.

Load the example data and run presets 1 through 4 in order. Each step retains
the files required by the next. The controls have CAG repeat alleles
12/18 and 12/75. The longer allele exceeds the read length, so ExpansionHunter
estimates its size and confidence interval. These are explicitly synthetic
reads, described in the [dataset provenance](../../datasets/repeats/README.md).
They contain no human-derived reads and are not clinical findings.

Choose `review.NORMAL_CAG.svg` or `review.EXPANDED_CAG.svg` in the results
selector to view the read alignment plot. The SVG is displayed as an image rather than executable page
markup. Its default 100% scale preserves the original labels; scroll within
the plot or choose **Fit width** or a larger scale. Downloads preserve the
original SVG and table bytes. Text previews are limited to 100,000 bytes; downloads are complete.

Upload your BAM or CRAM, reference FASTA, matching indexes and repeat catalog
to use your own data. Folder uploads preserve relative paths. Edit the JSON
argument array and the output filenames for each command. Use the same
reference, catalog and region extension length in ExpansionHunter and
REViewer. REViewer expects the sorted, indexed ExpansionHunter BAMlet and
the corresponding VCF. The default region extension length is 1,000 bases.
These presets use repeat-only loci. REViewer 0.2.7 has upstream limitations
for catalogs combining repeats with small variants; this demo does not
claim support for those compound loci.

Every command runs in a fresh worker. The page retains successful outputs
and passes their bytes to later commands. Requested output files are excluded
from the inputs, so an older result cannot satisfy a new command. Failed, cancelled or incomplete commands preserve prior
successful outputs. Use different paths for inputs and outputs. Neither
ExpansionHunter nor REViewer creates output-prefix parent directories;
the presets use flat output prefixes.

The standard-input controls support commands such as `samtools view` that
accept `-`. Saving standard output as a workspace file preserves binary BAM
bytes as well as text. Cancellation terminates the worker, and the time limit
includes program loading. Clearing files or reloading the page removes the
page's workspace.

Input copies, outputs and program memory occupy browser memory. Start with
small regions and samples. Validation of these fixtures does not establish
whole-genome capacity or clinical validity.

The UI test uses the real WASM programs on ordinary static HTTP. It checks
the full call-to-plot workflow, known genotypes, binary downloads, SVG image
loading, uploaded files and folders, standard input, failure recovery,
cancellation, timeouts and local-only asset loading:

```sh
npm ci
npx playwright install --with-deps chromium firefox webkit
BROWSER=chromium node scripts/test-repeats-demo.mjs
BROWSER=firefox node scripts/test-repeats-demo.mjs
BROWSER=webkit node scripts/test-repeats-demo.mjs
```

`REPEATS_DEMO_ROOT` selects an extracted ZIP root; `REPEATS_DEMO_REPORT`
selects the report directory. Scientific comparisons against native builds
are separate release checks. See the [JavaScript API](../../runtime/REPEATS.md).
