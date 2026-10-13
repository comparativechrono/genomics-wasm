# MACS3 scientific validation inputs

Pinned upstream MACS v3.0.5 test data, commit c5443190e3edfeb301cc94acf450e2b2c026a223. CTCF chr22 ChIP/control reads and fragments are unchanged public upstream fixtures. The ATAC fixture contains every chrI fragment in upstream yeast_500k_SRR1822137.bedpe.gz (S. cerevisiae SRR1822137). Small callvar BAMs retain complete, original aligned records overlapping the upstream callvar test regions, padded by 2000 bases. Header, sequence, quality, flags, CIGAR and tags are preserved. Synthetic bedGraph values are explicitly labelled and enable independent arithmetic/interval truth checks.

Every source is pinned by download URL, SHA-256, byte count and Git blob identity. provenance/fixture-sources.json records transformations per output. Files are software-validation fixtures, not a new biological study or a diagnostic resource. The manifest contains no generated result goldens.

Verify: `python3 scripts/fetch-macs3-test-data.py --verify`.
Regenerate: install pysam==0.23.3, then `python3 scripts/fetch-macs3-test-data.py --regenerate --fetch`.
