#!/usr/bin/env python3
"""Verify or reproduce small real SEQC2 HCC1395/HCC1395BL somatic fixtures.

--verify and --fetch-sources use only Python's standard library.
--regenerate requires exactly pysam0.23.3 (bundled HTSlib/SAMtools1.21).
Each public BAM byte range is size/SHA256-pinned: no whole-genome BAM download.
No reads, variants or reference bases are simulated or spiked into the inputs.
"""
import argparse
import collections
from concurrent.futures import ThreadPoolExecutor
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import struct
import tempfile
import urllib.request
import zlib

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "datasets" / "somatic"


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
            raise RuntimeError("Fixture size/SHA256 mismatch: " + name)
        count += 1
    print(f"Verified {count} real somatic fixture files", flush=True)


def download(metadata, target):
    if matches(target, metadata):
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".partial")
    headers = {"User-Agent": "genomics-wasm-fixtures/1"}
    interval = metadata.get("range")
    if interval:
        headers["Range"] = f"bytes={interval['start']}-{interval['endInclusive']}"
    request = urllib.request.Request(metadata["url"], headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=60) as source, partial.open(
            "wb"
        ) as output:
            if interval:
                expected = f"bytes {interval['start']}-{interval['endInclusive']}/{metadata['fullObjectBytes']}"
                if (
                    source.status != 206
                    or source.headers.get("Content-Range") != expected
                ):
                    raise RuntimeError(
                        "Server did not honor exact bounded BAM range: "
                        + metadata["url"]
                    )
            remaining = metadata["bytes"]
            while remaining:
                block = source.read(min(1024 * 1024, remaining))
                if not block:
                    break
                output.write(block)
                remaining -= len(block)
            if remaining or source.read(1):
                raise RuntimeError("Source size differs from pinned size")
        if not matches(partial, metadata):
            raise RuntimeError("Source SHA256 mismatch: " + metadata["url"])
        partial.replace(target)
    finally:
        partial.unlink(missing_ok=True)
    return target


def blocks(raw, absolute_start=0):
    i = 0
    while i + 18 <= len(raw):
        if raw[i : i + 4] != b"\x1f\x8b\x08\x04":
            raise RuntimeError(f"Bad BGZF header {i}")
        size = struct.unpack_from("<H", raw, i + 16)[0] + 1
        if i + size > len(raw):
            return
        yield absolute_start + i, zlib.decompress(raw[i : i + size], 31)
        i += size


def bam_header(raw):
    data = b"".join(d for _, d in blocks(raw))
    if data[:4] != b"BAM\1":
        raise RuntimeError("not BAM")
    textlen = struct.unpack_from("<i", data, 4)[0]
    pos = 8 + textlen
    nref = struct.unpack_from("<i", data, pos)[0]
    pos += 4
    refs = []
    for _ in range(nref):
        namelen = struct.unpack_from("<i", data, pos)[0]
        pos += 4
        name = data[pos : pos + namelen - 1].decode()
        pos += namelen
        length = struct.unpack_from("<i", data, pos)[0]
        pos += 4
        refs.append((name, length))
    return data[:pos], refs


def original_bams(output, sources, manifest, pysam):
    output.mkdir(parents=True, exist_ok=True)
    regions = manifest["regionMappings"]
    for sample, extraction in manifest["extraction"].items():
        header, refs = bam_header(sources[extraction["headerSource"]].read_bytes())
        tid = [x[0] for x in refs].index("chr21")
        whole, seen = bytearray(header), set()
        for chunk in extraction["chunks"]:
            a, end = chunk["virtualStart"], chunk["virtualEnd"]
            name = chunk["source"]
            start = manifest["sources"][name]["range"]["start"]
            decoded = list(blocks(sources[name].read_bytes(), start))
            data = b"".join(row[1] for row in decoded)
            endoff = 0
            for blockstart, blockdata in decoded:
                if blockstart == end >> 16:
                    endoff += end & 65535
                    break
                endoff += len(blockdata)
            else:
                raise RuntimeError("BAM virtual end block absent")
            pos = a & 65535
            while pos < endoff:
                size = struct.unpack_from("<i", data, pos)[0]
                record = data[pos : pos + size + 4]
                if len(record) != size + 4:
                    raise RuntimeError("Incomplete source BAM record")
                refid, left = struct.unpack_from("<ii", record, 4)
                # Same bounded start filter used when creating the immutable source
                # snapshots. Complete pairs finally retained start inside a window.
                if refid == tid and any(
                    g["start0"] - 1000 <= left < g["end0"] for g in regions
                ):
                    if record not in seen:
                        whole.extend(record)
                        seen.add(record)
                pos += size + 4
        raw = output / (sample + "-raw-blocks.bam")
        with pysam.BGZFile(str(raw), "wb") as stream:
            stream.write(bytes(whole))
        target = output / (sample + "-source.bam")
        with pysam.AlignmentFile(str(raw), "rb") as source, pysam.AlignmentFile(
            str(target), "wb", header=source.header
        ) as destination:
            for read in source:
                if any(
                    read.reference_name == g["originalContig"]
                    and read.reference_start < g["end0"]
                    and (read.reference_end or read.reference_start + 1) > g["start0"]
                    for g in regions
                ):
                    destination.write(read)
        pysam.index(str(target))
        raw.unlink()


