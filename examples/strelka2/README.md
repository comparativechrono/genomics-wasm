# Strelka2 browser example

Run `python3 -m http.server 8000` from the package or repository root, then open `http://localhost:8000/examples/strelka2/`. The server serves files; the analysis runs in browser workers. Ordinary static hosting is sufficient.

Load the real HCC1395 tumour/normal example, choose a command, and run it. You can upload your own reference, BAMs and indexes and edit the arguments. The reference and both BAM sequence dictionaries must agree. Input files and retained outputs are available to subsequent commands until you reload or clear the page.

The default workflow estimates normal-sample depth, calls and scores SNVs and indels, and produces compressed VCFs with indexes and run statistics. The exome setting disables the high-depth filter, as it does upstream. Callable regions are an optional output. Each stage uses the original compiled program; JavaScript schedules those stages and handles the files.

Arguments are a JSON array, with one entry per argument. The command box is not a Unix shell. The output lists select the files to return and keep in memory. An unsuccessful or cancelled run preserves previous results.

See [the runtime documentation](../../runtime/STRELKA2.md) for the API and supported options, [the tool notes](../../tools/strelka2/README.md) for build requirements and limits, and [the data documentation](../../datasets/strelka2/README.md) for provenance and coordinate mappings.
