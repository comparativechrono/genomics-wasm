#!/usr/bin/env python3
"""Verify committed fixtures, or reconstruct them from checksum-pinned sources.

Verification uses only the Python standard library and never accesses the network.
Regeneration additionally needs pysam==0.23.3 and the pinned native BWA build.
"""

import argparse
import gzip
import hashlib
import importlib
import json
import shutil
import subprocess
import sys
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
    failed = []
    checked = 0
    for name, expected in manifest["files"].items():
        if generated_only and (name.startswith("provenance/") or name.endswith("LICENSE.txt")):
            continue
        checked += 1
        if not matches(directory / name, expected):
            failed.append(name)
    if failed:
        raise RuntimeError("Fixture checksum or size mismatch: " + ", ".join(failed))
    print(f"Verified {checked} fixture files against datasets/manifest.json")


def download(source, destination):
    if matches(destination, source):
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    part = destination.with_name(destination.name + ".partial")
    headers = {"User-Agent": "genomics-wasm-fixtures/1"}
    if "range" in source:
        headers["Range"] = source["range"]
    request = urllib.request.Request(source["url"], headers=headers)
    print(f"Downloading {source['url']}", flush=True)
    try:
        with urllib.request.urlopen(request, timeout=60) as response, part.open("wb") as output:
            if "range" in source and response.status != 206:
                raise RuntimeError("The source server did not honour the bounded Range request")
            remaining = source["bytes"]
            while remaining:
                block = response.read(min(1024 * 1024, remaining))
                if not block:
                    break
                output.write(block)
                remaining -= len(block)
            if remaining or response.read(1):
                raise RuntimeError("Source length differs from the pinned byte count")
        if not matches(part, source):
            raise RuntimeError("Source checksum mismatch: " + source["url"])
        part.replace(destination)
    finally:
        part.unlink(missing_ok=True)
    return destination


def fastq_records(path, count):
    """Read a bounded prefix, without trying to reach a truncated gzip trailer."""
    opener = gzip.open if path.name.endswith(".gz") else open
    with opener(path, "rt", encoding="ascii", newline="") as stream:
        for _ in range(count):
            record = [stream.readline() for _ in range(4)]
            if not all(record) or not record[0].startswith("@") or not record[2].startswith("+"):
                raise RuntimeError(f"Incomplete or malformed FASTQ record in {path}")
            if len(record[1].rstrip("\r\n")) != len(record[3].rstrip("\r\n")):
                raise RuntimeError(f"Sequence and quality lengths differ in {path}")
            yield record


def human(output, downloaded, pysam):
    output.mkdir(parents=True)
    for original, target in [
        ("q.fa", "reference.fa"), ("q.fa.fai", "reference.fa.fai"),
        ("NA12878.chr22.tiny.bam", "input.bam"),
        ("NA12878.chr22.tiny.bam.bai", "input.bam.bai"),
    ]:
        shutil.copyfile(downloaded["human/" + original], output / target)
    pairs = {}
    with pysam.AlignmentFile(str(output / "input.bam")) as bam:
        for read in bam:
            if read.is_secondary or read.is_supplementary or not read.is_paired:
                continue
            if read.is_read1 == read.is_read2:
                continue
            pairs.setdefault(read.query_name, {})[1 if read.is_read1 else 2] = read
    names = sorted(name for name, pair in pairs.items() if len(pair) == 2)[:512]
    if len(names) != 512:
        raise RuntimeError("Human source does not contain the expected complete pairs")
    for mate in (1, 2):
        with (output / f"reads_{mate}.fastq").open("w", encoding="ascii", newline="\n") as stream:
            for name in names:
                read = pairs[name][mate]
                sequence = read.get_forward_sequence()
                quality = "".join(chr(q + 33) for q in read.get_forward_qualities())
                stream.write(f"@{name}\n{sequence}\n+\n{quality}\n")


