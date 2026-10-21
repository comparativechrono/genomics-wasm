# HTS validation

The case plan runs 59 cases for 1.16 and 61 for 1.21. Every included stock
plugin (39 per version) processes actual input and produces checked scientific
output. The inventory is checked against the pinned build inventory, so a
missing plugin or a help-only substitute cannot satisfy release coverage.

Each case runs three independent workspaces: unchanged upstream native
bcftools with dynamic plugins, patched native with the built-in plugin
registry, and WebAssembly through the same `HTSRunner` used by the demo.
No comparison assumes that different upstream versions produce identical
results. Version-specific plans handle `mendelian`/`mendelian2`, older
`variant-distance` and `remove-overlaps` options, and 1.21 automatic indexing.

| Area | Scientific coverage |
| --- | --- |
| VEP extraction | All transcripts, primary transcript, BCSQ, exact fields and 24-row independent truth |
| Allele annotations | Every synthetic site's AN/AC/AF/NS independently recounted; populations, haploid, half-missing, HWE and zero-AN upstream cases |
| Family analysis | Mendelian annotation; trio statistics and independent five-site truth; switch rates; de novo likelihood, DNG and naive models; parental origin; chromosome colouring |
| All other plugins | Actual annotation, filtering, likelihood conversion, interval, subset, split/scatter and statistics outputs |
| Core variant tools | VCF/BCF roundtrip, index queries, multiallelic normalization, mpileup plus both callers with known heterozygous variant, 1.21 auto-index |
| Alignment tools | SAM/BAM/CRAM conversion and indexing, region queries, counts, flagstat, depth, pileup, FASTA indexing, native-oracle decoding |
| HTSlib utilities | bgzip roundtrip, tabix region queries, htsfile format detection, 64-bit logical and virtual offsets |
| Portability | GTisec four-sample lookup; aggregate depth above 2^32; optical coordinates above INT32_MAX; seek above 4 GiB; SAMtools subsampling seed parsing above 2^32 |

The SAMtools subsampling regression checks seed zero and signed seed values
that wrap to zero in upstream's `uint32_t` storage. All four commands must
retain the same eight known records, including both mates of the retained
pair. This catches 32-bit `strtol` saturation without assuming that nonzero
seeds use the same libc random generator on all platforms. A separately
seeded 1.21 `+setGT` case exercises HTSlib's portable POSIX random generator.

## Comparison policy

Scientific values compare exactly, including numeric output. No statistical
or numerical tolerance was needed for the exercised cases. VCF parsing keeps
record order, sample order, variant identities, genotype phasing/missingness,
annotations and FORMAT values. INFO and FORMAT dictionary ordering and SAM
optional tag ordering are immaterial. Only specifically identified provenance
is canonicalized: bcftools `*Version`/`*Command` headers, the executable prefix
in the bcftools stats command line and SAMtools markdup `COMMAND` line, and
SAM reference `@SQ UR` directory paths. Scientific headers remain checked.

BGZF compression layout may differ; its decoded bytes compare exactly and
physical offsets are independently checked. BCF/BAM/CRAM bytes are retained
and hashed, then decoded both by each target's own implementation and by the
unchanged native decoder. Decoding steps' input hashes bind those exact binary
artifacts. SAM CRAM roundtrips preserve reference-dependent MD/NM annotations
as produced by the matched native implementation. Comparator mutation tests
reject changed alleles, genotypes, depth/count values, transcript annotations,
Mendelian errors, CRCs and optical-duplicate classifications.

## Running and evidence

```sh
python3 scripts/fetch-hts-test-data.py --fetch-sources --regenerate
python3 scripts/fetch-hts-test-data.py --verify
node scripts/validate-hts.mjs --version 1.21 --engine node
node scripts/validate-hts.mjs --version 1.21 --engine chromium
# Repeat for firefox, webkit and version 1.16.
python3 -m unittest tests.test_hts_compare
```

Browser runs use ordinary HTTP hosting with `crossOriginIsolated=false` and a
fresh worker for each command. A UUID directory preserves raw step evidence,
stdout/stderr, initial inputs, intermediate and final outputs. The published
summary is `reports/hts-<version>/<engine>/summary-all.json`; publication is
atomic and occurs only after the full run. Failed reports remain explicitly
failed. Reports bind every fixture, build input, executable/plugin, distribution
asset, runtime module, validator, case plan, comparator and generator. Per-step
records bind cumulative inputs and produced outputs by SHA-256 and byte length.
Comparisons are checked against those captured bytes, and the package gate
re-runs the comparator from immutable evidence.

This suite establishes equivalence on the stated fixtures and commands, not
complete proof over every CLI option or biological data set. Upstream algorithms
and limitations remain upstream algorithms and limitations; see the tool's
portability notes for documented unsupported threading and libc-dependent
nonzero-seed SAMtools subsampling.
