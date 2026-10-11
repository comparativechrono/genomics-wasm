#!/usr/bin/env python3
"""Verify or rebuild compact biological RNA-analysis parity fixtures.

--verify: offline, Python standard library only.
--fetch-sources: fetch/verify all pinned source archives, standard library only.
--regenerate: pysam==0.23.3 plus native HISAT2 2.2.2 build/align executables.
All immutable input snapshots are included in datasets/rna-analysis/provenance.
Only the additional yeast NH>1 read selection downloads public files (~11 MB),
and downloads are bounded and checked against pinned sizes and SHA256 values.
"""
import argparse
from collections import Counter, defaultdict
import gzip
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "datasets" / "rna-analysis"


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
    print(f"Verified {count} real RNA-analysis fixture files", flush=True)


def download(metadata, path):
    if matches(path, metadata):
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    print("Downloading " + metadata["url"], flush=True)
    request = urllib.request.Request(metadata["url"], headers={"User-Agent": "genomics-wasm-fixtures/1"})
    try:
        with urllib.request.urlopen(request, timeout=120) as source, partial.open("wb") as target:
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


def read_fastq(path):
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="ascii", newline="") as stream:
        while True:
            lines = [stream.readline() for _ in range(4)]
            if not lines[0]:
                break
            if not all(lines) or not lines[0].startswith("@") or not lines[2].startswith("+") or len(lines[1].rstrip()) != len(lines[3].rstrip()):
                raise RuntimeError("Malformed biological FASTQ: " + str(path))
            yield lines[0][1:].split()[0], "".join(lines)


def prepare_reads(output, provenance, downloaded, pysam):
    for name in ("human", "yeast"):
        directory = output / name
        directory.mkdir(parents=True)
        for filename in ("reference.fa", "reads_1.fastq", "reads_2.fastq", "splice-sites.tsv"):
            shutil.copyfile(provenance / "inputs" / name / filename, directory / filename)
    pairs = defaultdict(dict)
    with pysam.AlignmentFile(str(downloaded["nfcore-WT_REP1.sorted.bam"]), "rb") as source:
        for read in source:
            if (read.is_unmapped or read.is_secondary or read.is_supplementary or not read.is_paired
                    or read.reference_name != "I" or not read.query_name.startswith("SRR6357070.")
                    or read.is_read1 == read.is_read2):
                continue
            mate = 1 if read.is_read1 else 2
            if mate in pairs[read.query_name]:
                raise RuntimeError("Duplicate primary mate in biological source")
            pairs[read.query_name][mate] = read
    names = sorted(name for name, pair in pairs.items() if len(pair) == 2 and any(read.get_tag("NH") > 1 for read in pair.values()))[:64]
    if len(names) != 64:
        raise RuntimeError("Insufficient real multi-mapping source pairs")
    selected = set(names)
    base_names = {name for name, _ in read_fastq(output / "yeast" / "reads_1.fastq")}
    if selected & base_names:
        raise RuntimeError("Additional source selection repeats an existing fragment")
    orders = []
    for mate in (1, 2):
        order = []
        with (output / "yeast" / f"reads_{mate}.fastq").open("a", encoding="ascii", newline="") as target:
            for name, record in read_fastq(downloaded[f"nfcore-SRR6357070_{mate}.fastq.gz"]):
                if name in selected:
                    target.write(record)
                    order.append(name)
        if len(order) != 64 or set(order) != selected:
            raise RuntimeError("Selected original FASTQ reads unavailable")
        orders.append(order)
    if orders[0] != orders[1]:
        raise RuntimeError("Additional real FASTQ mate order mismatch")
    (output / "yeast" / "additional-multimap-names.txt").write_text("\n".join(names) + "\n")


def annotations(output, provenance):
    attrs = {}
    for line in (provenance / "human-selected.attrs.tsv").read_text().splitlines():
        fields = line.split("\t")
        attrs[fields[4]] = fields
    rows = []
    offset, end, contig = 102480000, 102520000, "chr14_102480001_102520000"
    for line in (provenance / "human-selected.genePred.tsv").read_text().splitlines():
        fields = line.split("\t")
        transcript, strand = fields[1], fields[3]
        gene, gene_name = attrs[transcript][:2]
        starts = list(map(int, fields[9].rstrip(",").split(",")))
        ends = list(map(int, fields[10].rstrip(",").split(",")))
        for index, (left, right) in enumerate(zip(starts, ends), 1):
            left, right = max(left, offset), min(right, end)
            if left >= right:
                continue
            attribute = f'gene_id "{gene}"; transcript_id "{transcript}"; gene_name "{gene_name}"; exon_number "{index}";'
            rows.append((left, right, gene, transcript, f"{contig}\tGENCODEv19_local\texon\t{left - offset + 1}\t{right - offset}\t.\t{strand}\t.\t{attribute}\n"))
    (output / "human" / "annotation.gtf").write_text("".join(row[-1] for row in sorted(rows)))
    shutil.copyfile(provenance / "yeast-selected-exons.gtf", output / "yeast" / "annotation.gtf")
    for dataset in ("human", "yeast"):
        genes = defaultdict(list)
        for line in (output / dataset / "annotation.gtf").read_text().splitlines():
            fields = line.split("\t")
            gene = re.search(r'gene_id "([^"]+)"', fields[8]).group(1)
            genes[(fields[0], gene)].append((int(fields[3]) - 1, int(fields[4])))
        regions, exons = [], []
        for (chrom, gene), intervals in sorted(genes.items()):
            regions.append((chrom, min(x[0] for x in intervals), max(x[1] for x in intervals), gene))
            merged = []
            for start, finish in sorted(set(intervals)):
                if merged and start <= merged[-1][1]:
                    merged[-1][1] = max(merged[-1][1], finish)
                else:
                    merged.append([start, finish])
            exons.extend((chrom, start, finish, gene + f"_exonUnion_{i + 1}") for i, (start, finish) in enumerate(merged))
        for filename, records in [("regions.bed", regions), ("exons.bed", exons)]:
            (output / dataset / filename).write_text("".join("\t".join(map(str, row)) + "\n" for row in sorted(records)))


