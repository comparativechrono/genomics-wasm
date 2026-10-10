# Browser example

Build the tool packages, then serve the repository root over HTTP:

```sh
python3 -m http.server 8000
```

Open `http://localhost:8000/examples/browser/`. The page imports the local runtime and the selected module from `dist/<tool>/<tool>.mjs`. It does not download a tool from a CDN. Missing compiled files produce a loading error rather than simulated output.

Upload the files needed for a command. Change the example arguments to match their names; arguments are a JSON array, not a shell command. The examples use placeholder filenames, not included datasets.

| Tool | Inputs and outputs |
| --- | --- |
| BWA and BWA-MEM2 | Run the selected tool's `index` command first, keeping its five generated index files, then run alignment. The two tools use different indexes. SAM is written to standard output. |
| FreeBayes | Supply a reference and its `.fai`, a coordinate-sorted BAM and its `.bai`. VCF is written to standard output. |
| AdapterRemoval | Supply single or paired FASTQ reads. Trimmed FASTQ files and a settings report appear as separate downloads and stay available to later commands. |
| seqtk | Supply a FASTQ file. `trimfq` writes trimmed FASTQ to standard output; the examples show quality trimming and fixed-length trimming. |
| HISAT2 | Run `build` first, keeping all eight `.ht2` files. Then use `align` with reads. The example saves `alignment.sam` as a file. `inspect` reads the index. |
| minimap2 | Supply a reference FASTA and reads directly, or build an `.mmi` index first. Use the same preset for indexing and alignment. `splice:sr` is for short RNA reads; `splice` is for long RNA reads. SAM is written to standard output. |

The ports run with one processing thread. Start with small references and read sets. Clear the files and upload your inputs again when changing to an aligner that needs a different index. See each tool's README for its supported scope and limits.

Uploaded files and requested generated files remain in the page's memory between commands. Each tool command itself gets a fresh filesystem. Standard output can be downloaded and uploaded again as input for another command. Download links preserve the original bytes; the text preview is limited to 200,000 bytes. Cancel terminates the worker, including a command blocked in synchronous WebAssembly. Reloading the page clears files and results.

No input data is uploaded by this example. The browser loads JavaScript and WebAssembly from the host serving the page. Tool validation is a separate test suite; successful loading of this page is not evidence of native-output equivalence.

After installing the development dependencies and the Playwright browsers, run `node scripts/test-demo.mjs` from the repository root. Set `BROWSER=firefox` or `BROWSER=webkit` to select another browser. The test uses the actual compiled modules and recorded real datasets. It checks uploads, indexing, trimming, RNA alignment, variant calling and byte-preserving downloads. It refuses to run if a compiled module is missing.
