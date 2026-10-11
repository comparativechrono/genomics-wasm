#!/usr/bin/env python3
"""Compare complete transcript quantification tables and run accounting.

No genes are filtered, no abundance cutoff is applied, and zero stays zero.
Only explicitly named wall-clock, command-line and memory-use metadata is
excluded. Floating-point fields are exact unless a reviewed policy says otherwise.
"""

from __future__ import annotations

import argparse
import csv
from decimal import Decimal, InvalidOperation, localcontext
import json
import math
from pathlib import Path
import re

NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z")
INTEGER = re.compile(r"(?:0|[1-9][0-9]*)\Z")
SCHEMAS = {
    "kallisto": {"table": "abundance.tsv", "metadata": "run_info.json",
        "columns": ("target_id", "length", "eff_length", "est_counts", "tpm"),
        "version_key": "kallisto_version", "version": "0.52.0",
        "targets": "n_targets", "processed": "n_processed", "mapped": "n_pseudoaligned",
        "counts": "est_counts", "tpm": "tpm", "effective": "eff_length",
        "required_ints": ("n_targets", "n_bootstraps", "n_processed", "n_pseudoaligned", "n_unique", "index_version", "k-mer length"),
        "required_numbers": ("p_pseudoaligned", "p_unique"),
        "volatile": {"start_time": "Wall-clock start time", "call": "Executable path and invocation"}},
    "salmon": {"table": "quant.sf", "metadata": "aux_info/meta_info.json",
        "columns": ("Name", "Length", "EffectiveLength", "TPM", "NumReads"),
        "version_key": "salmon_version", "version": "2.8.0",
        "targets": "num_valid_targets", "processed": "num_processed", "mapped": "num_mapped",
        "counts": "NumReads", "tpm": "TPM", "effective": "EffectiveLength",
        "required_ints": ("num_valid_targets", "num_processed", "num_mapped", "num_eq_classes", "num_bootstraps", "num_em_iterations"),
        "required_numbers": ("percent_mapped", "frag_length_mean", "frag_length_sd"),
        "volatile": {"start_time": "Wall-clock start time", "end_time": "Wall-clock end time",
            "total_time_seconds": "Elapsed wall-clock time", "peak_rss_kb": "Platform-specific peak memory use"}},
}
# The allowlist prevents a policy from making identifiers, lengths, counts of
# input fragments, convergence flags or arbitrary metadata approximate.
NUMERIC_METADATA = {
    "kallisto": {"p_pseudoaligned", "p_unique"},
    "salmon": {"percent_mapped", "frag_length_mean", "frag_length_sd", "inference_truncated_mass"},
}
LIBRARY_FORMATS = ("IU", "ISF", "ISR", "OU", "OSF", "OSR", "MU", "MSF", "MSR", "U", "SF", "SR")
NUMERIC_LIBRARY = {"compatible_fragment_ratio", "strand_mapping_bias"}


def number(value, label):
    if not isinstance(value, str) or not NUMBER.fullmatch(value):
        raise ValueError(f"{label}: invalid decimal number {value!r}")
    result = Decimal(value)
    if not result.is_finite() or not math.isfinite(float(result)):
        raise ValueError(f"{label}: nonfinite or out-of-range value")
    return result


def load_transcripts(path):
    """Read the independent FASTA identity/length contract, not a tool index."""
    lengths, identity = {}, None
    for line_no, raw in enumerate(Path(path).read_text().splitlines(), 1):
        if raw.startswith(">"):
            fields = raw[1:].split()
            if not fields or fields[0] in lengths:
                raise ValueError(f"{path}:{line_no}: missing or duplicate transcript identifier")
            identity = fields[0]
            lengths[identity] = 0
        elif identity is None or not re.fullmatch(r"[ACGTNacgtn]+", raw):
            raise ValueError(f"{path}:{line_no}: invalid transcript FASTA")
        else:
            lengths[identity] += len(raw)
    if not lengths or min(lengths.values()) <= 0:
        raise ValueError(f"{path}: no transcripts or empty transcript")
    return lengths