def run(command, cwd, logfile):
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True)
    logfile.write_text(result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError(f"Native fixture command failed: {command}\n{result.stderr}")


def overlap_bases(read1, read2):
    return sum(max(0, min(b, d) - max(a, c)) for a, b in read1.get_blocks() for c, d in read2.get_blocks())


def align(directory, build, aligner, pysam):
    run([str(build), "-p", "1", "reference.fa", "reference"], directory, directory / "index.log")
    args = [str(aligner), "-p", "1", "--seed", "0", "--dta", "-k", "10", "--known-splicesite-infile", "splice-sites.tsv", "-x", "reference", "-1", "reads_1.fastq", "-2", "reads_2.fastq", "-S", "raw.sam"]
    run(args, directory, directory / "alignment.log")
    # Remove build-host/path-bearing @PG only; retain every alignment field/tag.
    raw = (directory / "raw.sam").read_text().splitlines()
    raw = [line for line in raw if not line.startswith("@PG\t")]
    raw.insert(next((i for i, line in enumerate(raw) if not line.startswith("@")), len(raw)),
               "@PG\tID:fixture_hisat2\tPN:hisat2\tVN:2.2.2\tCL:hisat2-align-s -p 1 --seed 0 --dta -k 10 --known-splicesite-infile splice-sites.tsv -x reference -1 reads_1.fastq -2 reads_2.fastq -S raw.sam")
    (directory / "raw.sam").write_text("\n".join(raw) + "\n")
    pysam.sort("--no-PG", "-o", str(directory / "input.bam"), str(directory / "raw.sam"))
    pysam.index(str(directory / "input.bam"))
    (directory / "input.sam").write_text(pysam.view("-h", "--no-PG", str(directory / "input.bam")))
    pysam.faidx(str(directory / "reference.fa"))
    # HTSlib makes a supplied reference_filename absolute in CRAM @SQ UR and
    # embeds the output filename in the CRAM file ID. Use a local UR+M5 lookup
    # and relative filenames to make both independent of the build directory.
    original_directory = Path.cwd()
    original_environment = {key: os.environ.get(key) for key in ("REF_PATH", "REF_CACHE")}
    try:
        os.chdir(directory)
        os.environ["REF_PATH"] = "."
        os.environ["REF_CACHE"] = "."
        with pysam.FastaFile("reference.fa") as reference, pysam.AlignmentFile("input.bam", "rb") as source:
            header = source.header.to_dict()
            for sequence in header["SQ"]:
                sequence["UR"] = "reference.fa"
                sequence["M5"] = hashlib.md5(reference.fetch(sequence["SN"]).upper().encode("ascii")).hexdigest()
            with pysam.AlignmentFile("input.cram", "wc", header=header, format_options=[b"version=3.0"]) as target:
                for record in source:
                    target.write(record)
    finally:
        os.chdir(original_directory)
        for key, value in original_environment.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    pysam.index(str(directory / "input.cram"))
    with pysam.AlignmentFile(str(directory / "input.bam"), "rb") as source:
        records = list(source)
        names = sorted({read.query_name for read in records})
        subsets = [set(names[::2]), set(names[1::2])]
        for i, subset in enumerate(subsets, 1):
            with pysam.AlignmentFile(str(directory / f"half{i}.bam"), "wb", header=source.header) as target:
                for record in records:
                    if record.query_name in subset:
                        target.write(record)
            pysam.index(str(directory / f"half{i}.bam"))
    primary_pairs = defaultdict(dict)
    flags, qualities, cigars, tags = Counter(), Counter(), Counter(), Counter()
    for read in records:
        flags[str(read.flag)] += 1
        qualities[str(read.mapping_quality)] += 1
        for op, length in read.cigartuples or []:
            cigars[str(op)] += 1
        for tag in ("NH", "XS"):
            if read.has_tag(tag):
                tags[tag] += 1
        if not read.is_secondary and not read.is_supplementary and not read.is_unmapped:
            primary_pairs[read.query_name][1 if read.is_read1 else 2] = read
    overlap_pairs, overlap_total = 0, 0
    for pair in primary_pairs.values():
        if set(pair) == {1, 2} and pair[1].reference_id == pair[2].reference_id:
            bases = overlap_bases(pair[1], pair[2])
            overlap_pairs += bases > 0
            overlap_total += bases
    observation = dict(fragments=len(names), alignmentRecords=len(records),
                       mappedRecords=sum(not r.is_unmapped for r in records),
                       primaryMappedRecords=sum(not r.is_unmapped and not r.is_secondary and not r.is_supplementary for r in records),
                       secondaryRecords=sum(r.is_secondary for r in records),
                       splicedRecords=sum(any(op == 3 for op, length in (r.cigartuples or [])) for r in records),
                       multiMappingRecords=sum(r.has_tag("NH") and r.get_tag("NH") > 1 for r in records),
                       mateOverlapPairs=overlap_pairs, mateOverlapAlignedBases=overlap_total,
                       cigarOperationCounts=dict(cigars), mapqCounts=dict(qualities), flagCounts=dict(flags),
                       recordsWithTags=dict(tags), splitFragments=list(map(len, subsets)))
    (directory / "observations.json").write_text(json.dumps(observation, indent=2, sort_keys=True) + "\n")
    # Indexes are only preparation intermediates; end users analyse ready BAM/SAM.
    for path in directory.glob("reference.*.ht2"):
        path.unlink()
    for name in ("raw.sam", "index.log", "alignment.log"):
        (directory / name).unlink()
    return observation


def regenerate(manifest, cache, build, aligner, bootstrap=False):
    pysam = importlib.import_module("pysam")
    if pysam.__version__ != "0.23.3":
        raise RuntimeError("Regeneration requires pysam==0.23.3")
    if not build.is_file() or not aligner.is_file():
        raise RuntimeError("Supply existing native HISAT2 2.2.2 --hisat2-build and --hisat2-align executables")
    for binary in (build, aligner):
        version = subprocess.check_output([str(binary), "--version"], text=True)
        if "version 2.2.2" not in version:
            raise RuntimeError("Native HISAT2 version must be 2.2.2")
    downloaded = {name: download(metadata, cache / name) for name, metadata in manifest["sources"].items()
                  if metadata.get("usedForData")}
    cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="rna-analysis-", dir=cache) as temporary:
        output = Path(temporary)
        prepare_reads(output, DATA / "provenance", downloaded, pysam)
        annotations(output, DATA / "provenance")
        for name in ("human", "yeast"):
            observation = align(output / name, build, aligner, pysam)
            print(name, json.dumps(observation, sort_keys=True), flush=True)
            if bootstrap:
                manifest["datasets"][name]["observations"] = observation
        if not bootstrap:
            verify(output, manifest, generated_only=True)
        for name in ("human", "yeast"):
            destination = DATA / name
            destination.mkdir(exist_ok=True)
            for path in (output / name).iterdir():
                shutil.copyfile(path, destination / path.name)
    if bootstrap:
        manifest["files"] = {str(p.relative_to(DATA)): dict(bytes=p.stat().st_size, sha256=digest(p), generated=p.relative_to(DATA).parts[0] in ("human", "yeast"))
                             for p in sorted(DATA.rglob("*")) if p.is_file() and p.name != "manifest.json"}
        (DATA / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    verify(DATA, manifest)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--verify", action="store_true")
    action.add_argument("--fetch-sources", action="store_true")
    action.add_argument("--regenerate", action="store_true")
    parser.add_argument("--cache", type=Path, default=ROOT / "build" / "rna-analysis-sources")
    parser.add_argument("--hisat2-build", type=Path, default=ROOT / "build/native/hisat2-bin/hisat2-build-s")
    parser.add_argument("--hisat2-align", type=Path, default=ROOT / "build/native/hisat2-bin/hisat2-align-s")
    args = parser.parse_args()
    manifest = json.loads((DATA / "manifest.json").read_text())
    if args.verify:
        verify(DATA, manifest)
    elif args.fetch_sources:
        cache = args.cache.resolve()
        for name, metadata in manifest["sources"].items():
            download(metadata, cache / name)
        print(f"Verified {len(manifest['sources'])} pinned source archives in {cache}", flush=True)
    else:
        # Verify every immutable input snapshot before executing a native tool.
        for name, metadata in manifest["files"].items():
            if not metadata["generated"] and not matches(DATA / name, metadata):
                raise RuntimeError("Immutable fixture input checksum mismatch: " + name)
        regenerate(manifest, args.cache.resolve(), args.hisat2_build.resolve(), args.hisat2_align.resolve())


if __name__ == "__main__":
    main()
