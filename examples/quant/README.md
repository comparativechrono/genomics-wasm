# RNA quantification example

This page runs actual compiled programs in the browser. It keeps input and output files in memory; reloading clears them. Select a real human or yeast read subset, build the selected program's transcript index, then choose **Quantify reads**. You can edit the arguments, add your own files, inspect the original output tables and download the generated files.

The sample data uses a restricted transcript reference and small read subsets. It checks that commands work; it is not a whole-transcriptome expression study. The dataset README records the sources and selection. Each program needs its own index, built with this WebAssembly build.

## Start the page

Build the included tool and fetch the example data first if using a source checkout. Standalone release packages contain their compiled tool and data. From the extracted package or repository root, run:

```sh
python3 examples/quant/serve.py
```

Open `http://localhost:8000/examples/quant/`. This server only serves static files. All indexing and quantification happens in the browser; it does not run the programs on the server.

## Hosting

kallisto can use ordinary static hosting. Salmon's worker threads need `SharedArrayBuffer` and cross-origin isolation. Serve the page and assets over HTTPS (or localhost), with these HTTP response headers:

```text
Cross-Origin-Opener-Policy: same-origin
Cross-Origin-Embedder-Policy: require-corp
```

The supplied local server sets both. On static hosts such as GitHub Pages, select Salmon and choose **Enable threaded tools and reload** before loading data. This installs the included same-origin service worker for the `examples/quant/` path. The worker adds the headers to responses for this example; it does not cache data, fetch third-party scripts or control the other example pages. It needs HTTPS (localhost also works) and a browser that permits service workers. The reload clears the files in the tab. Hosting-level headers are preferable when you control the server.

While the example is controlled by this service worker, it loads the JavaScript runtime and compiled programs through `examples/quant/__assets__/` URLs. The worker maps only the named runtime and quantifier assets to their existing files. This keeps the main worker and Salmon's pthread workers within its scope, so their responses also receive isolation headers. The compiled files are unchanged.

If your browser still reports that isolation is unavailable after the reload, use the local server or hosting-level headers. The page checks isolation before starting Salmon and does not repeatedly reload itself. You can remove the worker in your browser's site settings to undo this hosting setup.

## Arguments and files

Arguments are a JSON array, without the executable name. Each string is one argument, including paths containing spaces. There is no shell: pipes, environment-variable expansion and redirection are not interpreted. Standard output and standard error are returned as bytes.

The default commands use paired FASTQ reads named `reads_1.fastq` and `reads_2.fastq`, and a transcript FASTA named `transcripts.fa`. Uploaded files keep their filenames; update the arguments if yours differ. Consult the program's own documentation for single-end reads, library orientation, bias correction and other options supported by the port.

Use the output file list for individual files and the directory list for output folders. A directory retains its contents, including the files needed to reuse a Salmon index. Each command gets a fresh worker and filesystem. Its successful outputs are copied back to the page and supplied to later commands. A failed or cancelled command does not replace outputs from a previous successful run. To avoid mixing inputs, loading an example dataset clears existing files; use **Add files** to retain them.

The displayed abundance tables are the unmodified tool outputs. kallisto and Salmon use different column names and models, so their values should be compared with the original column definitions and options in mind. Downloads preserve the original bytes, even when the preview is shortened.

**Cancel** terminates the active worker. A subsequent run starts a new worker. Memory and runtime limits depend on the browser; start with small references and read files. This example does not establish that a whole human transcriptome fits in every browser.
