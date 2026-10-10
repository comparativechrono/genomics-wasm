#!/usr/bin/env python3
"""Verify real DESeq2 fixtures offline, or recreate them from pinned airway data.

Normal verification uses only Python's standard library. Regeneration requires
native R, but no add-on R packages: the script reads the existing object's slots.
"""

import argparse
import csv
import hashlib
import json
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "datasets" / "deseq2-manifest.json"


EXTRACT_R = r'''
args <- commandArgs(trailingOnly=TRUE)
load(args[1])
# Reading stored slots avoids any dependency on the airway or
# SummarizedExperiment packages and preserves the original assay exactly.
countData <- methods::slot(methods::slot(methods::slot(airway, "assays"),
                                       "data"), "listData")[["counts"]]
geneIds <- methods::slot(methods::slot(methods::slot(airway, "rowRanges"),
                                     "partitioning"), "NAMES")
sampleIds <- methods::slot(methods::slot(airway, "colData"), "rownames")
sampleData <- methods::slot(methods::slot(airway, "colData"), "listData")
stopifnot(is.integer(countData), identical(dim(countData), c(63677L, 8L)),
          length(geneIds) == nrow(countData), all(countData >= 0L),
          !anyNA(countData), !anyDuplicated(geneIds),
          identical(as.character(sampleData$Run), sampleIds))
counts <- data.frame(gene_id=geneIds, countData, check.names=FALSE)
names(counts) <- c("gene_id", sampleIds)
samples <- data.frame(sample_id=sampleIds, sampleData, check.names=FALSE)
write.table(counts, file=file.path(args[2], "counts.tsv"), sep="\t",
            quote=FALSE, row.names=FALSE, col.names=TRUE, eol="\n")
write.table(samples, file=file.path(args[2], "samples.tsv"), sep="\t",
            quote=FALSE, row.names=FALSE, col.names=TRUE, eol="\n")
'''


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def matches(path, expected):
    return (path.is_file() and path.stat().st_size == expected["bytes"]
            and digest(path) == expected["sha256"])


def verify(directory, manifest):
    for name, expected in manifest["files"].items():
        if not matches(directory / name, expected):
            raise RuntimeError("DESeq2 fixture checksum or size mismatch: " + name)
    data = manifest["datasets"]["deseq2-airway"]
    with (directory / "deseq2-airway" / "samples.tsv").open(newline="") as stream:
        samples = list(csv.DictReader(stream, delimiter="\t"))
    with (directory / "deseq2-airway" / "counts.tsv").open(newline="") as stream:
        counts = csv.reader(stream, delimiter="\t")
        header = next(counts)
        if header != ["gene_id", *data["sample_ids"]]:
            raise RuntimeError("Unexpected count matrix column order")
        if [row["sample_id"] for row in samples] != header[1:]:
            raise RuntimeError("Sample metadata and count matrix are not aligned")
        genes = set()
        for row in counts:
            if len(row) != data["samples"] + 1 or row[0] in genes:
                raise RuntimeError("Malformed or duplicate count matrix row")
            genes.add(row[0])
            if any(not value.isdigit() for value in row[1:]):
                raise RuntimeError("Counts must be non-negative integers")
        if len(genes) != data["genes"]:
            raise RuntimeError("Unexpected gene count")
    if len(samples) != data["samples"]:
        raise RuntimeError("Unexpected sample count")
    pairs = {}
    for row in samples:
        pairs.setdefault(row["cell"], []).append(row["dex"])
    if len(pairs) != 4 or any(sorted(values) != ["trt", "untrt"]
                              for values in pairs.values()):
        raise RuntimeError("Expected four paired untreated/treated cell lines")
    print(f"Verified {len(manifest['files'])} DESeq2 fixture files: "
          f"{len(genes):,} genes and {len(samples)} real samples")


def download(source, path):
    if matches(path, source):
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".partial")
    print("Downloading " + source["url"], flush=True)
    request = urllib.request.Request(source["url"],
                                     headers={"User-Agent": "genomics-wasm-fixtures/1"})
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


def regenerate(directory, manifest, cache, r_command):
    cache.mkdir(parents=True, exist_ok=True)
    archive_name = "airway_1.26.0.tar.gz"
    archive_path = download(manifest["sources"][archive_name], cache / archive_name)
    with tempfile.TemporaryDirectory(prefix="extract-", dir=cache) as temporary:
        work = Path(temporary)
        generated = work / "deseq2-airway"
        generated.mkdir()
        with tarfile.open(archive_path, "r:gz") as archive:
            members = {
                "airway/data/airway.RData": work / "airway.RData",
                "airway/DESCRIPTION": generated / "UPSTREAM_DESCRIPTION.txt",
                "airway/inst/CITATION": generated / "UPSTREAM_CITATION.R",
            }
            for member, destination in members.items():
                with archive.extractfile(member) as source, destination.open("wb") as target:
                    shutil.copyfileobj(source, target)
        if not matches(work / "airway.RData",
                       manifest["datasets"]["deseq2-airway"]["source_rdata"]):
            raise RuntimeError("Source RData member checksum mismatch")
        script = work / "extract.R"
        script.write_text(EXTRACT_R, encoding="utf-8")
        subprocess.run([r_command, "--vanilla", "--slave", "-f", str(script),
                        "--args", str(work / "airway.RData"), str(generated)], check=True)
        verify(work, manifest)
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / "deseq2-airway"
        destination.mkdir(parents=True, exist_ok=True)
        for name in manifest["files"]:
            shutil.copyfile(work / name, directory / name)
    verify(directory, manifest)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--verify", action="store_true",
                      help="Verify the committed fixtures offline (the default)")
    mode.add_argument("--regenerate", action="store_true",
                      help="Recreate from the checksum-pinned upstream RData using native R")
    parser.add_argument("--output", type=Path, default=ROOT / "datasets")
    parser.add_argument("--cache", type=Path, default=ROOT / "build" / "deseq2-data")
    parser.add_argument("--r", default="R", help="Native R executable used for regeneration")
    args = parser.parse_args()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if args.regenerate:
        regenerate(args.output.resolve(), manifest, args.cache.resolve(), args.r)
    else:
        verify(args.output, manifest)


if __name__ == "__main__":
    main()
