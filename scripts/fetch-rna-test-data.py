#!/usr/bin/env python3
"""Verify RNA fixtures offline, or recreate them from checksum-pinned real data.

Regeneration needs pysam==0.23.3. Normal validation needs only Python's standard
library and uses the committed fixtures without any network access.
"""

import argparse
import collections
import gzip
import hashlib
import importlib
import json
import re
import shutil
import tarfile
import tempfile
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "datasets"


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def matches(path, expected):
    return (path.is_file() and path.stat().st_size == expected["bytes"]
            and digest(path) == expected["sha256"])


def verify(directory, manifest, generated_only=False):
    checked = 0
    for name, expected in manifest["files"].items():
        if generated_only and not expected["generated"]:
            continue
        if not matches(directory / name, expected):
            raise RuntimeError("RNA fixture checksum or size mismatch: " + name)
        checked += 1
    print(f"Verified {checked} RNA fixture files against datasets/rna-manifest.json")


def download(source, path):
    if matches(path, source):
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".partial")
    print("Downloading " + source["url"], flush=True)
    request = urllib.request.Request(source["url"], headers={"User-Agent": "genomics-wasm-fixtures/1"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response, temporary.open("wb") as stream:
            remaining = source["bytes"]
            while remaining:
                block = response.read(min(1024 * 1024, remaining))
                if not block:
                    break
                stream.write(block)
                remaining -= len(block)
            if remaining or response.read(1):
                raise RuntimeError("Source size differs from pinned byte count")
        if not matches(temporary, source):
            raise RuntimeError("Source checksum mismatch: " + source["url"])
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def write_sites(directory, sites, label):
    sites = sorted(sites)
    (directory / "splice-sites.tsv").write_text(
        "".join("\t".join(map(str, site)) + "\n" for site in sites), encoding="ascii")
    # Two 20 bp blocks surrounding each intron. These BED records encode junction
    # evidence; they do not assert a complete transcript or full exon boundaries.
    (directory / "junctions.bed").write_text("".join(
        f"{chrom}\t{left - 19}\t{right + 20}\t{label}_{i + 1}\t0\t{strand}\t"
        f"{left - 19}\t{right + 20}\t0\t2\t20,20\t0,{right - (left - 19)}\n"
        for i, (chrom, left, right, strand) in enumerate(sites)), encoding="ascii")


def human(output, downloaded, work, manifest, pysam):
    output.mkdir(parents=True)
    metadata = manifest["datasets"]["rna-human"]
    member = metadata["source_bam_member"]
    bam_path = work / "ERR127306_chr14.bam"
    with tarfile.open(downloaded["human.tar.gz"], "r:gz") as archive:
        with archive.extractfile(member) as source, bam_path.open("wb") as target:
            shutil.copyfileobj(source, target)
    if not matches(bam_path, metadata["source_bam"]):
        raise RuntimeError("Human BAM member checksum mismatch")
    reference = metadata["reference"]
    start, end = reference["start_0based"], reference["end_0based_exclusive"]
    with gzip.open(downloaded["hg19-chr14.fa.gz"], "rt", encoding="ascii") as stream:
        if stream.readline().strip() != ">chr14":
            raise RuntimeError("Unexpected human chromosome FASTA header")
        sequence = "".join(line.strip() for line in stream)[start:end].upper()
    if len(sequence) != end - start:
        raise RuntimeError("Human reference window is incomplete")
    contig = reference["fixture_contig"]
    (output / "reference.fa").write_text(
        f">{contig}\n" + "\n".join(sequence[i:i + 60] for i in range(0, len(sequence), 60)) + "\n",
        encoding="ascii")
    pairs = {}
    with pysam.AlignmentFile(str(bam_path), "rb") as bam:
        for read in bam:
            if (read.is_unmapped or read.is_secondary or read.is_supplementary or not read.is_paired
                    or read.reference_name != "chr14" or read.is_read1 == read.is_read2
                    or read.reference_start < start or read.reference_end > end):
                continue
            pair = pairs.setdefault(read.query_name, {})
            mate = 1 if read.is_read1 else 2
            if mate in pair:
                raise RuntimeError("Multiple primary records for a human mate")
            pair[mate] = read
    names = sorted(name for name, pair in pairs.items() if len(pair) == 2)[:256]
    if len(names) != 256:
        raise RuntimeError("Expected 256 complete human read pairs")
    for mate in (1, 2):
        with (output / f"reads_{mate}.fastq").open("w", encoding="ascii", newline="\n") as stream:
            for name in names:
                read = pairs[name][mate]
                qualities = "".join(chr(q + 33) for q in read.get_forward_qualities())
                stream.write(f"@{name}\n{read.get_forward_sequence()}\n+\n{qualities}\n")
    sites = set()
    for name in names:
        for read in pairs[name].values():
            position = read.reference_start - start
            for operation, length in read.cigartuples:
                if operation == 3:
                    sites.add((contig, position - 1, position + length, read.get_tag("XS")))
                if operation in (0, 2, 3, 7, 8):
                    position += length
    write_sites(output, sites, "observed")


def yeast(output, downloaded, pysam):
    output.mkdir(parents=True)
    transcripts = collections.defaultdict(list)
    for line in downloaded["nfcore-genes.gtf"].read_text(encoding="ascii").splitlines():
        fields = line.split("\t")
        if len(fields) == 9 and fields[0] == "I" and fields[2] == "exon":
            transcript = re.search(r'transcript_id "([^"]+)"', fields[8]).group(1)
            transcripts[(transcript, fields[6])].append((int(fields[3]) - 1, int(fields[4])))
    sites = set()
    for (_, strand), exons in transcripts.items():
        exons.sort()
        for left, right in zip(exons, exons[1:]):
            sites.add(("I", left[1] - 1, right[0], strand))
    pairs = {}
    with pysam.AlignmentFile(str(downloaded["nfcore-WT_REP1.sorted.bam"]), "rb") as bam:
        for read in bam:
            if (read.is_unmapped or read.is_secondary or read.is_supplementary or not read.is_paired
                    or not read.query_name.startswith("SRR6357070.") or read.reference_name != "I"
                    or read.get_tag("NH") != 1 or read.is_read1 == read.is_read2):
                continue
            pair = pairs.setdefault(read.query_name, {})
            mate = 1 if read.is_read1 else 2
            if mate in pair:
                raise RuntimeError("Multiple primary records for a yeast mate")
            pair[mate] = read
    spliced, other = [], []
    for name, pair in pairs.items():
        if len(pair) != 2:
            continue
        matching_junction = False
        for read in pair.values():
            position = read.reference_start
            for operation, length in read.cigartuples:
                if operation == 3 and any(("I", position - 1, position + length, strand) in sites
                                          for strand in ("+", "-")):
                    matching_junction = True
                if operation in (0, 2, 3, 7, 8):
                    position += length
        (spliced if matching_junction else other).append(name)
    if len(spliced) < 128 or len(other) < 128:
        raise RuntimeError("Insufficient yeast pairs for the recorded selection")
    selected = set(sorted(spliced)[:128] + sorted(other)[:128])
    shutil.copyfile(downloaded["nfcore-genome.fa"], output / "reference.fa")
    orders = []
    for mate in (1, 2):
        order = []
        with gzip.open(downloaded[f"nfcore-SRR6357070_{mate}.fastq.gz"], "rt", encoding="ascii", newline="") as source:
            with (output / f"reads_{mate}.fastq").open("w", encoding="ascii", newline="") as target:
                while True:
                    record = [source.readline() for _ in range(4)]
                    if not record[0]:
                        break
                    if (not all(record) or not record[0].startswith("@") or not record[2].startswith("+")
                            or len(record[1].rstrip()) != len(record[3].rstrip())):
                        raise RuntimeError("Malformed source FASTQ")
                    name = record[0][1:].split()[0]
                    if name in selected:
                        target.writelines(record)
                        order.append(name)
        if len(order) != 256 or len(set(order)) != 256:
            raise RuntimeError("Expected 256 distinct selected yeast FASTQ pairs")
        orders.append(order)
    if orders[0] != orders[1]:
        raise RuntimeError("Yeast FASTQ mates are not in matching order")
    write_sites(output, sites, "annotated")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--verify", action="store_true")
    action.add_argument("--regenerate", action="store_true")
    parser.add_argument("--cache", type=Path, default=ROOT / "build" / "rna-sources")
    args = parser.parse_args()
    manifest = json.loads((DATA / "rna-manifest.json").read_text())
    if args.verify:
        verify(DATA, manifest)
        return
    pysam = importlib.import_module("pysam")
    if pysam.__version__ != "0.23.3":
        raise RuntimeError("Regeneration requires pysam==0.23.3")
    downloaded = {name: download(source, args.cache / name) for name, source in manifest["sources"].items()}
    args.cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="regenerate-", dir=args.cache) as temporary:
        work = Path(temporary)
        output = work / "datasets"
        human(output / "rna-human", downloaded, work, manifest, pysam)
        yeast(output / "rna-yeast", downloaded, pysam)
        verify(output, manifest, generated_only=True)
        # Only replace committed files after every reconstructed byte is verified.
        # Public provenance snapshots and upstream notices are retained unchanged.
        for name, expected in manifest["files"].items():
            if expected["generated"]:
                shutil.copyfile(output / name, DATA / name)
    verify(DATA, manifest)


if __name__ == "__main__":
    main()
