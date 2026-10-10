# Upstream software

The WebAssembly packages contain upstream software, not reimplementations of
the algorithms. Exact revisions and checksums are recorded in `tools/bwa/` and
`tools/freebayes/`.

- BWA: Heng Li and contributors; GPL-3.0. Source: https://github.com/lh3/bwa
- FreeBayes: Erik Garrison and contributors; upstream MIT. Its linked
  dependencies have additional terms, including GPL-2.0-or-later code in the
  vcflib Smith-Waterman library. Source: https://github.com/freebayes/freebayes
- zlib: Jean-loup Gailly and Mark Adler; zlib licence.
- FreeBayes additionally includes pinned HTSlib, SeqLib, vcflib, SIMDe and their
  component libraries. See `tools/freebayes/` and the collected notices in the
  build output for individual terms.

Each binary package includes the relevant notices, build recipes, portability
changes and the corresponding upstream sources. The repository-level MIT
licence applies only to original infrastructure, runtime and validation code.
