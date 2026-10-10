#!/usr/bin/env python3
"""Compare complete DESeq2 tables, retaining row order and missing-value states."""
import argparse
import csv
import json
import math
from pathlib import Path
import re

SAMPLES = ["SRR1039508", "SRR1039509", "SRR1039512", "SRR1039513",
           "SRR1039516", "SRR1039517", "SRR1039520", "SRR1039521"]
RESULT = ["baseMean", "log2FoldChange", "lfcSE", "stat", "pvalue", "padj"]
SCHEMAS = {
    "versions.tsv": ("package", 44, {"version": "text"}),
    "samples.tsv": ("sample_id", 8, {"cell": "text", "dex": "text"}),
    "selected-counts.tsv": ("gene_id", 2100, dict.fromkeys(SAMPLES, "integer")),
    "size-factors.tsv": ("sample_id", 8, {"size_factor": "number"}),
    "normalized-counts.tsv": ("gene_id", 2100, dict.fromkeys(SAMPLES, "number")),
    "dispersions.tsv": ("gene_id", 2100, {
        "baseMean": "number", "baseVar": "number", "allZero": "boolean",
        "dispGeneEst": "number", "dispFit": "number", "dispersion": "number",
        "dispOutlier": "boolean", "betaConv": "boolean", "dispMAPConverged": "boolean"}),
    **{name: ("gene_id", 2100, dict.fromkeys(RESULT, "number")) for name in
       ["wald.tsv", "wald-unfiltered.tsv", "wald-reverse.tsv", "lrt.tsv"]},
    "shrink-normal.tsv": ("gene_id", 2100, dict.fromkeys(RESULT, "number")),
    "vst.tsv": ("gene_id", 2100, dict.fromkeys(SAMPLES, "number")),
}
SCHEMAS["local-dispersions.tsv"] = SCHEMAS["dispersions.tsv"]
SCHEMAS["local-wald.tsv"] = SCHEMAS["wald.tsv"]
NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?\Z")
SIGNIFICANCE_THRESHOLDS = (0.001, 0.01, 0.05, 0.1)


def load_table(path, schema):
    id_name, row_count, fields = schema
    with Path(path).open(newline="", encoding="utf-8") as stream:
        rows = list(csv.reader(stream, delimiter="\t", strict=True))
    if not rows or rows.pop(0) != [id_name, *fields]:
        raise ValueError(f"{path}: incorrect column names or order")
    if row_count is not None and len(rows) != row_count:
        raise ValueError(f"{path}: expected {row_count} rows, got {len(rows)}")
    seen = set()
    for line, row in enumerate(rows, 2):
        if len(row) != len(fields) + 1:
            raise ValueError(f"{path}:{line}: incorrect column count")
        if not row[0] or row[0] in seen:
            raise ValueError(f"{path}:{line}: missing or duplicate row identifier")
        seen.add(row[0])
        for (field, kind), value in zip(fields.items(), row[1:]):
            valid = bool(value)
            if kind == "number":
                valid = value == "NA" or (bool(NUMBER.fullmatch(value)) and math.isfinite(float(value)))
            elif kind == "integer":
                valid = bool(re.fullmatch(r"0|[1-9]\d*", value))
            elif kind == "boolean":
                valid = value in {"TRUE", "FALSE", "NA"}
            if not valid:
                raise ValueError(f"{path}:{line}: invalid {kind} in {field}: {value!r}")
    return rows


def load_policy(path=None):
    policy = json.loads(Path(path).read_text()) if path else {
        "schema": 1, "default": {"absolute": 0, "relative": 0}, "columns": {}}
    if policy.get("schema") != 1 or not isinstance(policy.get("columns"), dict):
        raise ValueError("Invalid tolerance policy")
    for key, tolerance in [("default", policy.get("default")), *policy["columns"].items()]:
        if not isinstance(tolerance, dict) or set(tolerance) != {"absolute", "relative"}:
            raise ValueError(f"Invalid tolerance: {key}")
        for value in tolerance.values():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError(f"Invalid tolerance: {key}")
        if key != "default":
            filename, separator, column = key.partition(":")
            if not separator or filename not in SCHEMAS:
                raise ValueError(f"Unknown tolerance field: {key}")
            fields = SCHEMAS[filename][2]
            if column == "*":
                if not all(kind == "number" for kind in fields.values()):
                    raise ValueError(f"Wildcard covers nonnumeric fields: {key}")
            elif fields.get(column) != "number":
                raise ValueError(f"Tolerance requires a floating-point field: {key}")
    return policy