def load_table(path, tool, transcripts):
    schema = SCHEMAS[tool]
    with Path(path).open(newline="", encoding="utf-8") as stream:
        reader = csv.reader(stream, delimiter="\t", strict=True)
        if next(reader, None) != list(schema["columns"]):
            raise ValueError(f"{path}: incorrect column names or order")
        rows = {}
        for line_no, row in enumerate(reader, 2):
            label = f"{path}:{line_no}"
            if len(row) != 5:
                raise ValueError(f"{label}: expected five columns")
            identity = row[0]
            if not identity or any(c.isspace() or ord(c) < 32 for c in identity) or identity in rows:
                raise ValueError(f"{label}: missing, invalid or duplicate transcript identifier")
            if not INTEGER.fullmatch(row[1]) or int(row[1]) <= 0:
                raise ValueError(f"{label}: invalid transcript length")
            if identity not in transcripts or int(row[1]) != transcripts[identity]:
                raise ValueError(f"{label}: transcript identifier/length does not match FASTA")
            values = {schema["columns"][1]: int(row[1])}
            for column, text in zip(schema["columns"][2:], row[2:]):
                value = number(text, f"{label}:{column}")
                if value < 0 or (column == schema["effective"] and value <= 0):
                    raise ValueError(f"{label}:{column}: negative abundance or nonpositive effective length")
                values[column] = value
            rows[identity] = values
    if set(rows) != set(transcripts):
        raise ValueError(f"{path}: transcript set differs from FASTA; missing {sorted(set(transcripts)-set(rows))[:10]}")
    return rows


def unique_object(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def load_json(path):
    def reject_constant(value):
        raise ValueError(f"Nonfinite JSON value: {value}")
    result = json.loads(Path(path).read_text(), object_pairs_hook=unique_object,
                        parse_constant=reject_constant, parse_float=Decimal)
    def validate(value):
        if isinstance(value, Decimal) and (not value.is_finite() or not math.isfinite(float(value))):
            raise ValueError("Nonfinite or out-of-range JSON number")
        if isinstance(value, dict):
            for item in value.values():
                validate(item)
        elif isinstance(value, list):
            for item in value:
                validate(item)
    validate(result)
    return result


def load_metadata(path, tool, transcripts, processed):
    meta, schema = load_json(path), SCHEMAS[tool]
    if not isinstance(meta, dict):
        raise ValueError(f"{path}: metadata must be an object")
    for field in schema["required_ints"]:
        if type(meta.get(field)) is not int or meta[field] < 0:
            raise ValueError(f"{path}: missing or invalid integer metadata {field}")
    for field in schema["required_numbers"]:
        value = meta.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, Decimal)) or not Decimal(value).is_finite() or value < 0:
            raise ValueError(f"{path}: missing or invalid numeric metadata {field}")
    if meta.get(schema["version_key"]) != schema["version"]:
        raise ValueError(f"{path}: expected {tool} {schema['version']}")
    if meta[schema["targets"]] != len(transcripts):
        raise ValueError(f"{path}: target count does not match FASTA")
    if meta[schema["processed"]] != processed:
        raise ValueError(f"{path}: expected {processed} processed fragments, got {meta[schema['processed']]}")
    if meta[schema["mapped"]] > processed:
        raise ValueError(f"{path}: mapped count exceeds processed fragments")
    if tool == "kallisto" and meta["n_unique"] > meta["n_pseudoaligned"]:
        raise ValueError(f"{path}: unique count exceeds pseudoaligned fragments")
    for field in schema["volatile"]:
        if field not in meta:
            raise ValueError(f"{path}: missing provenance metadata {field}")
        if field in {"start_time", "end_time", "call"}:
            if not isinstance(meta[field], str) or not meta[field]:
                raise ValueError(f"{path}: invalid provenance metadata {field}")
        elif isinstance(meta[field], bool) or not isinstance(meta[field], (int, Decimal)) or meta[field] < 0:
            raise ValueError(f"{path}: invalid timing/memory metadata {field}")
    if tool == "salmon":
        if meta.get("quant_errors") != []:
            raise ValueError(f"{path}: quantification reported errors")
        if type(meta.get("em_converged")) is not bool or not isinstance(meta.get("library_types"), list):
            raise ValueError(f"{path}: missing convergence or library metadata")
    return meta


