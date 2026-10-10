# Browser example

Build the tool packages, then serve the repository root over HTTP:

```sh
python3 -m http.server 8000
```

Open `http://localhost:8000/examples/browser/`. The page imports the local runtime and `dist/bwa/bwa.mjs` or `dist/freebayes/freebayes.mjs`; it does not download a tool from a CDN. Missing compiled files produce a loading error rather than simulated output.

Upload the files needed for a command. Change the example arguments to match their names; arguments are a JSON array, not a shell command. For BWA, run `index` first and retain the five index files listed in the example, then choose an alignment command. For FreeBayes, supply a reference and its `.fai`, a coordinate-sorted BAM and its `.bai`. The command examples use standard placeholder filenames, not included datasets.

Uploaded files and requested generated files remain in the page's memory between commands. Each tool command itself gets a fresh filesystem. Download links preserve the original output bytes; the text preview is limited to 200,000 bytes. Cancel terminates the worker, including a command blocked in synchronous WebAssembly. Reloading the page clears files and results.

No input data is uploaded by this example. The browser loads JavaScript and WebAssembly from the host serving the page. Tool validation is a separate test suite; successful loading of this page is not evidence of native-output equivalence.

After installing the development dependencies and the Playwright browsers, run `node scripts/test-demo.mjs` from the repository root. Set `BROWSER=firefox` or `BROWSER=webkit` to select another browser. The test uses the actual compiled BWA and FreeBayes modules and the recorded human fixture. It checks uploads, BWA indexing and repeated alignment, FreeBayes calling, and byte-preserving downloads. It refuses to run without both compiled modules.
