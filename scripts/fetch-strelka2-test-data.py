#!/usr/bin/env python3
"""Verify or reproduce Strelka2-compatible real SEQC2 HCC1395 fixtures.

Only unused BAM sequence-dictionary entries and their numeric reference IDs are
normalized. Every SAM alignment field, including sequence, qualities, positions,
CIGAR, flags, template length and optional tags, stays identical to the audited
somatic source fixture. No reads or variants are synthesized.

--verify and --fetch-sources: Python standard library only.
--regenerate: pysam==0.23.3 with bundled SAMtools/HTSlib1.21.
The required source extractor and source manifest are included under provenance;
there is no dependency on any other repository dataset directory or fetch script.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "datasets" / "strelka2"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def matches(path, metadata):
    return (
        path.is_file()
        and path.stat().st_size == metadata["bytes"]
        and digest(path) == metadata["sha256"]
    )


def verify(directory, manifest, generated_only=False):
    count = 0
    for name, metadata in manifest["files"].items():
        if generated_only and not metadata["generated"]:
            continue
        if not matches(directory / name, metadata):
            raise RuntimeError("Strelka2 fixture size/SHA256 mismatch: " + name)
        count += 1
    print(f"Verified {count} real Strelka2 fixture files", flush=True)


def source_helper():
    path = DATA / "provenance" / "somatic-extraction.py"
    spec = importlib.util.spec_from_file_location("somatic_source_extraction", path)
    module = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode = True
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


def normalize(output, somatic, pysam):
    target = output / "hcc1395"
    target.mkdir(parents=True, exist_ok=True)
    (output / "provenance").mkdir(parents=True, exist_ok=True)
    copied = (
        "reference.fa",
        "reference.fa.fai",
        "regions.bed",
        "extracted-regions.bed",
        "confidence.bed",
        "truth.vcf",
        "truth-snv.vcf",
        "truth-indel.vcf",
    )
    for name in copied:
        shutil.copyfile(somatic / "hcc1395" / name, target / name)
    reference_dictionary = [
        {"SN": fields[0], "LN": int(fields[1])}
        for fields in (
            line.split("\t")
            for line in (target / "reference.fa.fai").read_text().splitlines()
        )
    ]
    reference_names = {row["SN"] for row in reference_dictionary}
    observations = {}
    for sample in ("tumor", "normal"):
        source_path = somatic / "hcc1395" / (sample + ".bam")
        destination = target / (sample + ".bam")
        with pysam.AlignmentFile(str(source_path), "rb") as source:
            source_header = source.header.to_dict()
            source_dictionary = {row["SN"]: row for row in source_header["SQ"]}
            for row in reference_dictionary:
                if source_dictionary[row["SN"]]["LN"] != row["LN"]:
                    raise RuntimeError("Source BAM/reference contig length mismatch")
            header = source.header.to_dict()
            header["SQ"] = [
                source_dictionary[row["SN"]] for row in reference_dictionary
            ]
            header.setdefault("PG", []).append(
                {
                    "ID": "strelka_fixture_dictionary",
                    "PN": "fetch-strelka2-test-data.py",
                    "VN": "1",
                    "CL": "Remove unused SQ entries; remap numeric reference IDs only; retain every SAM alignment field.",
                }
            )
            new_header = pysam.AlignmentHeader.from_dict(header)
            count = sa_count = xa_count = 0
            input_sam_hash, output_sam_hash = hashlib.sha256(), hashlib.sha256()
            with pysam.AlignmentFile(str(destination), "wb", header=new_header) as bam:
                for read in source:
                    if (
                        read.reference_name not in reference_names
                        or read.next_reference_name not in reference_names
                    ):
                        raise RuntimeError(
                            "Source contains a record/mate outside the reference"
                        )
                    sam = read.to_string()
                    normalized = pysam.AlignedSegment.fromstring(sam, new_header)
                    rewritten = normalized.to_string()
                    if sam != rewritten:
                        raise RuntimeError(
                            "Dictionary normalization changed a SAM alignment field"
                        )
                    input_sam_hash.update((sam + "\n").encode())
                    output_sam_hash.update((rewritten + "\n").encode())
                    bam.write(normalized)
                    count += 1
                    sa_count += normalized.has_tag("SA")
                    xa_count += normalized.has_tag("XA")
            pysam.index(str(destination))
            observations[sample] = {
                "records": count,
                "pairs": count // 2,
                "inputHeaderContigs": len(source_header["SQ"]),
                "outputHeaderContigs": len(reference_dictionary),
                "SARecords": sa_count,
                "XARecords": xa_count,
                "inputSAMRecordsSHA256": input_sam_hash.hexdigest(),
                "outputSAMRecordsSHA256": output_sam_hash.hexdigest(),
                "allSAMAlignmentFieldsIdentical": True,
            }
    for name in ("regions.bed", "confidence.bed"):
        pysam.tabix_compress(
            str(target / name), str(target / (name + ".gz")), force=True
        )
        pysam.tabix_index(str(target / (name + ".gz")), preset="bed", force=True)
    (output / "provenance" / "dictionary-normalization.json").write_text(
        json.dumps(
            {
                "method": "Remove the 2779 unused original SQ entries; retained reference names, lengths and order exactly match FASTA. Remap numeric read/mate reference IDs. Preserve all SAM alignment fields and all optional tags, including original-coordinate SA/XA annotations.",
                "referenceDictionary": reference_dictionary,
                "observations": observations,
            },
            indent=2,
        )
        + "\n"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--verify", action="store_true")
    modes.add_argument("--fetch-sources", action="store_true")
    modes.add_argument("--regenerate", action="store_true")
    parser.add_argument(
        "--cache", type=Path, default=ROOT / "build" / "somatic-sources"
    )
    args = parser.parse_args()
    manifest = json.loads((DATA / "manifest.json").read_text())
    if args.verify:
        verify(DATA, manifest)
        return
    # Verify the bundled executable helper and provenance before importing it.
    for name, metadata in manifest["files"].items():
        if not metadata["generated"] and not matches(DATA / name, metadata):
            raise RuntimeError("Immutable provenance checksum mismatch: " + name)
    helper = source_helper()

    def fetch(item):
        name, metadata = item
        return name, helper.download(metadata, args.cache / name)

    with ThreadPoolExecutor(max_workers=4) as pool:
        sources = dict(pool.map(fetch, manifest["sources"].items()))
    print(f"Verified {len(sources)} pinned Strelka2 source files/ranges", flush=True)
    if args.fetch_sources:
        return
    import pysam

    if pysam.__version__ != "0.23.3" or pysam.__samtools_version__ != "1.21":
        raise RuntimeError("Exact regeneration requires pysam0.23.3 / SAMtools1.21")
    source_manifest = json.loads(
        (DATA / "provenance" / "somatic-source-manifest.json").read_text()
    )
    with tempfile.TemporaryDirectory(prefix="strelka2-fixtures-") as temporary:
        work = Path(temporary)
        helper.original_bams(work / "original", sources, source_manifest, pysam)
        helper.prepare(
            work / "somatic", work / "original", sources, source_manifest, pysam
        )
        helper.verify(work / "somatic", source_manifest, generated_only=True)
        normalize(work / "output", work / "somatic", pysam)
        verify(work / "output", manifest, generated_only=True)
        for name, metadata in manifest["files"].items():
            if metadata["generated"]:
                shutil.copyfile(work / "output" / name, DATA / name)
    verify(DATA, manifest)


if __name__ == "__main__":
    main()
