# Metagenomics browser example

Run `python3 -m http.server 8000` from the package or repository root, then open `http://localhost:8000/examples/metagenomics/`. The server serves files; the tools run in browser workers. Ordinary static hosting is sufficient. The package includes its runtime files and does not fetch code from a CDN.

Load the real mock-community example and choose **Classify paired reads**. Kraken2 writes one classification per read pair and a taxonomic report. Try the confidence setting to compare assignments, or rebuild and inspect the database. A database build uses the supplied reference FASTA, taxonomy and sequence-to-taxon map, with masking explicitly disabled.

When Bracken is included, run Kraken2 first, then choose **Estimate species abundances**. Bracken uses the retained `sample.report` and a distribution for this database and 151-base reads. The genus command changes the reporting level. **Build the read-length distribution** runs the original reference classifier, compiled distribution helper and Python distribution generator. Rebuild the distribution after changing the database or read length.

The example contains 4,096 unchanged paired reads from a sequenced Zymo mock community. Its small database contains selected genuine reference regions from E. coli, Salmonella and Shigella. Most reads come from sequences outside this collection. These inputs test how the implementations behave; this limited database cannot give an accurate composition of the original community. See [the data documentation](../../datasets/metagenomics/README.md) for accessions, selection and checksums.

You can upload files or a whole database folder. Folder uploads retain their relative paths: a folder named `my-db` is used with `--db my-db`. If you upload `hash.k2d`, `opts.k2d` and `taxo.k2d` individually, use `--db .`. Bracken also needs the matching `database<length>mers.kmer_distrib`; a local build requires the reference library, taxonomy and sequence map. Use small databases that fit within the build's 2 GiB WebAssembly heap and the browser's available memory. All commands run one thread.

Arguments are a JSON array, with one entry per argument. This box does not parse shell syntax. The output lists select the files to return and retain. Explicit output files are regenerated; collecting an output directory also retains its source files, so you can collect `db` while rebuilding its database. Inputs and successful outputs are available to later commands until you clear or reload the page. An unsuccessful or cancelled run preserves previous results. Each output can be previewed or downloaded.

Each included tool has API instructions under `runtime/` and build and scope notes under `tools/`. The Kraken2 package contains Kraken2; the Bracken package also contains its Kraken2 dependency.
