# Sources and reuse

- Primer3: https://github.com/primer3-org/primer3/tree/v2.6.1,
  revision `7f9f17d6012f404e83bbf0f931a4d06eb4af465b`.
  Upstream source/test data are distributed under the notices in the pinned
  archive. The general Primer3 GPL version 2 license text is preserved as
  `PRIMER3-LICENSE`; the packaged source includes all component notices,
  including GPL version 3 terms for Amplicon3. The fixture generator adapts
  the documented binary masker index format; it does not copy genome indices.
- NCBI RefSeq HBB transcript NM_000518.5:
  https://www.ncbi.nlm.nih.gov/nuccore/NM_000518.5
  and the exact EFetch URL in `sources.lock.json`. The source is a public
  reference sequence, used as factual biological sequence data with its
  accession and provenance retained. NCBI policies are at
  https://www.ncbi.nlm.nih.gov/home/about/policies/.

Parameters, arbitrary masker counts and selected repeat-library fragments are
software validation fixtures. Primer predictions and melting temperatures here
are comparisons of implementations, not experimental assay validation or a
claim of genome-wide specificity.