def load_library_counts(path, metadata):
    data = load_json(path)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: library counts must be an object")
    counts = (*LIBRARY_FORMATS, "num_compatible_fragments", "num_incompatible_fragments",
        "num_assigned_fragments", "num_frags_with_concordant_consistent_mappings",
        "num_frags_with_inconsistent_or_orphan_mappings")
    for field in counts:
        if type(data.get(field)) is not int or data[field] < 0:
            raise ValueError(f"{path}: missing or invalid library count {field}")
    for field in NUMERIC_LIBRARY:
        value = data.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, Decimal)) or not 0 <= value <= 1:
            raise ValueError(f"{path}: missing or invalid library ratio {field}")
    if data["num_assigned_fragments"] != metadata["num_mapped"]:
        raise ValueError(f"{path}: assigned fragments disagree with meta_info.json")
    if metadata["library_types"] != [data.get("expected_format")]:
        raise ValueError(f"{path}: library format disagrees with meta_info.json")
    if not isinstance(data.get("read_files"), list) or not data["read_files"] or any(not isinstance(item, str) or not item or "\0" in item for item in data["read_files"]):
        raise ValueError(f"{path}: missing or invalid read file provenance")
    return data


def load_policy(path=None):
    policy = load_json(path) if path else {"schema": 1, "fields": {}}
    if not isinstance(policy, dict) or set(policy) != {"schema", "fields"} or policy["schema"] != 1 or not isinstance(policy["fields"], dict):
        raise ValueError("Invalid tolerance policy")
    for field, allowance in policy["fields"].items():
        parts = field.split(":")
        if len(parts) != 3 or parts[0] not in SCHEMAS:
            raise ValueError(f"Unknown tolerance field: {field}")
        tool, kind, column = parts
        allowed = (SCHEMAS[tool]["columns"][2:] if kind == "table" else
            NUMERIC_METADATA[tool] if kind == "metadata" else
            NUMERIC_LIBRARY if kind == "library" and tool == "salmon" else ())
        if column not in allowed:
            raise ValueError(f"Tolerance cannot apply to exact field: {field}")
        if not isinstance(allowance, dict) or set(allowance) != {"absolute", "relative", "reason", "evidence"}:
            raise ValueError(f"Tolerance requires limits, reason and evidence: {field}")
        for bound in ("absolute", "relative"):
            value = allowance[bound]
            if isinstance(value, bool) or not isinstance(value, (int, Decimal)) or not Decimal(value).is_finite() or value < 0:
                raise ValueError(f"Invalid {bound} tolerance: {field}")
        if any(not isinstance(allowance[key], str) or not allowance[key].strip() for key in ("reason", "evidence")):
            raise ValueError(f"Tolerance must document reason and evidence: {field}")
    return policy


