# Somatic variant calling in the browser

This example runs SomaticSniper and LoFreq locally. Load the real HCC1395
tumour/normal subset or upload your own BAM, index, reference and region files.
The command arguments are editable. Successful output files remain available
for later commands and can be downloaded.

SomaticSniper calls SNVs from a tumour and a matched normal. LoFreq supports
single-sample calls and a tumour/normal workflow. Its authors describe the
somatic workflow as experimental and unfinished.

## Run

Build the selected tool from the repository root, or extract its standalone ZIP:

```sh
python3 -m http.server 8000
```

Open `http://localhost:8000/examples/somatic/`. This is ordinary static hosting:
analysis needs no server process, service worker or cross-origin isolation.
Keep the directory layout when copying it to another static host. Each
standalone package lists only its included tool.

## Commands and files

Arguments are a JSON array without the program name. Each entry is passed
literally: shell pipes, redirection and wildcard expansion are not interpreted.
Use the filenames shown in the file list. Add desired output paths to **Files
to keep**, or a directory to **Directories to keep**. The downloads preserve the
original bytes; compressed VCFs can be read with standard VCF tools.

For SomaticSniper, the tumour BAM comes before the normal BAM. Its version flag
is `-v`. An empty argument list prints usage and returns status 1, matching the
native program.

The LoFreq **Call somatic SNVs** preset uses the original input BAMs. For indels,
first run **Add tumour indel qualities**, **Add normal indel qualities**, and
both **Index the prepared … BAM** commands. Then run **Call somatic SNVs and
indels**. These steps use LoFreq's own `indelqual` and `index` implementations.
The original alignments remain loaded alongside the prepared copies.

LoFreq's **Call variants in the tumour** preset analyses one sample and does not
classify variants as somatic. The somatic presets compare both samples and
produce separate final SNV and indel VCF files. The indel file is also produced
when indel calling is disabled and may be empty.

**Cancel** stops the running command, including a multi-step LoFreq workflow.
A new command starts with fresh program state. Files occupy browser memory and
are cleared on page reload. The small supplied regions do not establish
whole-genome capacity or performance.

## Data and validation

See [the data documentation](../../datasets/somatic/README.md) for source
accessions, checksums, region selection and coordinate mappings. These are
real matched tumour/normal reads; no variants were inserted into them. The
small reference contigs contain original genomic sequence, with alignments
and reference calls moved to the same local coordinates.

The build and comparison instructions are in
[SomaticSniper](../../tools/somaticsniper/README.md),
[LoFreq](../../tools/lofreq/README.md) and
[the validation documentation](../../validation/somatic/README.md).
The tests compare native and browser results. Agreement checks the port; the
published reference calls provide separate evidence about biological accuracy.
