# MACS3 browser example

Run MACS3 3.0.5 locally in a browser using its complete upstream command-line
interface. The page includes presets for all 14 subcommands, editable arguments,
file uploads, previews, output downloads and cancellation. Narrow, broad and
paired-end peak-calling examples use public upstream fixtures. The bedGraph
examples use explicitly synthetic score tracks with known values. Consult
[the dataset documentation](../../datasets/macs3/README.md) for provenance,
selection and limitations.

Build MACS3 and prepare the data as described in
[the tool documentation](../../tools/macs3/README.md). From the repository root:

```sh
python3 -m http.server 8000 --bind 127.0.0.1
```

Open <http://127.0.0.1:8000/examples/macs3/>. Use an HTTP server; `file://` pages
cannot load the worker. Serve `runtime/`, `dist/macs3/`, `datasets/macs3/` and
`examples/macs3/` with their relative paths intact. No external package server,
service worker or cross-origin isolation headers are required. Use HTTPS when
hosting remotely, or HTTP on localhost, for WebCrypto checksum verification.
Python, MACS3
and its scientific dependencies are bundled and loaded from the same server.

Choose **Load example data**, select a command and select **Run**. The first
command downloads the Python runtime and scientific dependencies and may take
a while. Each command starts in a fresh worker so Python imports, logging,
random seeds and temporary files cannot leak into later runs. **Cancel**
terminates that worker; a subsequent command can start normally.

Arguments are a JSON array of strings without the program name. For example,
`["callpeak", "--help"]` shows the complete options for peak calling. Shell
pipes, quoting and redirection are not interpreted. Filenames in the arguments
must match those shown in the workspace. Upload BAM indexes alongside their BAM
files. MACS3 BEDPE uses three columns (chromosome, left coordinate, right
coordinate), unlike BEDTools BEDPE. All input and output paths are relative to
an isolated `/work` directory.

The examples demonstrate parameter choices, not recommendations for other
experiments. For your own data, set the effective genome size, input format,
control, fragment model, cutoffs and output names deliberately. The default
HMMRATAC preset generates its cutoff-analysis report; edit its arguments to
train or apply a model after selecting appropriate cutoffs. A separate preset
trains and applies a Poisson model on the small yeast subset with 1 kb flanks.
A Gaussian preset learns fragment distributions and selects training regions
using the upstream defaults. Single-process
`callvar` retains its native assembly and scoring code. Browser datasets and
outputs are held in memory, so start with small inputs.

List desired files under **Files to keep**, or an output directory under
**Directories to keep** to retain all generated files in it. A successful
command adds those files to the workspace so another command can consume them.
For example, after **Generate a coverage track**, change the input of
**Call peaks from a score track** to `pileup.bdg` and select a suitable coverage
cutoff. A path selected as output is replaced only after a successful command;
use a new filename when transforming an existing file. Failed and cancelled
commands leave previously retained files available. Reloading clears the page's
workspace. Uploading a file with an existing name replaces that file.

Previews show up to 100,000 bytes of text. Downloads contain every byte,
including compressed or binary files. Standard output and error from the last
completed command have separate download links. MACS3 usually writes progress
messages and fragment-length estimates to standard error.

Browser tests use the real compiled program and verify output content,
command chaining, file uploads, downloads, cancellation and reload:

```sh
BROWSER=chromium node scripts/test-macs3-demo.mjs
BROWSER=firefox node scripts/test-macs3-demo.mjs
BROWSER=webkit node scripts/test-macs3-demo.mjs
```

Install the corresponding Playwright browsers first. Numerical validation
separately compares native and WebAssembly outputs; browser interaction tests
are not a substitute for that scientific comparison.