def compare_tables(expected, actual, schema, filename, policy, max_examples=10):
    """Both files are validated before comparing. No sorting or NA substitution."""
    left, right = load_table(expected, schema), load_table(actual, schema)
    report = {"rows": len(left), "equal": True, "differenceCount": 0,
              "differences": [], "numeric": {}}

    def difference(row_id, column, a, b):
        report["equal"] = False
        report["differenceCount"] += 1
        if len(report["differences"]) < max_examples:
            report["differences"].append({"row": row_id, "column": column, "native": a, "wasm": b})

    if len(left) != len(right):
        difference("", "row-count", len(left), len(right))
    for arow, brow in zip(left, right):
        if arow[0] != brow[0]:
            difference(arow[0], schema[0], arow[0], brow[0])
        for (column, kind), a, b in zip(schema[2].items(), arow[1:], brow[1:]):
            if kind != "number" or a == "NA" or b == "NA":
                if a != b:
                    difference(arow[0], column, a, b)
                continue
            avalue, bvalue = float(a), float(b)
            if column == "padj":
                for threshold in SIGNIFICANCE_THRESHOLDS:
                    if (avalue < threshold) != (bvalue < threshold):
                        difference(arow[0], f"padj<{threshold}", a, b)
            delta = abs(avalue - bvalue)
            scale = max(abs(avalue), abs(bvalue))
            relative = delta / scale if scale else 0.0
            tolerance = policy["columns"].get(f"{filename}:{column}",
                         policy["columns"].get(f"{filename}:*", policy["default"]))
            metric = report["numeric"].setdefault(column, {
                "values": 0, "changed": 0, "maxAbsoluteDelta": 0,
                "maxRelativeDelta": 0, "tolerance": tolerance})
            metric["values"] += 1
            metric["changed"] += delta != 0
            metric["maxAbsoluteDelta"] = max(metric["maxAbsoluteDelta"], delta)
            metric["maxRelativeDelta"] = max(metric["maxRelativeDelta"], relative)
            # A tolerance never excuses a sign change or zero becoming nonzero.
            sign_changed = (avalue == 0) != (bvalue == 0) or (avalue < 0) != (bvalue < 0)
            if sign_changed or delta > tolerance["absolute"] + tolerance["relative"] * scale:
                difference(arow[0], column, a, b)
    return report


def compare_directories(expected, actual, policy=None):
    policy = policy or load_policy()
    expected, actual = Path(expected), Path(actual)
    allowed = {*SCHEMAS, "session-info.txt", "completed.txt"}
    for directory in [expected, actual]:
        names = {file.name for file in directory.iterdir()}
        if names != allowed:
            raise ValueError(f"{directory}: missing {sorted(allowed-names)}, unexpected {sorted(names-allowed)}")
        if (directory / "completed.txt").read_text() != "completed\n":
            raise ValueError(f"{directory}: analysis did not finish")
    tables = {name: compare_tables(expected / name, actual / name, schema, name, policy)
              for name, schema in SCHEMAS.items()}
    return {"schema": 1, "equal": all(table["equal"] for table in tables.values()),
            "policy": policy, "tables": tables,
            "exactAdjustedPvalueDecisions": list(SIGNIFICANCE_THRESHOLDS),
            "excluded": {"session-info.txt": "Platform-specific diagnostic retained with raw results."}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("native")
    parser.add_argument("wasm")
    parser.add_argument("--policy")
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    try:
        report = compare_directories(args.native, args.wasm, load_policy(args.policy))
        status = 0 if report["equal"] else 1
    except (ValueError, OSError, csv.Error, json.JSONDecodeError) as error:
        report, status = {"schema": 1, "equal": False, "error": str(error)}, 2
    content = json.dumps(report, indent=2, allow_nan=False) + "\n"
    Path(args.report).write_text(content)
    print(content, end="")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
