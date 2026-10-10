# Upstream software

The WebAssembly packages contain upstream software, not reimplementations of
the algorithms. Exact revisions and checksums are recorded in each tool's
directory under `tools/`.

- BWA: Heng Li and contributors; GPL-3.0. Source: https://github.com/lh3/bwa
- BWA-MEM2: Vasimuddin Md, Sanchit Misra and contributors, building on Heng Li's
  BWA; MIT. Source: https://github.com/bwa-mem2/bwa-mem2
  Its package also includes the pinned safestringlib and zlib sources and notices.
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
