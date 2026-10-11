# Cutadapt browser example

This page runs Cutadapt on real human and yeast read subsets or files you
upload. It provides paired-read and single-file commands, editable arguments,
text previews and complete output downloads. It uses the same standalone
JavaScript API as other applications can use.

Build the program assets and prepare the data as described in
[the tool documentation](../../tools/cutadapt/README.md) and
[the data documentation](../../datasets/cutadapt/README.md). From the repository
root, start an ordinary static server:

```sh
python3 -m http.server 8000 --bind 127.0.0.1
```

Open <http://127.0.0.1:8000/examples/cutadapt/>. Opening the HTML directly with
`file://` does not work. This example needs no special isolation headers or
service worker. Serve `runtime/`, `dist/cutadapt/`, `datasets/cutadapt/` and
`examples/cutadapt/` with their relative paths intact. The Cutadapt distribution
contains its Python runtime and dependencies; the page loads these assets from
the same server, without fetching packages from an external service.

Choose a dataset and select **Load example data**, then choose a command and
select **Run**. The initial command loads the Python runtime and packages, so it
can take longer. All computation runs in a worker in your browser. **Cancel**
stops that worker; a later command starts a fresh one.

The presets trim the listed 3′ adapter sequences and bases below quality 5,
then discard reads shorter than 20 bases. The paired command discards a pair
if either read becomes too short. These are example settings: choose adapters
and quality/length thresholds that match your own data. The human subset was
recovered from an existing BAM and has already been processed; the yeast subset
contains selected raw reads with adapter signal. Neither subset describes the
full original sequencing run.

Arguments are a JSON array of strings without the word `cutadapt`. For example,
`["--help"]` prints the command-line help. The interface does not parse a Unix
shell command. Use the exact uploaded filenames in the argument array and list
the output paths under **Files to keep** or **Directories to keep**.

Successful output files stay in the page and can be inputs to later commands.
For example, a second command can read `trimmed_1.fastq` and write another
filename. Reloading clears the workspace. A failed or cancelled command leaves
previously retained files available. Uploading a file with an existing name
replaces that file. Previews are limited to 100,000 bytes; downloads retain all
bytes, including compressed files. Use small inputs first because files and
results are held in memory.

The browser test uses the actual Cutadapt build and real files. It checks
paired and single presets, uploaded files, reused outputs, previews, downloads,
cancellation, reload and help, while rejecting external network requests:

```sh
BROWSER=chromium node scripts/test-cutadapt-demo.mjs
BROWSER=firefox node scripts/test-cutadapt-demo.mjs
BROWSER=webkit node scripts/test-cutadapt-demo.mjs
```

Install the matching Playwright browser before running a test. Reports record
the exact asset hashes, browser version, network requests and test outcomes.
The separate numerical validation compares Cutadapt results with the native
program; a successful UI test alone is not a claim of complete numerical
coverage.