def prepare(output, originals, sources, manifest, pysam):
    D, OUT, C = output, output / "hcc1395", originals
    REGIONS = manifest["regionMappings"]
    OUT.mkdir(parents=True, exist_ok=True)
    (D / "provenance/original").mkdir(parents=True, exist_ok=True)
    seq = "".join(
        gzip.open(sources["hg38-chr21.fa.gz"], "rt").read().splitlines()[1:]
    ).upper()
    with (OUT / "reference.fa").open("w") as f:
        for g in REGIONS:
            s = seq[g["start0"] : g["end0"]]
            f.write(">" + g["contig"] + "\n")
            f.write("".join(s[i : i + 60] + "\n" for i in range(0, len(s), 60)))
    pysam.faidx(str(OUT / "reference.fa"))
    (OUT / "regions.bed").write_text(
        "".join(
            f"{g['contig']}\t{g['flank']}\t{g['end0']-g['start0']-g['flank']}\n"
            for g in REGIONS
        )
    )
    (OUT / "extracted-regions.bed").write_text(
        "".join(f"{g['contig']}\t0\t{g['end0']-g['start0']}\n" for g in REGIONS)
    )
    obs = {}
    for sample in ["tumor", "normal"]:
        source = C / f"{sample}-source.bam"
        (D / "provenance/original" / f"{sample}.bam").write_bytes(source.read_bytes())
        (D / "provenance/original" / f"{sample}.bam.bai").write_bytes(
            source.with_suffix(".bam.bai").read_bytes()
        )
        with pysam.AlignmentFile(str(source)) as f:
            header = f.header.to_dict()
            original_header = str(f.header)
            pairs = collections.defaultdict(dict)
            for r in f:
                if (
                    r.is_unmapped
                    or r.is_secondary
                    or r.is_supplementary
                    or not r.is_paired
                    or r.mate_is_unmapped
                    or r.is_read1 == r.is_read2
                ):
                    continue
                mate = 1 if r.is_read1 else 2
                if mate in pairs[r.query_name]:
                    raise RuntimeError("duplicateprimary")
                pairs[r.query_name][mate] = r
        (D / "provenance" / f"{sample}-original-header.sam").write_text(original_header)
        originalSQ = len(header["SQ"])
        header["SQ"] += [
            {"SN": g["contig"], "LN": g["end0"] - g["start0"]} for g in REGIONS
        ]
        header.setdefault("CO", []).append(
            "Complete mapped primary pairs rebased from real SEQC2 chr21 windows; see manifest.regionMappings. SEQ QUAL CIGAR TLEN and original tags unchanged."
        )
        header.setdefault("PG", []).append(
            {
                "ID": "somatic_fixture_rebase",
                "PN": "fetch-somatic-test-data.py",
                "VN": "1",
                "CL": "Complete primary mapped pairs wholly inside documented chr21 windows; shift both read/mate positions and reference IDs only.",
            }
        )
        newheader = pysam.AlignmentHeader.from_dict(header)
        records = []
        paircounts = collections.Counter()
        for name, pair in pairs.items():
            if set(pair) != {1, 2}:
                continue
            for i, g in enumerate(REGIONS):
                if all(
                    r.reference_name == g["originalContig"]
                    and r.reference_start >= g["start0"]
                    and r.reference_end <= g["end0"]
                    for r in pair.values()
                ):
                    a, b = pair[1], pair[2]
                    if (
                        a.next_reference_id != b.reference_id
                        or b.next_reference_id != a.reference_id
                        or a.next_reference_start != b.reference_start
                        or b.next_reference_start != a.reference_start
                    ):
                        continue
                    for original in pair.values():
                        r = pysam.AlignedSegment.fromstring(
                            original.to_string(), newheader
                        )
                        r.reference_id = originalSQ + i
                        r.next_reference_id = originalSQ + i
                        r.reference_start -= g["start0"]
                        r.next_reference_start -= g["start0"]
                        records.append(r)
                    paircounts[g["contig"]] += 1
                    break
        records.sort(
            key=lambda r: (r.reference_id, r.reference_start, r.query_name, r.flag)
        )
        with pysam.AlignmentFile(str(OUT / f"{sample}.bam"), "wb", header=header) as f:
            for r in records:
                f.write(r)
        pysam.index(str(OUT / f"{sample}.bam"))
        obs[sample] = {
            "records": len(records),
            "pairs": len(records) // 2,
            "pairsPerContig": dict(paircounts),
            "duplicateRecords": sum(r.is_duplicate for r in records),
            "qcFailRecords": sum(r.is_qcfail for r in records),
            "mapqCounts": dict(
                sorted(collections.Counter(r.mapping_quality for r in records).items())
            ),
            "readLengthCounts": dict(
                sorted(collections.Counter(r.query_length for r in records).items())
            ),
            "cigarOperations": dict(
                sorted(
                    collections.Counter(
                        op for r in records for op, n in r.cigartuples
                    ).items()
                )
            ),
            "readGroups": header["RG"],
        }
    rows = []
    headers = []
    with gzip.open(sources["truth.vcf.gz"], "rt") as f:
        for line in f:
            if line.startswith("##"):
                if not line.startswith(("##contig=", "##reference=")):
                    headers.append(line)
            elif line.startswith("#CHROM"):
                columns = line
            else:
                v = line.rstrip().split("\t")
                pos = int(v[1]) - 1
                for g in REGIONS:
                    if (
                        v[0] == g["originalContig"]
                        and g["start0"] + g["flank"] <= pos < g["end0"] - g["flank"]
                    ):
                        assert seq[pos : pos + len(v[3])] == v[3], v[:5]
                        orig = v.copy()
                        v[0] = g["contig"]
                        v[1] = str(pos - g["start0"] + 1)
                        rows.append((v, orig))
                        break
    headers += [
        "##reference=reference.fa\n",
        "##source=SEQC2_v1.2.1_Google_DeepSomatic_mirror_coordinate_rebased_without_allele_changes\n",
    ]
    headers += [
        f"##contig=<ID={g['contig']},length={g['end0']-g['start0']}>\n" for g in REGIONS
    ]
    for name, pred in [
        ("truth.vcf", lambda v: True),
        ("truth-snv.vcf", lambda v: len(v[3]) == len(v[4]) == 1),
        ("truth-indel.vcf", lambda v: len(v[3]) != len(v[4])),
    ]:
        (OUT / name).write_text(
            "".join(headers)
            + columns
            + "".join("\t".join(v) + "\n" for v, orig in rows if pred(v))
        )
    (D / "provenance/original-truth-records.tsv").write_text(
        "".join("\t".join(orig) + "\n" for v, orig in rows)
    )
    confidence = []
    for line in (sources["confidence.bed"]).read_text().splitlines():
        if line.startswith("#") or not line.strip():
            continue
        cols = line.split("\t")
        left, right = map(int, cols[1:3])
        for g in REGIONS:
            if cols[0] == g["originalContig"]:
                a, b = max(left, g["start0"] + g["flank"]), min(
                    right, g["end0"] - g["flank"]
                )
                if a < b:
                    confidence.append((g["contig"], a - g["start0"], b - g["start0"]))
    (OUT / "confidence.bed").write_text(
        "".join(f"{c}\t{a}\t{b}\n" for c, a, b in confidence)
    )
    (D / "provenance/observations.json").write_text(
        json.dumps(
            {
                "regionMappings": REGIONS,
                "observations": obs,
                "truth": [
                    {
                        "contig": v[0],
                        "position": int(v[1]),
                        "ref": v[3],
                        "alt": v[4],
                        "originalContig": orig[0],
                        "originalPosition": int(orig[1]),
                        "filter": v[6],
                    }
                    for v, orig in rows
                ],
            },
            indent=2,
        )
        + "\n"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument(
        "--verify",
        action="store_true",
        help="Offline check of all fixture/provenance hashes",
    )
    modes.add_argument(
        "--fetch-sources",
        action="store_true",
        help="Fetch/verify bounded pinned source files and raw BAM ranges only",
    )
    modes.add_argument(
        "--regenerate",
        action="store_true",
        help="Rebuild exactly with pysam0.23.3; replace files only when every hash agrees",
    )
    parser.add_argument(
        "--cache", type=Path, default=ROOT / "build" / "somatic-sources"
    )
    args = parser.parse_args()
    manifest = json.loads((DATA / "manifest.json").read_text())
    if args.verify:
        verify(DATA, manifest)
        return

    def fetch_item(item):
        name, metadata = item
        return name, download(metadata, args.cache / name)

    with ThreadPoolExecutor(max_workers=4) as pool:
        sources = dict(pool.map(fetch_item, manifest["sources"].items()))
    print(f"Verified {len(sources)} pinned somatic source files/ranges", flush=True)
    if args.fetch_sources:
        return
    import pysam

    if pysam.__version__ != "0.23.3" or pysam.__samtools_version__ != "1.21":
        raise RuntimeError("Exact regeneration requires pysam0.23.3 / SAMtools1.21")
    for name, metadata in manifest["files"].items():
        if not metadata["generated"] and not matches(DATA / name, metadata):
            raise RuntimeError("Immutable provenance checksum mismatch: " + name)
    with tempfile.TemporaryDirectory(prefix="somatic-fixtures-") as temporary:
        work = Path(temporary)
        original_bams(work / "source-snapshots", sources, manifest, pysam)
        prepare(work / "output", work / "source-snapshots", sources, manifest, pysam)
        verify(work / "output", manifest, generated_only=True)
        for name, metadata in manifest["files"].items():
            if metadata["generated"]:
                shutil.copyfile(work / "output" / name, DATA / name)
    verify(DATA, manifest)


if __name__ == "__main__":
    main()
