#!/usr/bin/env python3
"""Compare captured KING executions and check synthetic genotype truth.

Native parity is necessary but not sufficient: each side must independently
recover the counts and relationships encoded by the synthetic fixture.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import itertools
import json
import math
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
NUMBER = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")
INTEGER = re.compile(r"^[+-]?\d+$")
SEGMENTS = "FID1 ID1 FID2 ID2 IBDType Chr StartMB StopMB StartSNP StopSNP N_SNP Length".split()
FAMILY = {"DAD", "MOM", "CHILD1", "CHILD2", "DUP"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def safe_path(base, name):
    require(isinstance(name, str) and name and not name.startswith("/")
            and all(part not in ("", ".", "..") for part in name.split("/"))
            and "\\" not in name, f"unsafe path: {name!r}")
    path = base / name
    require(not path.is_symlink() and path.is_file(), f"missing regular file: {path}")
    return path


def finite(value, context="number"):
    try:
        number = float(value)
    except (ValueError, TypeError) as error:
        raise ValueError(f"invalid numeric {context}: {value!r}") from error
    require(math.isfinite(number), f"nonfinite {context}: {value}")
    return number


def exact_int(value, context="count"):
    require(isinstance(value, str) and INTEGER.fullmatch(value), f"noninteger {context}: {value}")
    return int(value)


def near(value, expected, context, tolerance=0.00005001):
    actual = finite(value, context)
    require(abs(actual - expected) <= tolerance, f"truth mismatch {context}: {actual} != {expected}")


def text_rows(data, name):
    if name.endswith(".gz"):
        data = gzip.decompress(data)
    text = data.decode("utf-8", errors="strict")
    require("\x00" not in text, f"NUL in {name}")
    rows = [line.split() for line in text.splitlines() if line.strip()]
    for row in rows:
        for value in row:
            require(value.lower() not in {"nan", "+nan", "-nan", "inf", "+inf", "-inf", "infinity", "+infinity", "-infinity"}, f"nonfinite token in {name}")
            if NUMBER.fullmatch(value):
                finite(value, name)
    return rows


def table(data, name):
    rows = text_rows(data, name)
    require(rows, f"empty table: {name}")
    header = rows.pop(0)
    require(len(set(header)) == len(header), f"duplicate columns: {name}")
    require(rows, f"no records: {name}")
    require(all(len(row) == len(header) for row in rows), f"ragged table: {name}")
    return header, [dict(zip(header, row)) for row in rows]


def keyed(rows, columns, context):
    result = {}
    for row in rows:
        require(all(column in row for column in columns), f"missing key columns in {context}")
        key = tuple(row[column] for column in columns)
        require(key not in result, f"duplicate row in {context}: {key}")
        result[key] = row
    return result


def pair_key(first, second):
    return "|".join(sorted((first, second)))


def pair_rows(rows, context):
    result = {}
    for row in rows:
        require("ID1" in row and "ID2" in row, f"missing pair IDs in {context}")
        require(row["ID1"] != row["ID2"], f"self-pair in {context}")
        key = pair_key(row["ID1"], row["ID2"])
        require(key not in result, f"duplicate pair in {context}: {key}")
        result[key] = row
    return result


def require_columns(header, columns, context):
    require(set(columns).issubset(header), f"missing required columns in {context}: {sorted(set(columns) - set(header))}")


def load_fixture(dataset):
    require(dataset in ("main", "small"), "unknown dataset")
    base = ROOT / "datasets/king" / ("small" if dataset == "small" else "")
    manifest = json.loads(safe_path(base, "manifest.json").read_text())
    for name, identity in manifest["files"].items():
        data = safe_path(base, name).read_bytes()
        require(len(data) == identity["bytes"] and sha(data) == identity["sha256"], f"fixture identity mismatch: {name}")
    truth = json.loads(safe_path(base, "truth.json").read_text())
    require(truth["markerCount"] == manifest["markerCount"] and truth["sampleCount"] == manifest["sampleCount"], "fixture dimensions disagree")
    fam_rows = text_rows(safe_path(base, "cohort.fam").read_bytes(), "cohort.fam")
    require(all(len(row) == 6 for row in fam_rows), "ragged FAM fixture")
    fam = {row[1]: row for row in fam_rows}
    require(len(fam) == truth["sampleCount"] and set(fam) == set(truth["sampleStats"]), "fixture sample IDs disagree")
    bim = text_rows(safe_path(base, "cohort.bim").read_bytes(), "cohort.bim")
    require(len(bim) == truth["markerCount"] and all(len(row) == 6 for row in bim), "fixture marker count disagrees")
    return {"base": base, "truth": truth, "fam": fam, "bim": bim, "manifest": manifest}


def relationship(first, second):
    names = {first, second}
    if names == {"CHILD1", "DUP"}:
        return "Dup/MZ"
    if names & {"DAD", "MOM"} and names & {"CHILD1", "CHILD2", "DUP"}:
        return "PO"
    if "CHILD2" in names and names & {"CHILD1", "DUP"}:
        return "FS"
    return "UN"


def check_pair_table(data, name, fixture, expected_keys, *, clustered=False, ibs=False, types=False):
    header, rows = table(data, name)
    require_columns(header, "ID1 ID2 N_SNP Kinship".split(), name)
    if ibs:
        require_columns(header, "N_IBS0 N_IBS1 N_IBS2 NHetHet NHomHom N_Het1 N_Het2".split(), name)
    else:
        require_columns(header, ["HetHet", "IBS0"], name)
    if types:
        require_columns(header, "IBD1Seg IBD2Seg PropIBD InfType".split(), name)
    if name.endswith((".kin0", ".ibs0")):
        require_columns(header, ["FID1", "FID2"], name)
    else:
        require_columns(header, ["FID"], name)
    pairs = pair_rows(rows, name)
    require(set(pairs) == set(expected_keys), f"pair inventory mismatch in {name}: missing {sorted(set(expected_keys) - set(pairs))[:5]}, extra {sorted(set(pairs) - set(expected_keys))[:5]}")
    for key, row in pairs.items():
        truth = fixture["truth"]["pairStats"][key]
        for field in ("N_SNP", "N_IBS0", "N_IBS1", "N_IBS2", "NHetHet"):
            if field in row:
                require(exact_int(row[field], f"{name}/{key}/{field}") == truth[field], f"truth count mismatch {name}/{key}/{field}")
        for field in ("N_Het1", "N_Het2"):
            if field in row:
                index = 0 if field.endswith("1") else 1
                identity = row["ID1"] if index == 0 else row["ID2"]
                original = truth["IDs"].index(identity) + 1
                require(exact_int(row[field]) == truth[f"N_Het{original}"], f"truth heterozygote mismatch {name}/{key}/{field}")
        if "NHomHom" in row:
            require(exact_int(row["NHomHom"]) == truth["N_SNP"] - truth["N_Het1"] - truth["N_Het2"] + truth["NHetHet"], f"truth homozygote mismatch {name}/{key}")
        for field in ("HetHet", "IBS0", "Kinship"):
            if field in row:
                expected = truth["KinshipWithin"] if field == "Kinship" and clustered else truth[field]
                near(row[field], expected, f"{name}/{key}/{field}")
        if "InfType" in row:
            expected = relationship(row["ID1"], row["ID2"])
            require(row["InfType"] == expected, f"relationship classification mismatch {name}/{key}")
            check_ibd_fractions(row, expected, name + "/" + key)
        if "Error" in row:
            near(row["Error"], 0, name + "/" + key + "/pedigree-error", 0)
        for id_column, fid_column in (("ID1", "FID1"), ("ID2", "FID2")):
            if fid_column in row:
                require(row[fid_column] == fixture["fam"][row[id_column]][0], f"family identity mismatch {name}/{key}")
        if "FID" in row and not clustered:
            require(row["FID"] == fixture["fam"][row["ID1"]][0] == fixture["fam"][row["ID2"]][0], f"within-family identity mismatch {name}/{key}")
    return {"pairs": len(pairs), "independentGenotypeCounts": True, "classificationChecked": types}


def check_ibd_fractions(row, kind, context):
    if not all(field in row for field in ("IBD1Seg", "IBD2Seg", "PropIBD")):
        return
    one, two, prop = (finite(row[field], context) for field in ("IBD1Seg", "IBD2Seg", "PropIBD"))
    require(0 <= one <= 1 and 0 <= two <= 1 and one + two <= 1.0001, f"invalid IBD fractions {context}")
    near(row["PropIBD"], one / 2 + two, context + "/PropIBD", 0.00011)
    if kind == "Dup/MZ":
        require((one, two, prop) == (0, 1, 1), f"duplicate IBD mismatch {context}")
    elif kind == "PO":
        require((one, two, prop) == (1, 0, 0.5), f"parent-offspring IBD mismatch {context}")
    elif kind == "FS":
        require(0.42 <= one <= 0.58 and 0.20 <= two <= 0.30 and 0.45 <= prop <= 0.55, f"sibling IBD mismatch {context}")
    else:
        require(prop < 0.05, f"unexpected unrelated IBD {context}")


def check_allsegs(data, fixture):
    header, rows = table(data, "resultallsegs.txt")
    require_columns(header, "Segment Chr StartMB StopMB Length N_SNP StartSNP StopSNP".split(), "usable segments")
    observed = keyed(rows, ["Chr"], "usable segments")
    chrom = {}
    for record in fixture["bim"]:
        chrom.setdefault(record[0], []).append(record)
    require(set(observed) == {(key,) for key in chrom}, "usable chromosome inventory mismatch")
    for chromosome, records in chrom.items():
        row = observed[(chromosome,)]
        require(exact_int(row["N_SNP"]) == len(records), "usable marker count mismatch")
        require(row["StartSNP"] == records[0][1] and row["StopSNP"] == records[-1][1], "usable marker bounds mismatch")
        near(row["StartMB"], int(records[0][3]) / 1e6, "usable segment start", 0.000501)
        near(row["StopMB"], int(records[-1][3]) / 1e6, "usable segment stop", 0.000501)
    return {"chromosomes": len(chrom), "markers": sum(len(value) for value in chrom.values())}


def check_ibd(files, fixture):
    header, rows = table(files["result.seg"], "result.seg")
    require(header == "FID1 ID1 FID2 ID2 IBD1Seg IBD2Seg PropIBD InfType".split(), "unexpected IBD summary header")
    pairs = pair_rows(rows, "IBD summary")
    expected = {key for key, stat in fixture["truth"]["pairStats"].items() if relationship(*stat["IDs"]) != "UN"}
    require(set(pairs) == expected, "IBD relationship inventory mismatch")
    for key, row in pairs.items():
        require(row["FID1"] == fixture["fam"][row["ID1"]][0] and row["FID2"] == fixture["fam"][row["ID2"]][0], f"IBD family identity mismatch {key}")
        kind = relationship(row["ID1"], row["ID2"])
        require(row["InfType"] == kind, f"IBD classification mismatch {key}")
        check_ibd_fractions(row, kind, key)
    header, segments = table(files["result.segments.gz"], "result.segments.gz")
    require(header == SEGMENTS, "unexpected IBD segment header")
    require(len({tuple(row.values()) for row in segments}) == len(segments), "duplicate IBD segment rows")
    by_pair = {}
    snps = {row[1]: row for row in fixture["bim"]}
    chromosome_bounds = {}
    for marker in fixture["bim"]:
        chromosome_bounds.setdefault(marker[0], []).append(marker)
    for row in segments:
        key = pair_key(row["ID1"], row["ID2"])
        require(key in expected, f"spurious IBD segment pair {key}")
        require(row["IBDType"] in ("IBD1", "IBD2"), "invalid IBD segment type")
        require(row["StartSNP"] in snps and row["StopSNP"] in snps, "unknown segment markers")
        first, last = snps[row["StartSNP"]], snps[row["StopSNP"]]
        require(first[0] == last[0] == row["Chr"], "segment crosses chromosomes")
        start, stop = int(first[3]), int(last[3])
        require(start < stop, "invalid segment bounds")
        near(row["StartMB"], start / 1e6, "segment start", 0.000501)
        near(row["StopMB"], stop / 1e6, "segment stop", 0.000501)
        near(row["Length"], (stop - start) / 1e6, "segment length", 0.05001)
        require(exact_int(row["N_SNP"]) == (stop - start) // 10000 + 1, "segment SNP count mismatch")
        by_pair.setdefault(key, []).append(row)
    require(set(by_pair) == expected, "missing IBD segment pairs")
    for key, segments_for_pair in by_pair.items():
        kind = relationship(*key.split("|"))
        if kind not in ("PO", "Dup/MZ"):
            continue
        require(len(segments_for_pair) == len(chromosome_bounds), f"whole-chromosome segment count mismatch {key}")
        require({row["Chr"] for row in segments_for_pair} == set(chromosome_bounds), f"missing whole-chromosome segment {key}")
        for row in segments_for_pair:
            markers = chromosome_bounds[row["Chr"]]
            require(row["IBDType"] == ("IBD1" if kind == "PO" else "IBD2"), f"whole-chromosome IBD type mismatch {key}")
            require(row["StartSNP"] == markers[0][1] and row["StopSNP"] == markers[-1][1], f"whole-chromosome IBD bounds mismatch {key}")
    return {"relationships": len(pairs), "segments": len(segments), "wholeChromosomeInheritance": True}


def check_sample_qc(data, fixture):
    header, rows = table(data, "resultbySample.txt")
    require_columns(header, "FID IID FA MO SEX N_SNP Missing Heterozygosity N_pair N_MIp Err_MIp N_trio N_MIt Err_MIt MI_Removal".split(), "sample QC")
    samples = keyed(rows, ["IID"], "sample QC")
    require(set(samples) == {(key,) for key in fixture["fam"]}, "sample QC inventory mismatch")
    for (iid,), row in samples.items():
        truth = fixture["truth"]["sampleStats"][iid]
        require(exact_int(row["N_SNP"]) == truth["called"], f"sample called count mismatch {iid}")
        near(row["Missing"], truth["missing"] / fixture["truth"]["markerCount"], f"sample missingness/{iid}")
        near(row["Heterozygosity"], truth["heterozygous"] / truth["called"], f"sample heterozygosity/{iid}")
        for field in ("N_MIp", "N_MIt", "MI_Removal"):
            if field in row:
                require(exact_int(row[field]) == 0, f"unexpected Mendelian errors {iid}/{field}")
        fam = fixture["fam"][iid]
        require([row[field] for field in ("FID", "IID", "FA", "MO", "SEX")] == fam[:5], f"sample pedigree mismatch {iid}")
    return {"samples": len(samples), "calledCountsAndMissingness": True, "mendelianErrors": 0}


def check_snp_qc(data, fixture):
    header, rows = table(data, "resultbySNP.txt")
    require_columns(header, "SNP Chr Pos Label_A Label_a Freq_A N N_AA N_Aa N_aa CallRate N_PO N_errPO N_trio N_errTrio".split(), "SNP QC")
    markers = keyed(rows, ["SNP"], "SNP QC")
    require(set(markers) == {(row[1],) for row in fixture["bim"]}, "SNP QC inventory mismatch")
    bed = safe_path(fixture["base"], "cohort.bed").read_bytes()
    count = fixture["truth"]["sampleCount"]
    stride = (count + 3) // 4
    require(bed[:3] == b"\x6c\x1b\x01" and len(bed) == 3 + stride * len(fixture["bim"]), "invalid synthetic BED")
    for index, marker in enumerate(fixture["bim"]):
        row = markers[(marker[1],)]
        counts = [0, 0, 0, 0]
        offset = 3 + index * stride
        for sample in range(count):
            counts[(bed[offset + sample // 4] >> (2 * (sample % 4))) & 3] += 1
        called = count - counts[1]
        for field, expected in (("N", called), ("N_AA", counts[0]), ("N_Aa", counts[2]), ("N_aa", counts[3])):
            require(exact_int(row[field]) == expected, f"SNP genotype count mismatch {marker[1]}/{field}")
        require([row["Chr"], row["Pos"], row["Label_A"], row["Label_a"]] == [marker[0], marker[3], marker[4], marker[5]], f"SNP identity mismatch {marker[1]}")
        near(row["CallRate"], called / count, f"SNP call rate/{marker[1]}")
        near(row["Freq_A"], (2 * counts[0] + counts[2]) / (2 * called), f"SNP allele frequency/{marker[1]}")
        for field in ("N_errPO", "N_errTrio", "N_errMZ"):
            if field in row:
                require(exact_int(row[field]) == 0, f"unexpected SNP Mendelian errors {marker[1]}/{field}")
    return {"markers": len(markers), "directBedGenotypeCounts": True, "mendelianErrors": 0}


def check_pcs(data, fixture, mds=False):
    header, rows = table(data, "resultpc.txt")
    samples = keyed(rows, ["IID"], "principal components")
    require(set(samples) == {(key,) for key in fixture["fam"]}, "PC sample inventory mismatch")
    require([column for column in header if re.fullmatch(r"PC\d+", column)] == ["PC1", "PC2", "PC3"], "PC count mismatch")
    for row in samples.values():
        fam = fixture["fam"][row["IID"]]
        require([row[field] for field in ("FID", "IID", "FA", "MO", "SEX")] == fam[:5], "PC pedigree mismatch")
        require(row["AFF"] in ("1", "2"), "invalid PC reference/projection group")
    for column in ("PC1", "PC2", "PC3"):
        values = [finite(row[column], column) for row in rows]
        require(max(values) - min(values) > 0.001, f"constant PC {column}")
        near(samples[("CHILD1",)][column], finite(samples[("DUP",)][column]), "duplicate PC equality", 0.00011)
        reference = [finite(row[column]) for row in rows if row["AFF"] == "1"]
        require(len(reference) >= 3 and abs(sum(reference)) < len(reference) * 0.00005001, f"reference PC is not centered: {column}")
        if mds:
            require(abs(sum(value * value for value in reference) - 1) < 0.002, f"MDS reference PC norm mismatch: {column}")
    if mds:
        for first, second in itertools.combinations(("PC1", "PC2", "PC3"), 2):
            dot = sum(finite(row[first]) * finite(row[second]) for row in rows if row["AFF"] == "1")
            require(abs(dot) < 0.002, "MDS reference PCs are not orthogonal")
    return {"samples": len(samples), "components": 3, "duplicateCoordinates": True}


def check_unrelated(files, fixture):
    lists = []
    for name in ("resultunrelated.txt", "resultunrelated_toberemoved.txt"):
        rows = text_rows(files[name], name)
        require(rows and all(len(row) == 2 for row in rows), f"invalid unrelated list {name}")
        require(len({row[1] for row in rows}) == len(rows), f"duplicate unrelated IDs {name}")
        for fid, iid in rows:
            require(iid in fixture["fam"] and fid == fixture["fam"][iid][0], f"unknown unrelated ID {iid}")
        lists.append({row[1] for row in rows})
    selected, removed = lists
    require(not selected & removed and selected | removed == set(fixture["fam"]), "unrelated partition incomplete")
    require(fixture["truth"]["sampleCount"] - 4 <= len(selected) <= fixture["truth"]["sampleCount"] - 3, "unexpected unrelated selection size")
    for first, second in itertools.combinations(selected, 2):
        require(fixture["truth"]["pairStats"][pair_key(first, second)]["Kinship"] < 0.177, f"related pair retained as unrelated: {first}/{second}")
    return {"selected": len(selected), "removed": len(removed), "noFirstDegreeRelativesRetained": True}


def check_cluster(files, fixture):
    rows = text_rows(files["resultupdateids.txt"], "resultupdateids.txt")
    require(len(rows) == 5 and all(len(row) == 4 for row in rows), "invalid cluster ID update table")
    require({row[1] for row in rows} == FAMILY and len({row[2] for row in rows}) == 1, "cluster members disagree with synthetic pedigree")
    for old_fid, old_iid, new_fid, new_iid in rows:
        require(old_iid == new_iid and old_fid == fixture["fam"][old_iid][0], "cluster identity mismatch")
    expected = {pair_key(*pair) for pair in itertools.combinations(FAMILY, 2)}
    result = check_pair_table(files["resultcluster.kin"], "resultcluster.kin", fixture, expected, clustered=True, types=True)
    _, clustered_pairs = table(files["resultcluster.kin"], "resultcluster.kin")
    require({row["FID"] for row in clustered_pairs} == {rows[0][2]}, "cluster family IDs disagree with update table")
    result["clusterMembers"] = len(rows)
    return result


def check_contract(files, case, fixture):
    contract = case["contract"]
    stats = fixture["truth"]["pairStats"]
    within = {key for key, stat in stats.items() if stat["withinFamily"]}
    across = set(stats) - within
    checks = {}
    if contract in ("kinship", "kinship-filtered", "related"):
        checks["withinFamily"] = check_pair_table(files["result.kin"], "result.kin", fixture, within, types=contract == "related")
        if contract == "kinship-filtered":
            across = {key for key in across if stats[key]["Kinship"] >= 2 ** -2.5}
        elif contract == "related":
            across = {key for key in across if relationship(*stats[key]["IDs"]) != "UN"}
        checks["acrossFamily"] = check_pair_table(files["result.kin0"], "result.kin0", fixture, across, types=contract == "related")
    elif contract == "duplicate":
        header, rows = table(files["result.con"], "result.con")
        require_columns(header, "FID1 ID1 FID2 ID2 N N_IBS0 N_IBS1 N_IBS2 Concord HomConc HetConc".split(), "duplicate concordance")
        pairs = pair_rows(rows, "duplicate concordance")
        require(set(pairs) == {"CHILD1|DUP"}, "duplicate inventory mismatch")
        row = pairs["CHILD1|DUP"]
        require(row["FID1"] == fixture["fam"][row["ID1"]][0] and row["FID2"] == fixture["fam"][row["ID2"]][0], "duplicate family identity mismatch")
        for field, expected in (("N", fixture["truth"]["markerCount"]), ("N_IBS0", 0), ("N_IBS1", 0), ("N_IBS2", fixture["truth"]["markerCount"])):
            require(exact_int(row[field]) == expected, f"duplicate count mismatch {field}")
        for field in ("Concord", "HomConc", "HetConc"):
            near(row[field], 1, f"duplicate {field}", 0)
        checks["duplicate"] = {"pairs": 1, "completeConcordance": True}
    elif contract == "ibs":
        checks["withinFamily"] = check_pair_table(files["result.ibs"], "result.ibs", fixture, within, ibs=True)
        checks["acrossFamily"] = check_pair_table(files["result.ibs0"], "result.ibs0", fixture, across, ibs=True)
    elif contract == "sample-qc":
        checks["sampleQc"] = check_sample_qc(files["resultbySample.txt"], fixture)
    elif contract == "snp-qc":
        checks["snpQc"] = check_snp_qc(files["resultbySNP.txt"], fixture)
    elif contract == "pcs":
        checks["principalComponents"] = check_pcs(files["resultpc.txt"], fixture, mds="result_Dist.txt" in files)
    elif contract == "unrelated":
        checks["unrelated"] = check_unrelated(files, fixture)
    elif contract == "cluster":
        checks["cluster"] = check_cluster(files, fixture)
    elif contract != "ibdseg":
        raise ValueError(f"unknown contract: {contract}")
    if "resultallsegs.txt" in files:
        checks["usableSegments"] = check_allsegs(files["resultallsegs.txt"], fixture)
    if "result.seg" in files:
        checks["ibdSegments"] = check_ibd(files, fixture)
    return checks


def numeric_equal(first, second):
    if first == second:
        return True
    if not NUMBER.fullmatch(first) or not NUMBER.fullmatch(second):
        return False
    if INTEGER.fullmatch(first) and INTEGER.fullmatch(second):
        return int(first) == int(second)
    # At most one unit of the least precise printed decimal is accepted;
    # biological truth uses a stricter half-unit rounding tolerance.
    def quantum(value):
        mantissa, *exponent = value.lower().split("e")
        decimals = len(mantissa.split(".", 1)[1]) if "." in mantissa else 0
        return 10.0 ** ((int(exponent[0]) if exponent else 0) - decimals)
    tolerance = min(0.001001, max(quantum(first), quantum(second)) * 1.001)
    return abs(finite(first) - finite(second)) <= tolerance


def compare_pcs(first, second):
    head_a, rows_a = table(first, "resultpc.txt")
    head_b, rows_b = table(second, "resultpc.txt")
    require(head_a == head_b, "PC headers differ")
    a = keyed(rows_a, ["FID", "IID"], "native PCs")
    b = keyed(rows_b, ["FID", "IID"], "actual PCs")
    require(set(a) == set(b), "PC sample identities differ")
    signs = {}
    for column in ("PC1", "PC2", "PC3"):
        dot = sum(finite(a[key][column]) * finite(b[key][column]) for key in a)
        signs[column] = -1 if dot < 0 else 1
        for key in a:
            require(abs(finite(a[key][column]) - signs[column] * finite(b[key][column])) <= 0.00011001, f"PC numeric difference {key}/{column}")
    for key in a:
        for column in head_a:
            if column not in signs:
                require(a[key][column] == b[key][column], f"PC metadata differs {key}/{column}")
    return {"records": len(a), "signAlignment": signs, "absoluteTolerance": 0.00011001}


def compare_artifact(first, second, name):
    if name == "resultpc.txt":
        return compare_pcs(first, second)
    a, b = text_rows(first, name), text_rows(second, name)
    require(len(a) == len(b), f"row count differs: {name}")
    # Row order is not scientific information; all cells, including IDs,
    # chromosome coordinates, counts and headers remain in the comparison.
    a.sort(); b.sort()
    for index, (row_a, row_b) in enumerate(zip(a, b)):
        require(len(row_a) == len(row_b), f"row width differs: {name}/{index}")
        for x, y in zip(row_a, row_b):
            require(numeric_equal(x, y), f"artifact differs: {name}/{index}: {x!r} != {y!r}")
    return {"recordsIncludingHeader": len(a), "decompressed": name.endswith(".gz")}


def capture(directory, outputs):
    files = {name: safe_path(directory, name).read_bytes() for name in outputs}
    hashes = {name: sha(data) for name, data in files.items()}
    sizes = {name: len(data) for name, data in files.items()}
    execution = json.loads(safe_path(directory, "execution.json").read_text())
    require(type(execution.get("exitCode")) is int, "missing execution exitCode")
    require(execution.get("signal") is None, "execution terminated by signal")
    require(set(execution.get("files", {})) == set(outputs) and set(execution.get("fileBytes", {})) == set(outputs), "captured output inventory mismatch")
    for name in outputs:
        require(execution["files"][name] == hashes[name], f"captured hash mismatch: {name}")
        require(type(execution["fileBytes"][name]) is int and execution["fileBytes"][name] == sizes[name], f"captured byte count mismatch: {name}")
    for stream in ("stdout", "stderr"):
        data = safe_path(directory, stream + ".txt").read_bytes()
        require(execution.get(stream) == sha(data), f"captured {stream} hash mismatch")
    return files, hashes, sizes, execution


def compare(expected, actual, case, fixture):
    outputs = case["outputs"]
    require(len(outputs) == len(set(outputs)), "duplicate requested outputs")
    a, hashes_a, sizes_a, execution_a = capture(expected, outputs)
    b, hashes_b, sizes_b, execution_b = capture(actual, outputs)
    result = {"schema": 1, "case": case["id"], "equal": False,
              "expectedFiles": hashes_a, "actualFiles": hashes_b,
              "expectedFileBytes": sizes_a, "actualFileBytes": sizes_b}
    if case["contract"] == "failure":
        require(execution_a["exitCode"] != 0 and execution_b["exitCode"] != 0, "expected failure exited successfully")
        require((execution_a["exitCode"] & 255) == (execution_b["exitCode"] & 255), "failure exit codes differ")
        for directory in (expected, actual):
            diagnostic = (directory / "stdout.txt").read_text() + (directory / "stderr.txt").read_text()
            require(re.search(case["diagnostic"], diagnostic, re.I), "expected failure diagnostic absent")
        result["checks"] = {"native": {"expectedFailure": True}, "actual": {"expectedFailure": True}}
    else:
        for execution in (execution_a, execution_b):
            require(execution["exitCode"] == 0 and not execution.get("missingOutputs"), "scientific execution failed")
        result["checks"] = {"native": check_contract(a, case, fixture), "actual": check_contract(b, case, fixture)}
        result["comparisons"] = {name: compare_artifact(a[name], b[name], name) for name in outputs}
    result["equal"] = True
    result["biologicalDigest"] = sha(json.dumps(result["checks"]["actual"], sort_keys=True, separators=(",", ":")).encode())
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("expected", type=Path)
    parser.add_argument("actual", type=Path)
    parser.add_argument("--case", required=True)
    parser.add_argument("--dataset", choices=("main", "small"), default="main")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    try:
        plan = json.loads((ROOT / "validation/king/cases.json").read_text())
        cases = [case for case in plan["cases"] if case["id"] == args.case]
        require(len(cases) == 1, "unknown or duplicate case")
        fixture = load_fixture(args.dataset)
        result = compare(args.expected, args.actual, cases[0], fixture)
    except (OSError, ValueError, KeyError, TypeError, IndexError, OverflowError) as error:
        result = {"schema": 1, "case": args.case, "equal": False, "error": str(error)}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"case": args.case, "equal": result["equal"], "error": result.get("error")}))
    return 0 if result["equal"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
