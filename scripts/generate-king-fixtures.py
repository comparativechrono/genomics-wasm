#!/usr/bin/env python3
"""Generate entirely synthetic, deterministic KING validation data (no human data)."""
from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "datasets/king"
SEED = 2322026
MARKERS = 32809  # Remainder 41 exercises the upper half of the final 64-bit word.


class RNG:
    """Explicit xorshift32 avoids depending on a library PRNG version."""
    def __init__(self, seed):
        self.state = seed

    def next(self):
        x = self.state
        x ^= (x << 13) & 0xffffffff
        x ^= x >> 17
        x ^= (x << 5) & 0xffffffff
        self.state = x & 0xffffffff
        return self.state


def generate():
    OUT.mkdir(parents=True, exist_ok=True)
    rng = RNG(SEED)
    samples = [
        ["FAM", "DAD", "0", "0", "1", "1"],
        ["FAM", "MOM", "0", "0", "2", "1"],
        ["FAM", "CHILD1", "DAD", "MOM", "1", "2"],
        ["FAM", "CHILD2", "DAD", "MOM", "2", "2"],
        ["COPY", "DUP", "0", "0", "1", "2"],
    ] + [[f"U{i:02}", f"U{i:02}", "0", "0", str(1 + i % 2), "1"] for i in range(1, 101)]
    genotypes = [[] for _ in samples]
    bim = []
    bed = bytearray(b"\x6c\x1b\x01")
    for marker in range(MARKERS):
        chromosome = min(marker // 8192 + 1, 4)
        local = marker - (chromosome - 1) * 8192
        position = 10000 * (local + 1)
        # Founder allele frequencies vary between 0.15 and 0.45. Random
        # independent haplotypes supply no borrowed or personal genotypes.
        cutoff = (15 + rng.next() % 31) * (1 << 32) // 100
        founder = [[int(rng.next() < cutoff), int(rng.next() < cutoff)] for _ in range(102)]
        block = marker // 1024
        father_haplotype, mother_haplotype = block % 2, (block // 2) % 2
        child1 = founder[0][father_haplotype] + founder[1][mother_haplotype]
        # Four transmission states give full siblings 1/4, 1/2, 1/4 IBD2/1/0
        # over 10 Mb blocks while preserving Mendelian inheritance exactly.
        child2 = (founder[0][father_haplotype ^ int(block % 4 not in (0, 1))]
                  + founder[1][mother_haplotype ^ int(block % 4 not in (0, 2))])
        row = [sum(founder[0]), sum(founder[1]), child1, child2, child1]
        row += [sum(alleles) for alleles in founder[2:]]
        for missing_sample in (24, len(samples) - 1):
            if rng.next() % 5 == 0:
                row[missing_sample] = -1
        for sample, genotype in enumerate(row):
            genotypes[sample].append(genotype)
        # SNP-major PLINK: 00 homozygous A1, 10 heterozygous, 11 A2, 01 missing.
        # Genotypes count the minor A1 allele: KING's segment analysis requires
        # A1 to be the minor allele, matching PLINK's conventional output.
        for start in range(0, len(samples), 4):
            byte = 0
            for offset, genotype in enumerate(row[start:start + 4]):
                byte |= {-1: 1, 0: 3, 1: 2, 2: 0}[genotype] << (offset * 2)
            bed.append(byte)
        bim.append(f"{chromosome}\tsyn{marker + 1:05}\t0\t{position}\tA\tG\n")

    (OUT / "cohort.bed").write_bytes(bed)
    (OUT / "cohort.bim").write_text("".join(bim))
    (OUT / "cohort.fam").write_text("".join(" ".join(row) + "\n" for row in samples))
    (OUT / "cohort.fam.gz").write_bytes(gzip.compress((OUT / "cohort.fam").read_bytes(), mtime=0))

    sample_stats = {}
    for sample, row in zip(samples, genotypes):
        count = sum(g >= 0 for g in row)
        sample_stats[sample[1]] = {
            "FID": sample[0], "called": count, "missing": MARKERS - count,
            "heterozygous": row.count(1),
        }
    # Count genotype categories independently from KING's packed representation.
    # Python's arbitrary-width integers make this truth calculation fast even
    # for the >100-sample cohort required to exercise upstream screening.
    masks = []
    for row in genotypes:
        encoded = [bytearray((MARKERS + 7) // 8) for _ in range(3)]
        for marker, genotype in enumerate(row):
            if genotype >= 0:
                encoded[genotype][marker // 8] |= 1 << (marker % 8)
        masks.append(tuple(int.from_bytes(mask, 'little') for mask in encoded))
    pair_stats = {}
    for i, first in enumerate(samples):
        for j in range(i + 1, len(samples)):
            second = samples[j]
            a0, a1, a2 = masks[i]
            b0, b1, b2 = masks[j]
            count = ((a0 | a1 | a2) & (b0 | b1 | b2)).bit_count()
            both_het = (a1 & b1).bit_count()
            het1 = (a1 & (b0 | b1 | b2)).bit_count()
            het2 = (b1 & (a0 | a1 | a2)).bit_count()
            ibs0 = ((a0 & b2) | (a2 & b0)).bit_count()
            ibs1 = ((a1 & (b0 | b2)) | (b1 & (a0 | a2))).bit_count()
            within = first[0] == second[0]
            # KING-robust estimators expressed directly in observed genotypes.
            kinship = ((both_het - 2 * ibs0) / (het1 + het2) if within else
                       0.5 - (ibs1 + 4 * ibs0) / (4 * min(het1, het2)))
            pair_stats["|".join(sorted((first[1], second[1])))] = {
                "IDs": [first[1], second[1]],
                "withinFamily": within, "N_SNP": count, "N_IBS0": ibs0,
                "N_IBS1": ibs1, "N_IBS2": count - ibs0 - ibs1,
                "NHetHet": both_het, "N_Het1": het1, "N_Het2": het2,
                "HetHet": both_het / count, "IBS0": ibs0 / count,
                "Kinship": kinship,
                "KinshipWithin": (both_het - 2 * ibs0) / (het1 + het2),
            }
    truth = {
        "schema": 1, "origin": "Entirely synthetic; no human or public reference genotypes",
        "seed": SEED, "generator": "xorshift32", "sampleCount": len(samples),
        "markerCount": MARKERS, "chromosomes": 4, "sampleStats": sample_stats,
        "pairStats": pair_stats,
        "relationships": {"duplicate": ["CHILD1", "DUP"],
                          "parents": [["DAD", "CHILD1"], ["MOM", "CHILD1"], ["DAD", "CHILD2"], ["MOM", "CHILD2"]],
                          "siblings": ["CHILD1", "CHILD2"], "missingSamples": ["U20", "U100"]},
    }
    (OUT / "truth.json").write_text(json.dumps(truth, indent=2, sort_keys=True) + "\n")
    # Preserve the same first 25 samples as a separate small-cohort regression.
    # KING's upstream default relatedness screening skips cohorts below 100;
    # the small dataset is explicitly exercised with --related --noscreen.
    small = OUT / 'small'
    small.mkdir(exist_ok=True)
    small_samples = samples[:25]
    small_ids = {sample[1] for sample in small_samples}
    small_bed = bytearray(b"\x6c\x1b\x01")
    stride = (len(samples) + 3) // 4
    for marker in range(MARKERS):
        start = 3 + marker * stride
        small_bed.extend(bed[start:start + 7])
        small_bed[-1] &= 3  # Ignore padding beyond the 25th sample.
    (small / 'cohort.bed').write_bytes(small_bed)
    (small / 'cohort.bim').write_bytes((OUT / 'cohort.bim').read_bytes())
    (small / 'cohort.fam').write_text(''.join(' '.join(row) + '\n' for row in small_samples))
    small_truth = {**truth, 'sampleCount': len(small_samples),
                   'sampleStats': {key: value for key, value in sample_stats.items() if key in small_ids},
                   'pairStats': {key: value for key, value in pair_stats.items() if set(value['IDs']) <= small_ids},
                   'relationships': {**truth['relationships'], 'missingSamples': ['U20']}}
    (small / 'truth.json').write_text(json.dumps(small_truth, indent=2, sort_keys=True) + '\n')
    small_files = {name: {'sha256': hashlib.sha256((small / name).read_bytes()).hexdigest(), 'bytes': (small / name).stat().st_size}
                   for name in ('cohort.bed', 'cohort.bim', 'cohort.fam', 'truth.json')}
    (small / 'manifest.json').write_text(json.dumps({'schema': 1, 'version': '2.3.2', 'dataset': 'king-synthetic-small-v1',
        'sampleCount': 25, 'markerCount': MARKERS, 'inputFiles': ['cohort.bed', 'cohort.bim', 'cohort.fam'],
        'files': small_files, 'provenance': 'First 25 synthetic samples from the main cohort'}, indent=2, sort_keys=True) + '\n')
    names = ["cohort.bed", "cohort.bim", "cohort.fam", "cohort.fam.gz", "truth.json",
             "small/cohort.bed", "small/cohort.bim", "small/cohort.fam", "small/truth.json", "small/manifest.json"]
    files = {name: {"sha256": hashlib.sha256((OUT / name).read_bytes()).hexdigest(), "bytes": (OUT / name).stat().st_size} for name in names}
    manifest = {"schema": 1, "dataset": "king-synthetic-v1", "version": "2.3.2",
                "seed": SEED, "sampleCount": len(samples), "markerCount": MARKERS,
                "inputFiles": ["cohort.bed", "cohort.bim", "cohort.fam"], "files": files,
                "provenance": "Generated entirely by scripts/generate-king-fixtures.py; no donor data or upstream fixture redistribution."}
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(f"Generated {len(samples)} synthetic samples at {MARKERS} markers in {OUT}")


if __name__ == "__main__":
    generate()
