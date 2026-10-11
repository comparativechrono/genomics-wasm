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
- seqtk: Heng Li and contributors; MIT. Source: https://github.com/lh3/seqtk
- AdapterRemoval: Mikkel Schubert and contributors; GPL-3.0-or-later.
  Source: https://github.com/MikkelSchubert/adapterremoval
  Its package includes the pinned zlib and bzip2 sources and notices.
- HISAT2: Daehwan Kim and contributors, including code from Bowtie 2;
  GPL-3.0-or-later. Source: https://github.com/DaehwanKimLab/hisat2
- minimap2: Heng Li and contributors; MIT. Source: https://github.com/lh3/minimap2
- kallisto: Nicolas Bray, Harold Pimentel, Páll Melsted, Lior Pachter and
  contributors; BSD-2-Clause. Source: https://github.com/pachterlab/kallisto
  Its package includes Bifrost and its bundled components, pinned zlib, their
  corresponding sources and individual licence notices.
- Salmon 2: COMBINE lab and contributors; BSD-3-Clause.
  Source: https://github.com/COMBINE-lab/salmon
  The Rust dependencies, Rust standard library and bundled C/C++ components
  retain their own licences; see the source and notices included in its package.
- featureCounts: Wei Shi, Yang Liao and Subread contributors; GPL-3.0.
  Source: https://subread.sourceforge.net/
  Its package includes Subread sources, zlib and the Emscripten mailbox shim notices.
- StringTie: Mihaela Pertea, Geo Pertea and contributors; MIT.
  Source: https://github.com/gpertea/stringtie
  Its offline source distribution includes HTSlib, gclib and compression libraries;
  these retain their respective licences and notices.
- mosdepth: Brent Pedersen and contributors; MIT.
  Source: https://github.com/brentp/mosdepth
  Its package includes the pinned Nim and HTSlib dependencies and their notices.
- DESeq2: Michael Love, Simon Anders, Wolfgang Huber and contributors;
  LGPL-3.0-or-later. Source: https://github.com/thelovelab/DESeq2
  Its separate package uses webR and R, together with the Bioconductor and CRAN
  dependencies recorded in `tools/deseq2/sources.lock.json`. These retain their
  individual licences. `locfit` is GPL-2.0-or-later; the build changes its compiler
  configuration without changing the statistical implementation.
- webR: webR authors; distribution binaries are GPL-3.0. Build scripts and
  additional application software are MIT unless stated otherwise upstream.
  R and bundled libraries retain their respective licences.
  Source: https://github.com/r-wasm/webr
- R: R Core Team and contributors; GPL-2.0-or-later.
  Source: https://www.r-project.org/
- zlib: Jean-loup Gailly and Mark Adler; zlib licence.
- FreeBayes additionally includes pinned HTSlib, SeqLib, vcflib, SIMDe and their
  component libraries. See `tools/freebayes/` and the collected notices in the
  build output for individual terms.

Each binary package includes the relevant notices, build recipes, portability
changes and the corresponding upstream sources. The repository-level MIT
licence applies only to original infrastructure, runtime and validation code.
