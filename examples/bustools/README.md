# bustools in the browser

From the repository or extracted package root, run `python3 -m http.server 8000` and open `http://localhost:8000/examples/bustools/`. The original bustools 0.45.1 executable runs locally in a browser worker. Ordinary static hosting works; a service worker and cross-origin isolation are not required.

Load **Mouse single-cell RNA-seq BUS records**, then run the numbered commands in order:

1. **Sort BUS records** creates `sorted.bus` from the supplied `input.bus`.
2. **Correct cell barcodes** uses the supplied barcode on-list to write `corrected.bus`.
3. **Sort corrected records** prepares `corrected-sorted.bus` for counting.
4. **Count molecules by gene** uses the equivalence-class map, transcript names and transcript-to-gene map to produce `counts/genes.mtx`, `counts/genes.barcodes.txt` and `counts/genes.genes.txt`. Matrix rows correspond to barcodes and columns to genes.

The dataset contains authentic mouse single-cell RNA-seq BUS records from SRR8599150, derived from the pinned upstream kb_python test fixture. It is a small processing example, not a complete single-cell analysis. See the [data documentation](../../datasets/bustools/README.md) for the source, preparation, size and checksums.

After the numbered steps, **Inspect corrected BUS records** writes a JSON report and **View corrected BUS records as text** exports barcode, UMI, equivalence-class and count fields. **Compress sorted BUS records** writes a BUSZ file; **Restore compressed BUS records** restores the BUS bytes. These operations use the original compiled C++ implementation.

Arguments are an editable JSON array, with the bustools command first and one string per argument. Do not include the executable name. Shell pipes and redirection are not interpreted. Select **Command help** and replace `sort` with a command name to view its options. The port includes all upstream subcommands; the presets cover a common workflow. Sorting uses one thread, with an explicit `64M` memory budget and a temporary-file prefix. Browser storage is an in-memory filesystem, so inputs, temporary sort files and outputs all contribute to memory use. Use modest datasets that fit the browser's available memory and the port's 2 GiB heap limit.

Upload your own BUS files, matching reference maps and barcode lists. Folder uploads preserve their relative paths. Use the names shown in the file list in your command arguments. Files stay on this device and remain available to later commands until you clear them, load another example or reload the page.

**Files to keep** lists outputs that each command must regenerate. Use distinct input and output names: an explicit output is not mounted as a previous input. **Directories to keep** collects every file in each directory, including any source files already there. Specify known output files as well as a directory when you need to check they were newly produced. A successful command stores the returned files for later steps. An unsuccessful or cancelled command preserves earlier results.

Text previews are limited to the beginning of a file. Download links contain the complete, unchanged bytes, including binary BUS and BUSZ files. Standard output and standard error also have separate downloads. **Cancel** terminates the current worker; another command can be run immediately afterwards.

The JavaScript API is documented in [runtime/BUSTOOLS.md](../../runtime/BUSTOOLS.md); build instructions and scope are under [tools/bustools](../../tools/bustools/). The same example and tests are included in the standalone bustools ZIP.
