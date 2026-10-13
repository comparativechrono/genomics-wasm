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
- SomaticSniper: David Larson and contributors; MIT.
  Source: https://github.com/genome/somatic-sniper
  Its package includes the original SAMtools 0.1.6 sources and notices, and zlib.
- LoFreq: Andreas Wilm and contributors; MIT. Source: https://github.com/CSB5/lofreq
  Its package includes HTSlib, zlib and OpenLibm sources and their individual
  notices. The browser somatic coordinator is derived from the upstream Python
  workflow; variant calculations remain in the upstream C implementation.
- Strelka2 2.9.10: Illumina and contributors; GPL-3.0-or-later in the pinned
  source release. Source: https://github.com/Illumina/strelka/tree/v2.9.10
  Its package retains the release's bundled dependencies, scoring models,
  source archive and notices. The derived JavaScript somatic workflow retains
  the upstream GPL-3.0-or-later licence. CPython 2.7.18 is included as source for running
  the unchanged native workflow during validation, under its original notices.
  The browser workflow does not require Python.
- Kraken2: Derrick Wood and contributors; MIT.
  Source: https://github.com/DerrickWood/kraken2
  The pinned 2.17.2 release retains an upstream version banner of 2.17.1.
  Its package includes the original source, portability patches and notices.
- Bracken: Jennifer Lu and contributors; GPL-3.0-or-later.
  Source: https://github.com/jenniferlu717/Bracken
  The original Python abundance and distribution programs run in bundled
  Pyodide. The package includes CPython, Pyodide and runtime dependency sources
  and notices, plus the Kraken2 dependency used for database preparation.
- Prodigal: Doug Hyatt and contributors; GPL-3.0-or-later.
  Source: https://github.com/hyattpd/Prodigal
  The package includes the original source, training models, portability patch
  and licence. Gene prediction and training use the original C implementation.
- HMMER and Easel: Sean Eddy, the Eddy/Rivas laboratory and contributors;
  BSD-3-Clause and the third-party notices retained in their source archives.
  Source: https://hmmer.org/ and https://github.com/EddyRivasLab/hmmer
  The package retains the upstream SIMD algorithms, serial command programs,
  original sources and dependency notices.
- seqtk: Heng Li and contributors; MIT. Source: https://github.com/lh3/seqtk
- bustools: BUStools authors and contributors; BSD-2-Clause.
  Source: https://github.com/BUStools/bustools
  The package contains the original C++ source, pinned zlib and the embedded
  numerical and data-structure libraries with their individual licence notices.
  Portability changes retain the upstream BUS-processing algorithms.
- PLINK 2: Shaun Purcell, Christopher Chang and contributors;
  GPL-3.0-or-later for the application and LGPL-3.0-or-later for its reusable
  libraries. Source: https://github.com/chrchang/plink-ng
  The package retains the pinned upstream source, bundled compression-library
  notices, zlib, Emscripten notices and the portability patch. Its first browser
  build uses upstream `NOLAPACK` algorithms and does not include PCA.
- AdapterRemoval: Mikkel Schubert and contributors; GPL-3.0-or-later.
  Source: https://github.com/MikkelSchubert/adapterremoval
  Its package includes the pinned zlib and bzip2 sources and notices.
- HISAT2: Daehwan Kim and contributors, including code from Bowtie 2;
  GPL-3.0-or-later. Source: https://github.com/DaehwanKimLab/hisat2
- Cutadapt, dnaio and xopen: Marcel Martin and contributors; MIT.
  Source: https://github.com/marcelm/cutadapt
  The package includes Pyodide (MPL-2.0), CPython and their runtime libraries,
  with their corresponding sources and individual licences. See
  `tools/cutadapt/runtime-notices/` and the pinned source manifests.
- Primer3: Primer3 contributors; GPL-2.0-or-later for its core libraries,
  with GPL-3.0-or-later Amplicon3 code also linked into the upstream core.
  Source: https://github.com/primer3-org/primer3
  All seven command-line programs retain the original scientific code.
  The package includes the complete source and thermodynamic parameters,
  the component licences and Emscripten notices.
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
