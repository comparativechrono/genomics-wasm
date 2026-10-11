#!/usr/bin/env python3
"""Verify, fetch, or exactly regenerate the compact real Cutadapt fixtures.

All modes use only Python's standard library. Regeneration selects unchanged
yeast FASTQ records from two checksum-pinned upstream files, and copies the
included immutable human BAM-derived FASTQ snapshots. It needs no Cutadapt,
aligner, pysam, reference genome, or other repository dataset directory.
"""
import argparse
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "datasets" / "cutadapt"
ADAPTER1 = "AGATCGGAAGAGCACACGTCTGAACTCCAGTCA"
ADAPTER2 = "AGATCGGAAGAGCGTCGTGTAGGGAAAGAGTGT"
SEED = "AGATCGGAAGAGC"
STRATA = ("full_adapter_both", "other_adapter_seed", "low_quality_tail_no_seed", "ordinary_no_seed")
PER_STRATUM = 96


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def matches(path, metadata):
    return path.is_file() and path.stat().st_size == metadata["bytes"] and digest(path) == metadata["sha256"]


def verify(directory, manifest, generated_only=False):
    count = 0
    for name, metadata in manifest["files"].items():
        if generated_only and not metadata["generated"]:
            continue
        if not matches(directory / name, metadata):
            raise RuntimeError("Fixture checksum/size mismatch: " + name)
        count += 1
    print(f"Verified {count} real Cutadapt fixture files", flush=True)


def download(metadata, path):
    if matches(path, metadata):
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    print("Downloading " + metadata["url"], flush=True)
    request = urllib.request.Request(metadata["url"], headers={"User-Agent": "genomics-wasm-fixtures/1"})
    try:
        with urllib.request.urlopen(request, timeout=60) as source, partial.open("wb") as target:
            remaining = metadata["bytes"]
            while remaining:
                chunk = source.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                target.write(chunk)
                remaining -= len(chunk)
            if remaining or source.read(1):
                raise RuntimeError("Source size differs from pinned size")
        if not matches(partial, metadata):
            raise RuntimeError("Source checksum mismatch: " + metadata["url"])
        partial.replace(path)
    finally:
        partial.unlink(missing_ok=True)
    return path


def fastq(path):
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="ascii", newline="") as source:
        while True:
            lines = tuple(source.readline() for _ in range(4))
            if not lines[0]:
                if any(lines[1:]):
                    raise RuntimeError("Unexpected FASTQ trailing content")
                return
            if (not all(lines) or not lines[0].startswith("@") or not lines[2].startswith("+")
                    or len(lines[1].rstrip("\r\n")) != len(lines[3].rstrip("\r\n"))):
                raise RuntimeError("Malformed FASTQ: " + str(path))
            yield lines


def record_name(record):
    return record[0][1:].split()[0]


def stratum(first, second):
    s1, s2 = first[1].rstrip(), second[1].rstrip()
    q1, q2 = first[3].rstrip(), second[3].rstrip()
    if ADAPTER1 in s1 and ADAPTER2 in s2:
        return STRATA[0]
    if SEED in s1 or SEED in s2:
        return STRATA[1]
    if any(ord(q) - 33 < 20 for q in q1[-10:] + q2[-10:]):
        return STRATA[2]
    return STRATA[3]


def gzip_copy(source, target):
    # No embedded filename or clock time; fixed level and header for reproducibility.
    with target.open("wb") as stream:
        with gzip.GzipFile(filename="", mode="wb", fileobj=stream, mtime=0, compresslevel=9) as compressed:
            compressed.write(source.read_bytes())


def prepare(output, snapshots, sources):
    for species in ("human", "yeast"):
        (output / species).mkdir(parents=True)
    for mate in (1, 2):
        shutil.copyfile(snapshots / "human" / f"reads_{mate}.fastq", output / "human" / f"reads_{mate}.fastq")
    chosen, counts, population = [], Counter(), Counter()
    first = sources["nfcore-SRR6357070_1.fastq.gz"]
    second = sources["nfcore-SRR6357070_2.fastq.gz"]
    for r1, r2 in zip(fastq(first), fastq(second), strict=True):
        if record_name(r1) != record_name(r2):
            raise RuntimeError("Upstream mate names/order disagree")
        group = stratum(r1, r2)
        population[group] += 1
        if counts[group] < PER_STRATUM:
            chosen.append((r1, r2, group))
            counts[group] += 1
    if any(counts[group] != PER_STRATUM for group in STRATA):
        raise RuntimeError("Insufficient source pairs in a pinned selection stratum")
    for mate in (1, 2):
        (output / "yeast" / f"reads_{mate}.fastq").write_text(
            "".join("".join(pair[mate - 1]) for pair in chosen), encoding="ascii", newline="")
    rows = ["read_name\tselection_stratum\tr1_adapter_seed_0based\tr2_adapter_seed_0based\tminimum_tail_phred\n"]
    for r1, r2, group in chosen:
        rows.append(f"{record_name(r1)}\t{group}\t{r1[1].find(SEED)}\t{r2[1].find(SEED)}\t"
                    f"{min(ord(q) - 33 for q in r1[3].rstrip()[-10:] + r2[3].rstrip()[-10:])}\n")
    (output / "yeast" / "selection.tsv").write_text("".join(rows))
    (output / "yeast" / "selection-population.json").write_text(json.dumps({
        "source_pairs": sum(population.values()), "population": dict(sorted(population.items())),
        "selected": dict(sorted(counts.items())),
        "rule": "First 96 source-order pairs in each mutually exclusive stratum; output retains source order."
    }, indent=2) + "\n")
    for species in ("human", "yeast"):
        for mate in (1, 2):
            source = output / species / f"reads_{mate}.fastq"
            gzip_copy(source, source.with_suffix(".fastq.gz"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--verify", action="store_true", help="Offline size/SHA256 verification of every fixture file")
    modes.add_argument("--fetch-sources", action="store_true", help="Fetch/verify both raw public source archives, without changing fixtures")
    modes.add_argument("--regenerate", action="store_true", help="Regenerate in a temporary directory; replace only if every expected hash matches")
    parser.add_argument("--cache", type=Path, default=ROOT / "build" / "cutadapt-sources")
    args = parser.parse_args()
    manifest = json.loads((DATA / "manifest.json").read_text())
    if args.verify:
        verify(DATA, manifest)
        return
    sources = {name: download(metadata, args.cache / name) for name, metadata in manifest["sources"].items()}
    if args.fetch_sources:
        print(f"Verified {len(sources)} pinned Cutadapt fixture source archives", flush=True)
        return
    # Preserve trust in included human source snapshots before using them.
    for name, metadata in manifest["files"].items():
        if not metadata["generated"] and not matches(DATA / name, metadata):
            raise RuntimeError("Immutable provenance checksum mismatch: " + name)
    with tempfile.TemporaryDirectory(prefix="cutadapt-fixtures-") as temporary:
        output = Path(temporary)
        prepare(output, DATA / "provenance" / "inputs", sources)
        verify(output, manifest, generated_only=True)
        for name, metadata in manifest["files"].items():
            if metadata["generated"]:
                shutil.copyfile(output / name, DATA / name)
    verify(DATA, manifest)


if __name__ == "__main__":
    main()
