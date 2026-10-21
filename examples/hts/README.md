# BCFtools, SAMtools and HTSlib browser demo

This example uses the separately built, matched 1.21 and 1.16 toolchains. Each
contains BCFtools with built-in plugins, SAMtools, bgzip, tabix and htsfile. A
standalone release ZIP includes only its named version; the source checkout can
offer both versions through `config.json`.

From the repository root, prepare the builds and serve the repository:

```sh
python3 scripts/build-hts.py --version 1.21
python3 scripts/build-hts.py --version 1.16
python3 -m http.server 8000
```

Open <http://localhost:8000/examples/hts/>. When using an extracted release ZIP,
serve its top-level folder directly and skip the build commands. Ordinary static
HTTP is sufficient; no cross-origin isolation headers, server-side execution or
external runtime downloads are required. Opening the HTML through `file://` does
not provide the module and worker loading that browsers require.

## Working with variants and families

Load the synthetic example data, choose a version, and run a preset. The first
presets expose `+split-vep`, `+fill-tags`, `+trio-stats`, the version's Mendelian
plugin, `+trio-dnm2` and `+trio-switch-rate`. The plugin-list preset shows the full
inventory of the selected build. Use the normal `+name` command syntax in the
editable JSON argument array; plugin flags and the position of `--` follow the
corresponding upstream version.

The 12-variant VCF includes structured VEP CSQ annotations, four samples, missing
calls, haploid calls, multiallelic variants and deliberate Mendelian
inconsistencies. All inputs are synthetic controls. The genotype-only de novo
preset demonstrates the upstream NAIVE model; its flags are not confirmed de
novo findings. See [dataset provenance](../../datasets/hts/README.md).

BCFtools 1.16 uses `+mendelian -p family.ped`; 1.21 uses
`+mendelian2 -P family.ped`. The preset changes automatically with the selected
version. Other upstream plugin differences remain visible in their command help.

## Files, chaining and downloads

Upload one or more inputs and their indexes or references, or upload a folder to
retain relative paths. Command paths are relative to an in-memory `/work`
directory. Files do not leave the browser. Browser memory holds the inputs,
outputs and program, so begin with small regions and samples.

Successful files listed under **Files to keep** become downloadable workspace
files and can be used by subsequent commands. For example:

1. Run **Sort alignments to BAM**, then **Index the generated BAM**.
2. Run **BGZF-compress the VCF**, then **Index the compressed VCF**, then
   **Query the indexed VCF**.
3. Run **Calculate allele counts and frequencies**, then change a query's input
   filename to `filled.vcf` to use the new tags.

The **Standard input and output** section accepts a loaded file or entered text
as stdin. Use `-` in the arguments where supported. Saving standard output as a
workspace file preserves its exact bytes, including BGZF, BAM and BCF. Each
stream also has its own complete download. Text previews are limited to 100,000
bytes; binary previews show a download prompt.

Each invocation uses a fresh worker and memory filesystem. Files persist in the
page between commands and version changes, and are passed into the next worker.
Explicit output paths must be newly generated; existing files with those exact
paths are not supplied as inputs. Use a different filename for a transformation's
input and output. Folder collection can include existing files within that folder.
Failed, cancelled or incomplete commands preserve prior successful outputs.
Cancel terminates the worker; the editable time limit provides the same cleanup
for a timeout. Clearing files or reloading the page removes the page's workspace.

## Browser verification

The UI test runs every preset using real local WebAssembly, verifies downloaded
text and binary outputs, exercises generated BCF/BAM/CRAM and indexed VCF files,
uploads, stdin, version isolation, error recovery, cancellation and timeouts. It
records served asset digests, worker dispatches and downloads, and rejects
unexpected external requests or uncaught browser errors.

```sh
npm ci
npx playwright install --with-deps chromium firefox webkit
BROWSER=chromium node scripts/test-hts-demo.mjs
BROWSER=firefox node scripts/test-hts-demo.mjs
BROWSER=webkit node scripts/test-hts-demo.mjs
```

`HTS_VERSION=1.16` or `HTS_VERSION=1.21` restricts the check to that configured
version; the default `all` checks every version listed in the demo config.
`HTS_DEMO_ROOT` selects an extracted ZIP root, and `HTS_DEMO_REPORT` selects its
report directory. By default reports are written under
`reports/hts/<browser>/demo/`. Native scientific comparisons and package checks
are separate release gates; UI tests do not replace them.

See the [JavaScript API](../../runtime/HTS.md) and
[build and plugin documentation](../../tools/hts/README.md).
