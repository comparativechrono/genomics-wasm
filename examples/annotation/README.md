# Sequence annotation in the browser

From the repository or extracted package root, run `python3 -m http.server 8000` and open `http://localhost:8000/examples/annotation/`. Ordinary static hosting is sufficient. Computation runs locally in browser workers, and all program assets are included in the package.

For **Prodigal**, load **Microbial reference sequences** and choose **Predict genes in a genome**. This uses an unchanged 60,000-base E. coli chromosome window and writes gene coordinates (`genes.gff`), translated proteins (`proteins.faa`) and coding DNA (`genes.fna`). **Predict genes in mixed contigs** runs metagenomic mode on E. coli and Salmonella windows together. The windows are small examples, not complete genomes. Single-genome mode trains on the input; use enough appropriate sequence for a meaningful model. Prodigal predicts bacterial and archaeal protein-coding genes.

For **HMMER**, load **Protein families and a DNA repeat** and run these commands in order:

1. **Build a globin profile** creates `globins.hmm` from the supplied real alignment.
2. **Prepare the profile for scanning** creates its four `hmmpress` indexes.
3. **Scan proteins against the profile** searches the supplied protein collection and returns per-sequence and per-domain tables plus the full report.

You can also search the protein collection directly with `hmmsearch`, align haemoglobin beta to the generated profile, or search using the haemoglobin sequence with `phmmer`. The DNA presets build a MADE1 repeat profile and search a human chromosome window with `nhmmer`. See the [data documentation](../../datasets/annotation/README.md) for exact sources, selection and checksums.

When both tools are included, **Compare Prodigal's predicted proteins** uses the retained `proteins.faa` from a Prodigal run. Switch to HMMER and select this command without loading another dataset. It compares the predicted proteins with one another, including self-matches. Similarity alone does not establish a protein's function; supply a suitable annotated profile database for functional annotation.

Arguments are an editable JSON array with one string per argument. Prodigal takes its options directly; HMMER takes a program name first, followed by that program's options. Supported HMMER programs are `hmmbuild`, `hmmsearch`, `hmmscan`, `hmmpress`, `hmmfetch`, `hmmalign`, `hmmconvert`, `hmmemit`, `hmmstat`, `phmmer`, `jackhmmer`, `nhmmer` and `nhmmscan`. Shell pipes, redirection and quoting are not interpreted.

Upload your own FASTA sequences, Stockholm alignments, HMM profiles or database indexes. Folder uploads preserve relative paths. The file list shows the exact names to use in arguments. Use modest inputs and profile collections that fit in browser memory. Each command runs in an isolated worker with one thread.

**Files to keep** identifies explicit output files, which are regenerated on each command. **Directories to keep** returns every file in those folders, including retained source files. Successful outputs remain available to subsequent commands and can be previewed or downloaded. An unsuccessful or cancelled run preserves earlier results. Loading an example replaces the current files; clearing or reloading the page also removes them. Downloads contain complete, unchanged file bytes, including binary indexes.

The standalone packages show only their included tool. API documentation is under `runtime/`; upstream versions, build details and scope are under `tools/`.
