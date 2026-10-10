#!/usr/bin/env python3
"""Compare SAM or VCF results without losing raw alignment/variant information.

See validation/README.md for the exact comparison policy. This parser rejects
common malformed output, but is not a replacement for a complete format validator.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, MAX_EMAX, MIN_EMIN, localcontext
import json
import math
from pathlib import Path
import re
import sys
from typing import Callable, TextIO


DEFAULT_MAX_DIFFERENCES = 20
MAX_PREVIEW_CHARS = 1000
INTEGER = re.compile(r"[+-]?[0-9]+\Z")
UNSIGNED = re.compile(r"[0-9]+\Z")
FLOAT = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z")
TAG = re.compile(r"[A-Za-z][A-Za-z0-9]\Z")
CIGAR = re.compile(r"(?:[1-9][0-9]*[MIDNSHP=X])+\Z")
DNA = re.compile(r"[ACGTNacgtn]+\Z")
VCF_KEY = re.compile(r"[A-Za-z0-9_.]+\Z")
GENOTYPE = re.compile(r"(?:\.|[0-9]+)(?:[/|](?:\.|[0-9]+))*\Z")


class FormatError(ValueError):
    """An input could not be parsed safely for comparison."""


@dataclass
class Parsed:
    ordered_header: tuple
    metadata: Counter
    records: Counter


def _fail(line: int, message: str) -> None:
    raise FormatError(f"line {line}: {message}")


def _uint(value: str, line: int, label: str, maximum: int | None = None,
          minimum: int = 0) -> int:
    if not UNSIGNED.fullmatch(value):
        _fail(line, f"{label} must be an unsigned integer")
    number = int(value)
    if number < minimum or (maximum is not None and number > maximum):
        _fail(line, f"{label} is out of range")
    return number


def _float(value: str, line: int, label: str) -> float:
    if not FLOAT.fullmatch(value):
        _fail(line, f"{label} is not a finite decimal number")
    number = float(value)
    if not math.isfinite(number):
        _fail(line, f"{label} is not finite")
    return number


def _no_space(value: str, line: int, label: str) -> None:
    if not value or any(c.isspace() or ord(c) < 32 for c in value):
        _fail(line, f"empty or invalid {label}")


def _sam_optional(field: str, line: int) -> tuple[str, str, str]:
    pieces = field.split(":", 2)
    if len(pieces) != 3 or not TAG.fullmatch(pieces[0]):
        _fail(line, "invalid SAM optional field")
    key, kind, value = pieces
    if kind == "A":
        if len(value) != 1 or not 33 <= ord(value) <= 126:
            _fail(line, f"invalid A value for {key}")
    elif kind == "i":
        if not INTEGER.fullmatch(value) or not -(2**31) <= int(value) <= 2**32 - 1:
            _fail(line, f"invalid i value for {key}")
    elif kind == "f":
        _float(value, line, key)
    elif kind == "Z":
        if any(not 32 <= ord(c) <= 126 for c in value):
            _fail(line, f"invalid Z value for {key}")
    elif kind == "H":
        if len(value) % 2 or not re.fullmatch(r"[0-9A-F]*", value):
            _fail(line, f"invalid H value for {key}")
    elif kind == "B":
        items = value.split(",")
        limits = {"c": (-128, 127), "C": (0, 255), "s": (-32768, 32767),
                  "S": (0, 65535), "i": (-(2**31), 2**31 - 1), "I": (0, 2**32 - 1)}
        if len(items) < 2 or items[0] not in (*limits, "f"):
            _fail(line, f"invalid B array for {key}")
        for item in items[1:]:
            if items[0] == "f":
                _float(item, line, key)
            elif not INTEGER.fullmatch(item) or not limits[items[0]][0] <= int(item) <= limits[items[0]][1]:
                _fail(line, f"invalid B array element for {key}")
    else:
        _fail(line, f"unknown SAM optional type {kind!r}")
    return key, kind, value


def parse_sam(stream: TextIO) -> Parsed:
    metadata: Counter = Counter()
    records: Counter = Counter()
    sequences = []
    sequence_names: set[str] = set()
    identifiers: set[tuple[str, str]] = set()
    saw_record = False
    saw_hd = False
    for line, raw in enumerate(stream, 1):
        text = raw.rstrip("\r\n")
        if not text:
            _fail(line, "blank SAM line")
        columns = text.split("\t")
        if text.startswith("@"):
            if saw_record:
                _fail(line, "SAM header after alignment records")
            kind = columns[0]
            if kind == "@CO":
                if len(columns) < 2:
                    _fail(line, "SAM @CO requires comment text")
                metadata[(kind, text[4:])] += 1
                continue
            if kind not in ("@HD", "@SQ", "@RG", "@PG"):
                _fail(line, "unknown SAM header record")
            values = {}
            for field in columns[1:]:
                key, separator, value = field.partition(":")
                if not separator or not TAG.fullmatch(key) or not value or key in values:
                    _fail(line, "invalid or duplicate SAM header field")
                values[key] = value
            if kind == "@HD":
                if saw_hd or line != 1 or "VN" not in values:
                    _fail(line, "@HD must occur once, first, with VN")
                saw_hd = True
            elif kind == "@SQ":
                if "SN" not in values or "LN" not in values:
                    _fail(line, "@SQ requires SN and LN")
                _uint(values["LN"], line, "reference length", 2**31 - 1, 1)
                _no_space(values["SN"], line, "reference name")
                if values["SN"] in sequence_names:
                    _fail(line, "duplicate @SQ reference name")
                sequence_names.add(values["SN"])
                sequences.append(tuple(sorted(values.items())))
                continue
            else:
                if "ID" not in values:
                    _fail(line, f"{kind} requires ID")
                identity = (kind, values["ID"])
                if identity in identifiers:
                    _fail(line, f"duplicate {kind} ID")
                identifiers.add(identity)
                if kind == "@PG":
                    values.pop("CL", None)  # The only ignored SAM field.
            metadata[(kind, tuple(sorted(values.items())))] += 1
            continue
        saw_record = True
        if len(columns) < 11:
            _fail(line, "SAM alignment requires at least 11 tab-separated columns")
        name, flag, reference, position, mapq, cigar, mate_reference, mate_position, tlen, sequence, quality = columns[:11]
        if not 1 <= len(name) <= 254 or any(not (33 <= ord(c) <= 63 or 65 <= ord(c) <= 126) for c in name):
            _fail(line, "invalid SAM query name")
        _uint(flag, line, "FLAG", 65535)
        _uint(position, line, "POS", 2**31 - 1)
        _uint(mapq, line, "MAPQ", 255)
        _uint(mate_position, line, "PNEXT", 2**31 - 1)
        if not INTEGER.fullmatch(tlen) or not -(2**31) <= int(tlen) <= 2**31 - 1:
            _fail(line, "invalid TLEN")
        for value, label in ((reference, "RNAME"), (mate_reference, "RNEXT")):
            _no_space(value, line, label)
            if sequence_names and value not in ("*", "=") and value not in sequence_names:
                _fail(line, f"{label} is absent from @SQ")
        if reference == "=":
            _fail(line, "RNAME cannot be =")
        if cigar != "*" and not CIGAR.fullmatch(cigar):
            _fail(line, "invalid CIGAR")
        if sequence != "*" and not re.fullmatch(r"[A-Za-z=.]+", sequence):
            _fail(line, "invalid SEQ")
        if quality != "*":
            if sequence == "*" or len(quality) != len(sequence) or any(not 33 <= ord(c) <= 126 for c in quality):
                _fail(line, "QUAL must match SEQ length and contain printable ASCII")
        if cigar != "*" and sequence != "*":
            query_length = sum(int(n) for n, op in re.findall(r"([0-9]+)([MIDNSHP=X])", cigar) if op in "MIS=X")
            if query_length != len(sequence):
                _fail(line, "CIGAR query length does not match SEQ")
        optional = [_sam_optional(field, line) for field in columns[11:]]
        if len({field[0] for field in optional}) != len(optional):
            _fail(line, "duplicate SAM optional tag")
        records[(tuple(columns[:11]), tuple(sorted(optional)))] += 1
    if not sequences and not metadata and not records:
        raise FormatError("SAM input is empty")
    return Parsed(tuple(sequences), metadata, records)


def _structured_metadata(value: str, line: int) -> tuple:
    if not value.endswith(">"):
        _fail(line, "unterminated structured VCF metadata")
    parts, start, quoted, escaped = [], 1, False, False
    for index, char in enumerate(value[1:-1], 1):
        if escaped:
            escaped = False
        elif char == "\\" and quoted:
            escaped = True
        elif char == '"':
            quoted = not quoted
        elif char == "," and not quoted:
            parts.append(value[start:index])
            start = index + 1
    if quoted or escaped:
        _fail(line, "unterminated quote in VCF metadata")
    parts.append(value[start:-1])
    fields = {}
    for part in parts:
        key, separator, item = part.partition("=")
        if not separator or not key or not item or key in fields:
            _fail(line, "invalid or duplicate structured VCF metadata field")
        _no_space(key, line, "metadata key")
        if item.startswith('"'):
            if not re.fullmatch(r'"(?:[^"\\]|\\.)*"', item):
                _fail(line, "invalid quoted VCF metadata value")
        elif '"' in item or any(c.isspace() for c in item):
            _fail(line, "unquoted VCF metadata value contains a quote or whitespace")
        fields[key] = item
    return tuple(sorted(fields.items()))


def _vcf_definition(fields: dict, kind: str, line: int) -> tuple[str, str]:
    if "Number" not in fields or "Type" not in fields:
        _fail(line, f"{kind} requires Number and Type")
    number, value_type = fields["Number"], fields["Type"]
    if not (UNSIGNED.fullmatch(number) or number in ("A", "R", "G", ".")):
        _fail(line, f"invalid {kind} Number")
    allowed = ("Integer", "Float", "Character", "String", "Flag")
    if value_type not in allowed or (kind == "FORMAT" and value_type == "Flag"):
        _fail(line, f"invalid {kind} Type")
    if (value_type == "Flag") != (number == "0"):
        _fail(line, "Flag fields must have Number=0, and only flags may use Number=0")
    return number, value_type


def _vcf_value(value: str | None, definition: tuple[str, str] | None,
               alt_count: int, line: int, label: str, ploidy: int | None = None) -> None:
    if definition is None:
        return
    number, value_type = definition
    if value_type == "Flag":
        if value is not None:
            _fail(line, f"{label} Flag must not have a value")
        return
    if value is None:
        _fail(line, f"{label} requires a value")
    if value == ".":
        return
    items = value.split(",")
    expected = int(number) if UNSIGNED.fullmatch(number) else None
    if number == "A":
        expected = alt_count
    elif number == "R":
        expected = alt_count + 1
    elif number == "G" and ploidy is not None:
        expected = math.comb(alt_count + ploidy, ploidy)
    if expected is not None and len(items) != expected:
        _fail(line, f"{label} has {len(items)} values; expected {expected}")
    for item in items:
        if item == ".":
            continue
        if not item:
            _fail(line, f"{label} has an empty value")
        if value_type == "Integer" and not INTEGER.fullmatch(item):
            _fail(line, f"{label} requires integer values")
        if value_type == "Float":
            _float(item, line, label)
        if value_type == "Character" and len(item) != 1:
            _fail(line, f"{label} requires single-character values")


def _valid_alt(value: str) -> bool:
    if value == "*" or DNA.fullmatch(value) or re.fullmatch(r"<[A-Za-z0-9_.:-]+>", value):
        return True
    # Preserve breakends literally: never rewrite orientations or remote loci.
    if re.fullmatch(r"(?:[ACGTNacgtn]+\.|\.[ACGTNacgtn]+)", value):
        return True
    return bool(re.fullmatch(r"(?:[ACGTNacgtn]*)([\[\]])[^\s\[\]:]+:[1-9][0-9]*\1(?:[ACGTNacgtn]*)", value))


def parse_vcf(stream: TextIO) -> Parsed:
    metadata: Counter = Counter()
    records: Counter = Counter()
    contigs = []
    identifiers: set[tuple[str, str]] = set()
    definitions: dict[str, dict[str, tuple[str, str]]] = {"INFO": {}, "FORMAT": {}}
    header = None
    saw_fileformat = False
    ignored: set[str] = set()
    for line, raw in enumerate(stream, 1):
        text = raw.rstrip("\r\n")
        if not text:
            _fail(line, "blank VCF line")
        if text.startswith("##"):
            if header is not None:
                _fail(line, "VCF metadata after column header")
            key, separator, value = text[2:].partition("=")
            if not separator or not key or not value:
                _fail(line, "invalid VCF metadata")
            _no_space(key, line, "metadata key")
            if key == "fileformat":
                if saw_fileformat or line != 1 or not re.fullmatch(r"VCFv4\.[0-9]+", value):
                    _fail(line, "VCF requires one first-line VCFv4.x fileformat declaration")
                saw_fileformat = True
            elif line == 1:
                _fail(line, "first VCF line must declare fileformat")
            if key in ("fileDate", "commandline"):
                if key in ignored:
                    _fail(line, f"duplicate VCF {key} metadata")
                ignored.add(key)
                continue  # The only ignored VCF metadata keys.
            item = _structured_metadata(value, line) if value.startswith("<") else value
            if key in ("INFO", "FORMAT", "FILTER", "ALT", "contig"):
                if not isinstance(item, tuple):
                    _fail(line, f"{key} must use structured metadata")
                fields = dict(item)
                identity = fields.get("ID")
                if not identity or (key, identity) in identifiers:
                    _fail(line, f"missing or duplicate {key} ID")
                if identity.startswith('"'):
                    _fail(line, f"{key} ID must not be quoted")
                identifiers.add((key, identity))
                if key in definitions:
                    definitions[key][identity] = _vcf_definition(fields, key, line)
                if key == "contig":
                    if "length" in fields:
                        _uint(fields["length"], line, "contig length", minimum=1)
                    contigs.append(item)
                    continue
            metadata[(key, item)] += 1
            continue
        if text.startswith("#"):
            if header is not None or not saw_fileformat:
                _fail(line, "duplicate or misplaced VCF column header")
            columns = text.split("\t")
            if columns[:8] != ["#CHROM", "POS", "ID", "REF", "ALT", "QUAL", "FILTER", "INFO"]:
                _fail(line, "invalid VCF column names")
            if len(columns) == 9 or (len(columns) > 8 and columns[8] != "FORMAT"):
                _fail(line, "VCF FORMAT requires at least one sample")
            samples = columns[9:]
            if len(set(samples)) != len(samples):
                _fail(line, "duplicate VCF sample name")
            for sample in samples:
                _no_space(sample, line, "sample name")
            header = tuple(columns)
            continue
        if header is None:
            _fail(line, "VCF record before column header")
        columns = text.split("\t")
        if len(columns) != len(header):
            _fail(line, f"VCF record has {len(columns)} columns; expected {len(header)}")
        chrom, position, identifier, reference, alternate, quality, filters, info = columns[:8]
        _no_space(chrom, line, "CHROM")
        _uint(position, line, "POS", minimum=1)
        _no_space(identifier, line, "ID")
        if not DNA.fullmatch(reference):
            _fail(line, "invalid VCF REF allele")
        alts = [] if alternate == "." else alternate.split(",")
        if len(alts) != len(set(alts)) or any(not _valid_alt(allele) for allele in alts):
            _fail(line, "invalid or duplicate VCF ALT allele")
        if quality != "." and _float(quality, line, "QUAL") < 0:
            _fail(line, "QUAL must not be negative")
        filter_values = filters.split(";")
        if len(filter_values) != len(set(filter_values)) or any(not VCF_KEY.fullmatch(item) for item in filter_values):
            _fail(line, "invalid or duplicate FILTER")
        if len(filter_values) > 1 and any(item in (".", "PASS") for item in filter_values):
            _fail(line, "PASS or missing FILTER cannot be combined with other filters")
        information = {}
        if info != ".":
            for field in info.split(";"):
                key, separator, value = field.partition("=")
                if not VCF_KEY.fullmatch(key) or key in information or (separator and not value):
                    _fail(line, "invalid or duplicate INFO field")
                if separator:
                    _no_space(value, line, f"INFO/{key}")
                    if "=" in value:
                        _fail(line, f"INFO/{key} contains an unescaped equals sign")
                item = value if separator else None
                _vcf_value(item, definitions["INFO"].get(key), len(alts), line, f"INFO/{key}")
                information[key] = item
        sample_values = []
        if len(header) > 8:
            format_keys = columns[8].split(":")
            if len(format_keys) != len(set(format_keys)) or any(not VCF_KEY.fullmatch(key) or key == "." for key in format_keys):
                _fail(line, "invalid or duplicate FORMAT key")
            if "GT" in format_keys and format_keys[0] != "GT":
                _fail(line, "GT must be the first FORMAT field")
            for sample in columns[9:]:
                values = sample.split(":")
                if len(values) > len(format_keys) or any(not value for value in values):
                    _fail(line, "invalid number of FORMAT sample fields or empty field")
                values += ["."] * (len(format_keys) - len(values))
                fields = dict(zip(format_keys, values))
                ploidy = None
                if "GT" in fields:
                    genotype = fields["GT"]
                    if not GENOTYPE.fullmatch(genotype):
                        _fail(line, "invalid genotype")
                    alleles = re.split(r"[/|]", genotype)
                    if any(allele != "." and int(allele) > len(alts) for allele in alleles):
                        _fail(line, "genotype allele index exceeds ALT count")
                    # A lone missing GT does not specify ploidy.
                    ploidy = None if genotype == "." else len(alleles)
                for key, value in fields.items():
                    _no_space(value, line, f"FORMAT/{key}")
                    _vcf_value(value, definitions["FORMAT"].get(key), len(alts), line, f"FORMAT/{key}", ploidy)
                sample_values.append(tuple(sorted(fields.items())))
        records[(tuple(columns[:6]), tuple(sorted(filter_values)), tuple(sorted(information.items())), tuple(sample_values))] += 1
    if header is None:
        raise FormatError("VCF column header is missing")
    return Parsed((tuple(contigs), header), metadata, records)


def _preview(value) -> str:
    text = json.dumps(value, ensure_ascii=True, separators=(",", ":"))
    return text if len(text) <= MAX_PREVIEW_CHARS else text[:MAX_PREVIEW_CHARS] + "... [truncated]"


def _qual_tolerance(value: str | Decimal | float | int) -> Decimal:
    try:
        tolerance = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("QUAL absolute tolerance must be a finite, nonnegative decimal") from exc
    if not tolerance.is_finite() or tolerance < 0:
        raise ValueError("QUAL absolute tolerance must be a finite, nonnegative decimal")
    return tolerance


def _decimal_delta(left: Decimal, right: Decimal) -> Decimal:
    # Decimal subtraction normally uses a 28-digit context. Set sufficient
    # precision for these exact operands so even cancellation is not rounded.
    scale = min(left.as_tuple().exponent, right.as_tuple().exponent)
    precision = max(len(left.as_tuple().digits) + left.as_tuple().exponent - scale,
                    len(right.as_tuple().digits) + right.as_tuple().exponent - scale) + 1
    with localcontext() as context:
        context.prec = precision
        context.Emax = MAX_EMAX
        context.Emin = MIN_EMIN
        return abs(left - right)


def _tolerate_vcf_qualities(missing: Counter, extra: Counter, tolerance: Decimal,
                           max_examples: int, max_magnitude: Decimal | None) -> dict:
    result = {"count": 0, "max_qual_absolute_delta": "0", "examples": [],
              "omitted_examples": 0}
    if not tolerance:
        return result
    groups = []
    for records in (missing, extra):
        grouped = {}
        for record in records:
            quality_text = record[0][5]
            if quality_text == ".":
                continue  # A missing value never participates in tolerance.
            quality = Decimal(quality_text)
            if not quality.is_finite():
                continue
            if max_magnitude is not None and quality.copy_abs() > max_magnitude:
                continue  # Outside the bound, only exact records may match.
            # Everything except QUAL must match, including genotype phasing,
            # raw allele representation, tags, and all sample fields. Separate
            # signed zero as well: a sign change must never be tolerated.
            key = (record[0][:5], record[1:], quality.is_signed())
            grouped.setdefault(key, []).append((quality, quality_text, record))
        for values in grouped.values():
            values.sort(key=lambda value: (value[0], value[1]))
        groups.append(grouped)
    largest_delta = Decimal(0)
    example_count = 0
    for key in sorted(groups[0].keys() & groups[1].keys(), key=repr):
        left, right = groups[0][key], groups[1][key]
        i = j = 0
        # Ordered matching gives maximal multiplicity-preserving matches within
        # an absolute interval. Include exact matches in this matching: removing
        # them first can prevent a valid pairing of the remaining duplicates.
        while i < len(left) and j < len(right):
            expected, expected_text, expected_record = left[i]
            actual, actual_text, actual_record = right[j]
            delta = _decimal_delta(expected, actual)
            if delta <= tolerance:
                count = min(missing[expected_record], extra[actual_record])
                missing[expected_record] -= count
                extra[actual_record] -= count
                if expected_text != actual_text:
                    result["count"] += count
                    largest_delta = max(largest_delta, delta)
                    example_count += 1
                    if len(result["examples"]) < max_examples:
                        result["examples"].append({"kind": "vcf_qual", "count": count,
                                                   "expected": expected_text, "actual": actual_text,
                                                   "absolute_delta": str(delta),
                                                   "record_without_qual": _preview(key[:2])})
                if not missing[expected_record]:
                    del missing[expected_record]
                    i += 1
                if not extra[actual_record]:
                    del extra[actual_record]
                    j += 1
            elif expected < actual:
                i += 1
            else:
                j += 1
    result["max_qual_absolute_delta"] = str(largest_delta)
    result["omitted_examples"] = example_count - len(result["examples"])
    return result


def compare_files(kind: str, expected: str | Path, actual: str | Path,
                  max_differences: int = DEFAULT_MAX_DIFFERENCES,
                  qual_absolute_tolerance: str | Decimal | float | int = 0,
                  qual_max_magnitude: str | Decimal | float | int | None = None) -> dict:
    """Return a JSON-serializable report. ``equal`` is false for invalid inputs.

    Record order and unordered key order are ignored; record multiplicity and
    all unallowlisted values remain exact. VCF QUAL tolerance is opt-in, absolute,
    calculated with Decimal, and separately reported; all other values are exact.
    """
    parsers: dict[str, Callable[[TextIO], Parsed]] = {"sam": parse_sam, "vcf": parse_vcf}
    if kind not in parsers:
        raise ValueError("format must be sam or vcf")
    if max_differences < 1:
        raise ValueError("max_differences must be positive")
    tolerance = _qual_tolerance(qual_absolute_tolerance)
    max_magnitude = None
    if qual_max_magnitude is not None:
        try:
            max_magnitude = _qual_tolerance(qual_max_magnitude)
        except ValueError as exc:
            raise ValueError("QUAL maximum magnitude must be a finite, nonnegative decimal") from exc
    if kind != "vcf" and (tolerance or max_magnitude is not None):
        raise ValueError("QUAL absolute tolerance and maximum magnitude are supported only for VCF")
    report = {"format": kind, "expected": str(expected), "actual": str(actual),
              "equal": False, "counts": {}, "differences": [], "differences_total": 0,
              "omitted_differences": 0, "errors": [],
              "qual_absolute_tolerance": str(tolerance),
              "qual_max_magnitude": None if max_magnitude is None else str(max_magnitude),
              "tolerated_differences": {"count": 0, "max_qual_absolute_delta": "0",
                                        "examples": [], "omitted_examples": 0}}
    parsed = {}
    for side, path in (("expected", expected), ("actual", actual)):
        try:
            with Path(path).open("r", encoding="utf-8", errors="strict", newline=None) as stream:
                parsed[side] = parsers[kind](stream)
            report["counts"][f"{side}_records"] = sum(parsed[side].records.values())
            report["counts"][f"{side}_unique_records"] = len(parsed[side].records)
        except (OSError, UnicodeError, FormatError) as exc:
            report["errors"].append({"side": side, "message": str(exc)[:MAX_PREVIEW_CHARS]})
    if report["errors"]:
        return report
    left, right = parsed["expected"], parsed["actual"]

    def difference(entry: dict) -> None:
        report["differences_total"] += 1
        if len(report["differences"]) < max_differences:
            report["differences"].append(entry)

    if left.ordered_header != right.ordered_header:
        difference({"kind": "ordered_header", "expected": _preview(left.ordered_header),
                    "actual": _preview(right.ordered_header)})
    for category, left_values, right_values in (("metadata", left.metadata, right.metadata),
                                                ("record", left.records, right.records)):
        missing = left_values - right_values
        extra = right_values - left_values
        if category == "record" and kind == "vcf" and tolerance:
            missing, extra = left_values.copy(), right_values.copy()
            report["tolerated_differences"] = _tolerate_vcf_qualities(missing, extra, tolerance, max_differences, max_magnitude)
            unchanged = missing & extra
            missing -= unchanged
            extra -= unchanged
        report["counts"][f"missing_{category}s"] = sum(missing.values())
        report["counts"][f"extra_{category}s"] = sum(extra.values())
        for label, values in (("missing", missing), ("extra", extra)):
            for entry, count in sorted(values.items(), key=lambda item: repr(item[0])):
                difference({"kind": f"{label}_{category}", "count": count,
                            "value": _preview(entry)})
    report["omitted_differences"] = report["differences_total"] - len(report["differences"])
    report["equal"] = report["differences_total"] == 0
    return report


def compare_sam(expected: str | Path, actual: str | Path,
                max_differences: int = DEFAULT_MAX_DIFFERENCES) -> dict:
    return compare_files("sam", expected, actual, max_differences)


def compare_vcf(expected: str | Path, actual: str | Path,
                max_differences: int = DEFAULT_MAX_DIFFERENCES,
                qual_absolute_tolerance: str | Decimal | float | int = 0,
                qual_max_magnitude: str | Decimal | float | int | None = None) -> dict:
    return compare_files("vcf", expected, actual, max_differences, qual_absolute_tolerance, qual_max_magnitude)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("format", choices=("sam", "vcf"))
    parser.add_argument("expected", type=Path)
    parser.add_argument("actual", type=Path)
    parser.add_argument("--report", type=Path, help="also save the JSON report to this path")
    parser.add_argument("--max-differences", type=int, default=DEFAULT_MAX_DIFFERENCES)
    parser.add_argument("--qual-absolute-tolerance", default="0", metavar="DECIMAL",
                        help="opt-in VCF QUAL-only absolute tolerance (default: 0, strict)")
    parser.add_argument("--qual-max-magnitude", default=None, metavar="DECIMAL",
                        help="apply QUAL tolerance only when both absolute QUAL values are at most this bound")
    args = parser.parse_args(argv)
    if args.max_differences < 1:
        parser.error("--max-differences must be positive")
    try:
        report = compare_files(args.format, args.expected, args.actual, args.max_differences,
                               args.qual_absolute_tolerance, args.qual_max_magnitude)
    except ValueError as exc:
        parser.error(str(exc))
    output = json.dumps(report, indent=2, sort_keys=True) + "\n"
    print(output, end="")
    if args.report:
        try:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(output, encoding="utf-8")
        except OSError as exc:
            print(f"cannot write report: {exc}", file=sys.stderr)
            return 2
    return 2 if report["errors"] else 0 if report["equal"] else 1


if __name__ == "__main__":
    sys.exit(main())