def yeast(output, downloaded, pysam, bwa, work, manifest):
    output.mkdir(parents=True)
    reference = work / "sacCer3.fa"
    with gzip.open(downloaded["yeast/sacCer3.fa.gz"], "rb") as source, reference.open("wb") as target:
        shutil.copyfileobj(source, target)
    sequence = None
    for block in reference.read_text(encoding="ascii").split(">"):
        if block.startswith("chrM\n"):
            sequence = "".join(block.splitlines()[1:])
    if sequence is None or len(sequence) != 85779:
        raise RuntimeError("Unexpected mitochondrial reference sequence")
    (output / "reference.fa").write_text(
        ">chrM\n" + "\n".join(sequence[i:i + 60] for i in range(0, len(sequence), 60)) + "\n",
        encoding="ascii", newline="\n",
    )
    pysam.faidx(str(output / "reference.fa"))
    for mate in (1, 2):
        source = downloaded[f"yeast/SRR1569870_{mate}.prefix.fastq.gz"]
        with (work / f"yeast-prefix_{mate}.fastq").open("w", encoding="ascii", newline="") as target:
            for record in fastq_records(source, 100000):
                target.write("".join(record))
    # The reference includes nuclear chromosomes during enrichment. Indexing chrM
    # alone would not establish that the selected reads prefer mitochondria.
    subprocess.run([str(bwa), "index", reference.name], cwd=work, check=True)
    command = [str(bwa)] + manifest["datasets"]["yeast"]["alignment_command"][1:]
    sam = work / "yeast-full.sam"
    with sam.open("w", encoding="ascii") as stream:
        subprocess.run(command, cwd=work, check=True, stdout=stream)
    pairs = {}
    with pysam.AlignmentFile(str(sam)) as source:
        for read in source:
            if read.is_secondary or read.is_supplementary or read.is_unmapped:
                continue
            if read.reference_name == "chrM":
                pairs.setdefault(read.query_name, {})[1 if read.is_read1 else 2] = read
    names = sorted((name for name, pair in pairs.items() if len(pair) == 2),
                   key=lambda name: int(name.rsplit(".", 1)[1]))
    if len(names) != 1079:
        raise RuntimeError(f"Expected 1079 mitochondrial pairs, found {len(names)}")
    header = {
        "HD": {"VN": "1.6", "SO": "coordinate"},
        "SQ": [{"SN": "chrM", "LN": len(sequence)}],
        "RG": [{"ID": "SRR1569870", "SM": "BY4741", "PL": "ILLUMINA"}],
        "PG": [{"ID": "bwa", "PN": "bwa", "VN": "0.7.19-r1273",
                "CL": " ".join(manifest["datasets"]["yeast"]["alignment_command"])}],
        "CO": ["Primary alignments of complete pairs mapping to chrM; first 100000 pairs of ENA SRR1569870; coordinates unchanged."],
    }
    records = []
    for name in names:
        for mate in (1, 2):
            read = pairs[name][mate]
            read.reference_id = read.next_reference_id = 0
            records.append(read)
    records.sort(key=lambda read: (read.reference_start, read.query_name, read.flag))
    with pysam.AlignmentFile(str(output / "input.bam"), "wb", header=header) as bam:
        for read in records:
            bam.write(read)
    pysam.index(str(output / "input.bam"))
    wanted = set(names)
    for mate in (1, 2):
        with (output / f"reads_{mate}.fastq").open("w", encoding="ascii", newline="") as target:
            for record in fastq_records(work / f"yeast-prefix_{mate}.fastq", 100000):
                if record[0][1:].split()[0] in wanted:
                    target.write("".join(record))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--verify", action="store_true", help="Verify all fixture hashes; never download (default)")
    modes.add_argument("--regenerate", action="store_true", help="Rebuild fixtures from pinned public sources")
    parser.add_argument("--bwa", type=Path, default=ROOT / "build/native/bwa", help="Pinned native BWA executable")
    parser.add_argument("--cache", type=Path, default=ROOT / "build/data-sources", help="Downloaded source cache")
    args = parser.parse_args()
    manifest = json.loads((DATA / "manifest.json").read_text())
    if not args.regenerate:
        verify(DATA, manifest)
        return
    try:
        pysam = importlib.import_module("pysam")
    except ImportError as error:
        raise RuntimeError("Regeneration requires pysam==0.23.3; verification needs no dependencies") from error
    if pysam.__version__ != "0.23.3":
        raise RuntimeError("Use pysam==0.23.3 for reproducible BAM generation")
    bwa = args.bwa.resolve()
    if not bwa.is_file():
        raise RuntimeError("Build the pinned native BWA first, or supply --bwa")
    version = subprocess.run([str(bwa)], capture_output=True, text=True)
    if "Version: 0.7.19-r1273" not in version.stderr:
        raise RuntimeError("Regeneration requires native bwa 0.7.19-r1273")
    cache = args.cache.resolve()
    downloaded = {name: download(source, cache / name) for name, source in manifest["sources"].items()}
    work_parent = ROOT / "build"
    work_parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="data-regeneration-", dir=work_parent) as temporary:
        work = Path(temporary)
        rebuilt = work / "datasets"
        human(rebuilt / "human", downloaded, pysam)
        yeast(rebuilt / "yeast", downloaded, pysam, bwa, work, manifest)
        # Never overwrite the fixtures until every generated byte has matched.
        verify(rebuilt, manifest, generated_only=True)
        for path in rebuilt.glob("*/*"):
            shutil.copyfile(path, DATA / path.relative_to(rebuilt))
    verify(DATA, manifest)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, subprocess.SubprocessError) as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)