class Comparison:
    def __init__(self, tool, policy, max_examples=20):
        self.tool, self.policy, self.max_examples = tool, policy, max_examples
        self.report = {"equal": True, "differenceCount": 0, "differences": [], "numeric": {}}

    def difference(self, field, expected, actual):
        self.report["equal"] = False
        self.report["differenceCount"] += 1
        if len(self.report["differences"]) < self.max_examples:
            self.report["differences"].append({"field": field, "expected": expected, "actual": actual})

    def numeric(self, field, key, expected, actual):
        a, b = Decimal(expected), Decimal(actual)
        allowance = self.policy["fields"].get(f"{self.tool}:{key}", {})
        absolute, relative = Decimal(allowance.get("absolute", 0)), Decimal(allowance.get("relative", 0))
        with localcontext() as context:
            context.prec = 80
            delta, scale = abs(a-b), max(abs(a), abs(b))
            ratio = delta/scale if scale else Decimal(0)
            metric = self.report["numeric"].setdefault(key, {"values": 0, "changed": 0,
                "maxAbsoluteDelta": Decimal(0), "maxRelativeDelta": Decimal(0),
                "absoluteTolerance": absolute, "relativeTolerance": relative})
            metric["values"] += 1
            metric["changed"] += a != b
            metric["maxAbsoluteDelta"] = max(metric["maxAbsoluteDelta"], delta)
            metric["maxRelativeDelta"] = max(metric["maxRelativeDelta"], ratio)
            if (a == 0) != (b == 0) or (a < 0) != (b < 0) or delta > absolute + relative*scale:
                self.difference(field, a, b)

    def metadata(self, a, b, path="", section="metadata"):
        if isinstance(a, dict) and isinstance(b, dict):
            if set(a) != set(b):
                self.difference(f"{section}:{path}:keys", sorted(a), sorted(b))
            for key in sorted(set(a) & set(b)):
                if section == "metadata" and not path and key in SCHEMAS[self.tool]["volatile"]:
                    continue
                self.metadata(a[key], b[key], f"{path}.{key}" if path else key, section)
        elif isinstance(a, list) and isinstance(b, list):
            if len(a) != len(b):
                self.difference(f"{section}:{path}:length", len(a), len(b))
            for index, (left, right) in enumerate(zip(a, b)):
                self.metadata(left, right, f"{path}[{index}]", section)
        elif path in (NUMERIC_METADATA[self.tool] if section == "metadata" else NUMERIC_LIBRARY) and not isinstance(a, bool) and not isinstance(b, bool) and isinstance(a, (int, Decimal)) and isinstance(b, (int, Decimal)):
            self.numeric(f"{section}:{path}", f"{section}:{path}", a, b)
        elif type(a) is not type(b) or a != b:
            self.difference(f"{section}:{path}", a, b)


def compare_directories(expected, actual, tool, transcripts, processed, policy=None):
    schema, policy = SCHEMAS[tool], policy or load_policy()
    reference = load_transcripts(transcripts) if not isinstance(transcripts, dict) else transcripts
    a = load_table(Path(expected) / schema["table"], tool, reference)
    b = load_table(Path(actual) / schema["table"], tool, reference)
    ameta = load_metadata(Path(expected) / schema["metadata"], tool, reference, processed)
    bmeta = load_metadata(Path(actual) / schema["metadata"], tool, reference, processed)
    comparison = Comparison(tool, policy)
    for identity in a:
        for column in schema["columns"][2:]:
            comparison.numeric(f"{identity}:{column}", f"table:{column}", a[identity][column], b[identity][column])
    comparison.metadata(ameta, bmeta)
    if tool == "salmon":
        alib = load_library_counts(Path(expected) / "lib_format_counts.json", ameta)
        blib = load_library_counts(Path(actual) / "lib_format_counts.json", bmeta)
        comparison.metadata(alib, blib, section="library")
    return {"schema": 1, "tool": tool, "transcripts": len(reference), "processedFragments": processed,
        "mappedFragments": ameta[schema["mapped"]],
        "rowOrderChanged": list(a) != list(b), "policy": policy,
        "excludedMetadata": schema["volatile"], **comparison.report}


def json_default(value):
    if isinstance(value, Decimal):
        # Preserve tiny deltas rather than rounding them to a float zero.
        return str(value)
    raise TypeError(type(value).__name__)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tool", choices=SCHEMAS)
    parser.add_argument("expected")
    parser.add_argument("actual")
    parser.add_argument("--transcripts", required=True)
    parser.add_argument("--processed", type=int, required=True)
    parser.add_argument("--policy")
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    try:
        if args.processed < 0:
            raise ValueError("Processed fragments cannot be negative")
        result = compare_directories(args.expected, args.actual, args.tool, args.transcripts, args.processed, load_policy(args.policy))
        status = 0 if result["equal"] else 1
    except (OSError, ValueError, csv.Error, InvalidOperation) as error:
        result, status = {"schema": 1, "tool": args.tool, "equal": False, "error": str(error)}, 2
    output = json.dumps(result, default=json_default, indent=2, allow_nan=False) + "\n"
    Path(args.report).write_text(output)
    print(output, end="")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
