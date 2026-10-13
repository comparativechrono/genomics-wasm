# Data reuse and attribution

The authentic file is the 1000 Genomes Phase 3 chr21 subset already distributed
in the PLINK2 upstream test suite. It retains public consortium sample IDs;
no private cohort data or additional personal information is included.

Primary data-use statement consulted 13 October 2026:
https://www.internationalgenome.org/IGSR_disclaimer/

IGSR states that 1000 Genomes Project data is available without embargo after
the project's final publication and asks users to cite the data normally.
The same page explains that other IGSR collections can have different reuse
statements. This record applies to the original 1000 Genomes Phase 3 data,
not to arbitrary newer IGSR or HGSVC collections. We do not assign a CC0 or
other invented license to the genotype records.

Data citation:
The 1000 Genomes Project Consortium. A global reference for human genetic
variation. Nature 526, 68–74 (2015).
https://doi.org/10.1038/nature15393

The exact upstream distribution and source recipe are pinned here:
https://github.com/chrchang/plink-ng/tree/72119068234ce0acf7189e53dfe67a527bc53e2c/2.0/Tests/TEST_PHASED_VCF

The upstream regression scripts and their synthetic data are distributed as
part of PLINK2. The GPLv3 license is retained in `PLINK2-COPYING`; derived
regression-generation code remains GPL-3.0-or-later. Synthetic sample names,
phenotypes, covariates and coefficients are test inputs and carry no
clinical, population or experimental interpretation.
