# RNA counts, transcripts and coverage

This example runs featureCounts, StringTie and mosdepth in your browser. Load a real human or yeast dataset, select a program and command, and edit the arguments as needed. You can also upload your own files. Output files remain available for later commands and can be downloaded. Reloading clears the files.

The input subsets are small. Their results describe the selected reads and reference regions, not an entire sample or organism. See [data sources](../../datasets/rna-analysis/README.md).

## Start the page

Standalone packages include the compiled program and example data. For a source checkout, build the tools first. From the package or repository root:

```sh
python3 examples/rna-analysis/serve.py
```

Open `http://localhost:8000/examples/rna-analysis/`. The server serves static files only; analysis runs in the browser.

## Hosting

Threaded builds need cross-origin isolation and shared memory. The included server sets these response headers:

```text
Cross-Origin-Opener-Policy: same-origin
Cross-Origin-Embedder-Policy: require-corp
```

On HTTPS static hosting such as GitHub Pages, choose **Enable threaded tools and reload** before loading data. This installs the included same-origin service worker, scoped to this example. It adds the required headers without caching data or controlling other example pages. Keep the repository's directory layout when hosting the page.

## Commands and files

Arguments are a JSON array: each string is one command-line argument, without the program name. The runner does not interpret shell pipes or redirection. Use the filenames listed on the page. To retain a generated file, put its path in **Files to keep**, or list its containing directory in **Directories to keep**.

**Cancel** terminates the current worker. A later command starts with a fresh instance of the program and receives the retained input and output files. Failed or cancelled commands do not replace earlier successful outputs.

See each tool's README for its supported options and memory limits. This example is a small interface to the standalone JavaScript API, documented under [runtime](../../runtime/README.md).
