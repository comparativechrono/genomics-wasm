#!/usr/bin/env python3
"""Verify real quantification fixtures, or recreate their transcript references.

Verification is offline and uses only the Python standard library. Regeneration
downloads checksum-pinned reference sequences and annotations (about 40 MB),
then copies the identical, already verified reads from the RNA alignment fixtures.
To recreate those reads from their original sources first, run
scripts/fetch-rna-test-data.py --regenerate (pysam==0.23.3, about 389 MB).
"""

import argparse
import collections
import gzip
import hashlib
import json
import re
import shutil
import tempfile
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "datasets" / "rna-quant"
COMPLEMENT = str.maketrans("ACGTN", "TGCAN")


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def matches(path, expected):
    return (path.is_file() and path.stat().st_size == expected["bytes"]
            and digest(path) == expected["sha256"])


def verify(directory, manifest, generated_only=False):
    count = 0
    for name, expected in manifest["files"].items():
        if generated_only and not expected["generated"]:
            continue
        if not matches(directory / name, expected):
            raise RuntimeError("Quantification fixture checksum or size mismatch: " + name)
        count += 1
    print(f"Verified {count} quantification fixture files", flush=True)


def download(source, path):
    if matches(path, source):
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    print("Downloading " + source["url"], flush=True)
    request = urllib.request.Request(source["url"], headers={"User-Agent": "genomics-wasm-fixtures/1"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response, partial.open("wb") as stream:
            remaining = source["bytes"]
            while remaining:
                block = response.read(min(1024 * 1024, remaining))
                if not block:
                    break
                stream.write(block)
                remaining -= len(block)
            if remaining or response.read(1):
                raise RuntimeError("Source size differs from the pinned byte count")
        if not matches(partial, source):
            raise RuntimeError("Source checksum mismatch: " + source["url"])
        partial.replace(path)
    finally:
        partial.unlink(missing_ok=True)
    return path


def read_chromosome(path, chromosome):
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="ascii") as stream:
        if stream.readline().strip().split()[0] != ">" + chromosome:
            raise RuntimeError("Unexpected chromosome FASTA header")
        sequence = "".join(line.strip() for line in stream).upper()
    if set(sequence) - set("ACGTN"):
        raise RuntimeError("Unexpected reference sequence alphabet")
    return sequence


def write_transcripts(directory, records, chromosome):
    """Extract annotated exons without adding poly(A), padding or artificial bases."""
    directory.mkdir(parents=True, exist_ok=True)
    fasta, mapping, details = [], ["transcript_id\tgene_id\n"], [
        "transcript_id\tgene_id\tgene_name\tlength\tstrand\texon_count\n"]
    for transcript, gene, name, strand, exons in sorted(records):
        exons = sorted(exons)
        if (not exons or any(start < 0 or start >= end or end > len(chromosome)
                             for start, end in exons)
                or any(left[1] > right[0] for left, right in zip(exons, exons[1:]))):
            raise RuntimeError("Invalid or overlapping transcript exons: " + transcript)
        sequence = "".join(chromosome[start:end] for start, end in exons)
        if strand == "-":
            sequence = sequence.translate(COMPLEMENT)[::-1]
        elif strand != "+":
            raise RuntimeError("Unknown transcript strand: " + transcript)
        fasta.append(">" + transcript + "\n" + "\n".join(
            sequence[i:i + 60] for i in range(0, len(sequence), 60)) + "\n")
        mapping.append(f"{transcript}\t{gene}\n")
        details.append(f"{transcript}\t{gene}\t{name}\t{len(sequence)}\t{strand}\t{len(exons)}\n")
    (directory / "transcripts.fa").write_text("".join(fasta), encoding="ascii")
    (directory / "tx2gene.tsv").write_text("".join(mapping), encoding="ascii")
    (directory / "transcripts.tsv").write_text("".join(details), encoding="ascii")


def human(output, sources, manifest):
    region = manifest["datasets"]["human"]["selection_window"]
    with gzip.open(sources["hg19-gencode-basic-v19.txt.gz"], "rt", encoding="ascii") as stream:
        rows = [line.rstrip("\n").split("\t") for line in stream]
    with gzip.open(sources["hg19-gencode-attrs-v19.txt.gz"], "rt", encoding="ascii") as stream:
        attributes = {fields[4]: fields for line in stream
                      if (fields := line.rstrip("\n").split("\t"))}
    genes = {attributes[row[1]][0] for row in rows
             if row[2] == region["chromosome"]
             and int(row[4]) < region["end_0based_exclusive"]
             and int(row[5]) > region["start_0based"]}
    selected = sorted((row for row in rows if row[2] == region["chromosome"]
                       and attributes[row[1]][0] in genes), key=lambda row: row[1])
    if sorted(genes) != manifest["datasets"]["human"]["gene_ids"]:
        raise RuntimeError("Human annotation gene selection changed")
    records = []
    for row in selected:
        starts = [int(value) for value in row[9].rstrip(",").split(",")]
        ends = [int(value) for value in row[10].rstrip(",").split(",")]
        if len(starts) != int(row[8]) or len(ends) != int(row[8]):
            raise RuntimeError("Human annotation exon count mismatch")
        attr = attributes[row[1]]
        records.append((row[1], attr[0], attr[1], row[3], list(zip(starts, ends))))
    chromosome = read_chromosome(sources["hg19-chr14.fa.gz"], region["chromosome"])
    write_transcripts(output / "human", records, chromosome)
    provenance = output / "provenance"
    provenance.mkdir(parents=True, exist_ok=True)
    (provenance / "human-selected.genePred.tsv").write_text(
        "".join("\t".join(row) + "\n" for row in selected), encoding="ascii")
    (provenance / "human-selected.attrs.tsv").write_text(
        "".join("\t".join(attributes[row[1]]) + "\n" for row in selected), encoding="ascii")


def yeast(output, sources):
    grouped = collections.defaultdict(list)
    selected, metadata = [], {}
    for line in sources["nfcore-genes.gtf"].read_text(encoding="ascii").splitlines():
        fields = line.split("\t")
        if len(fields) != 9 or fields[0] != "I" or fields[2] != "exon":
            continue
        attrs = dict(re.findall(r'(\w+) "([^"]+)"', fields[8]))
        transcript = attrs["transcript_id"]
        annotation = (attrs["gene_id"], attrs.get("gene_name", attrs["gene_id"]), fields[6])
        if transcript in metadata and metadata[transcript] != annotation:
            raise RuntimeError("Inconsistent yeast transcript annotation")
        metadata[transcript] = annotation
        grouped[transcript].append((int(fields[3]) - 1, int(fields[4])))
        selected.append(line + "\n")
    records = [(transcript, *metadata[transcript], exons) for transcript, exons in grouped.items()]
    chromosome = read_chromosome(sources["nfcore-genome.fa"], "I")
    write_transcripts(output / "yeast", records, chromosome)
    (output / "provenance" / "yeast-selected-exons.gtf").write_text("".join(selected), encoding="ascii")


def copy_reads(output, manifest):
    for name in manifest["datasets"]:
        for mate in (1, 2):
            relative = f"{name}/reads_{mate}.fastq"
            original = ROOT / "datasets" / f"rna-{name}" / f"reads_{mate}.fastq"
            if not matches(original, manifest["files"][relative]):
                raise RuntimeError("Recreate the matching RNA read fixture first: " + str(original))
            shutil.copyfile(original, output / relative)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--verify", action="store_true")
    action.add_argument("--regenerate", action="store_true")
    parser.add_argument("--cache", type=Path, default=ROOT / "build" / "quant-sources")
    args = parser.parse_args()
    manifest = json.loads((DATA / "manifest.json").read_text())
    if args.verify:
        verify(DATA, manifest)
        return
    downloaded = {name: download(source, args.cache / name) for name, source in manifest["sources"].items()}
    with tempfile.TemporaryDirectory(prefix="regenerate-", dir=args.cache) as temporary:
        output = Path(temporary)
        human(output, downloaded, manifest)
        yeast(output, downloaded)
        copy_reads(output, manifest)
        verify(output, manifest, generated_only=True)
        for name, expected in manifest["files"].items():
            if expected["generated"]:
                shutil.copyfile(output / name, DATA / name)
    verify(DATA, manifest)


if __name__ == "__main__":
    main()
