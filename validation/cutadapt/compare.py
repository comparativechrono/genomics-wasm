#!/usr/bin/env python3
"""Compare Cutadapt records and report statistics without numerical tolerances.

FASTQ gzip wrapper metadata is ignored after complete CRC-checked decoding.
Read order, identifiers, bases, qualities and every JSON report value remain exact.
Each output is independently reconciled with its source reads and JSON counts.
"""
import argparse
from decimal import Decimal
import gzip
import hashlib
import json
from pathlib import Path


def canonical(value):
    if value is None:
        return ['null']
    if isinstance(value, bool):
        return ['boolean', value]
    if isinstance(value, int):
        return ['integer', str(value)]
    if isinstance(value, str):
        return ['string', value]
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError('non-finite JSON number')
        text = format(value, 'f')
        if '.' in text:
            text = text.rstrip('0').rstrip('.')
        return ['decimal', text if value else '0']
    if isinstance(value, dict):
        return ['object', [[key, canonical(item)] for key, item in sorted(value.items())]]
    if isinstance(value, (list, tuple)):
        return ['array', [canonical(item) for item in value]]
    raise ValueError('unsupported comparison value: ' + type(value).__name__)


def digest(value):
    return hashlib.sha256(json.dumps(canonical(value), sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def unique_object(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError('duplicate JSON key: ' + key)
        result[key] = value
    return result


def read_json(path):
    return json.loads(path.read_text(), parse_float=Decimal, object_pairs_hook=unique_object,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError('non-finite JSON number: ' + value)))


def fastq(path):
    if not path.is_file():
        raise ValueError('missing required FASTQ: ' + str(path))
    raw = path.read_bytes()
    if path.suffix == '.gz':
        if not raw.startswith(b'\x1f\x8b'):
            raise ValueError('FASTQ .gz output is not gzip: ' + str(path))
        raw = gzip.decompress(raw)
    try:
        text = raw.decode('ascii')
    except UnicodeDecodeError as error:
        raise ValueError('non-ASCII FASTQ: ' + str(path)) from error
    # Line ending syntax is not a base, quality or read identifier.
    text = text.replace('\r\n', '\n')
    if any(ord(char) < 32 and char != '\n' for char in text):
        raise ValueError('invalid FASTQ control character or line ending')
    rows = text.split('\n') if text else []
    if rows and rows[-1] == '':
        rows.pop()
    if len(rows) % 4:
        raise ValueError('incomplete FASTQ record: ' + str(path))
    records = []
    for offset in range(0, len(rows), 4):
        title, sequence, plus, quality = rows[offset:offset + 4]
        if not title.startswith('@') or len(title) == 1 or not plus.startswith('+'):
            raise ValueError('invalid FASTQ record header')
        if any(ord(char) < 32 or ord(char) > 126 for char in title[1:]):
            raise ValueError('invalid FASTQ identifier')
        if plus[1:] and plus[1:] != title[1:]:
            raise ValueError('FASTQ repeated identifier differs')
        if any(char not in 'ACGTRYSWKMBDHVNacgtryswkmbdhvn' for char in sequence):
            raise ValueError('invalid FASTQ nucleotide')
        if len(sequence) != len(quality) or any(ord(char) < 33 or ord(char) > 126 for char in quality):
            raise ValueError('invalid FASTQ quality')
        records.append((title[1:], sequence, quality))
    return records


def pair_name(title):
    token = title.split()[0]
    return token[:-2] if token.endswith(('/1', '/2')) else token


def integer(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(name + ': expected non-negative integer')
    return value


def validate_report(report, context, groups):
    schema = report.get('schema_version')
    if report.get('tag') != 'Cutadapt report' or schema != [0, 3] or any(type(value) is not int for value in schema):
        raise ValueError('invalid Cutadapt JSON report schema')
    if report.get('cutadapt_version') != context.get('version', '5.2'):
        raise ValueError('Cutadapt JSON version differs from pinned version')
    if report.get('python_version') != context.get('pythonVersion', '3.14.2'):
        raise ValueError('Cutadapt JSON Python version differs from pinned version')
    if context.get('args') is not None and report.get('command_line_arguments') != context['args']:
        raise ValueError('Cutadapt JSON command arguments differ from case plan')
    if integer(report.get('cores'), 'cores') != 1:
        raise ValueError('Cutadapt report did not use the supported single worker')
    inputs = context['inputs']
    paired = len(inputs) == 2
    if report.get('input', {}).get('paired') is not paired:
        raise ValueError('Cutadapt JSON paired status differs from inputs')
    counts, bases = report.get('read_counts', {}), report.get('basepair_counts', {})
    input_count = len(inputs[0])
    if not input_count or (paired and len(inputs[1]) != input_count):
        raise ValueError('empty or unequal source reads')
    if integer(counts.get('input'), 'input reads') != input_count:
        raise ValueError('JSON input read count differs from source FASTQ')
    input_bases = [sum(len(read[1]) for read in reads) for reads in inputs]
    if integer(bases.get('input'), 'input bases') != sum(input_bases):
        raise ValueError('JSON input base count differs from source FASTQ')
    for index, total in enumerate(input_bases, 1):
        if integer(bases.get(f'input_read{index}'), f'input read{index} bases') != total:
            raise ValueError('JSON per-mate input bases differ from source FASTQ')
    output_count = sum(len(group[0]) for group in groups)
    output_bases = [sum(sum(len(record[1]) for record in group[index]) for group in groups) for index in range(len(inputs))]
    if integer(counts.get('output'), 'output reads') != output_count:
        raise ValueError('JSON output read count differs from written FASTQ')
    if integer(bases.get('output'), 'output bases') != sum(output_bases):
        raise ValueError('JSON output base count differs from written FASTQ')
    for index, total in enumerate(output_bases, 1):
        if integer(bases.get(f'output_read{index}'), f'output read{index} bases') != total:
            raise ValueError('JSON per-mate output bases differ from written FASTQ')
    filtered = counts.get('filtered')
    if not isinstance(filtered, dict):
        raise ValueError('missing Cutadapt filtered counts')
    filtered_total = sum(integer(value, 'filtered count') for value in filtered.values() if value is not None)
    if output_count + filtered_total != input_count:
        raise ValueError('Cutadapt output and filtered reads do not account for all source reads')
    for index in range(len(inputs)):
        value = counts.get(f'read{index + 1}_with_adapter')
        if value is not None and integer(value, 'reads with adapter') > input_count:
            raise ValueError('adapter read count exceeds source reads')
    return {'inputReads': input_count, 'outputReads': output_count, 'filteredReads': filtered_total,
            'inputBases': input_bases, 'outputBases': output_bases}


def load_output(directory, context):
    inputs = context['inputs']
    source_indices = []
    for records in inputs:
        mapping = {record[0]: (index, record) for index, record in enumerate(records)}
        if len(mapping) != len(records):
            raise ValueError('fixture requires unique full read identifiers')
        source_indices.append(mapping)
    paired = len(inputs) == 2
    if paired and any(pair_name(a[0]) != pair_name(b[0]) for a, b in zip(*inputs)):
        raise ValueError('source FASTQ pairing is inconsistent')
    groups, seen = [], [set() for _ in inputs]
    for paths in context['readGroups']:
        if len(paths) != len(inputs):
            raise ValueError('output mate count differs from input mate count')
        group = [fastq(directory / path) for path in paths]
        if paired and (len(group[0]) != len(group[1]) or any(pair_name(a[0]) != pair_name(b[0]) for a, b in zip(*group))):
            raise ValueError('output FASTQ pairing is inconsistent')
        for mate, records in enumerate(group):
            previous = -1
            for record in records:
                if record[0] not in source_indices[mate]:
                    raise ValueError('output identifier does not exist in corresponding source mate')
                index, original = source_indices[mate][record[0]]
                if index <= previous or record[0] in seen[mate]:
                    raise ValueError('read order changed or a read was written more than once')
                previous = index
                seen[mate].add(record[0])
                # All planned transformations trim prefixes/suffixes and preserve
                # the correspondence between retained bases and quality scores.
                start = original[1].find(record[1])
                found = False
                while start >= 0:
                    if original[2][start:start + len(record[1])] == record[2]:
                        found = True
                        break
                    start = original[1].find(record[1], start + 1)
                if not found:
                    raise ValueError('written bases and qualities are not a matched slice of the source read')
        groups.append(group)
    report = read_json(directory / context.get('json', 'report.json'))
    invariants = validate_report(report, context, groups)
    if context.get('requireNonzero', True) and not invariants['outputReads']:
        raise ValueError('vacuous case: no output reads')
    return {'reads': groups, 'report': report}, invariants


def compare_directories(expected, actual, context):
    left, left_checks = load_output(Path(expected), context)
    right, right_checks = load_output(Path(actual), context)
    return {'schema': 1, 'tool': 'cutadapt', 'equal': canonical(left) == canonical(right),
            'expectedSemanticDigest': digest(left), 'semanticDigest': digest(right),
            'biologicalDigest': digest(right['reads']), 'invariants': right_checks,
            'nativeInvariants': left_checks,
            'normalisation': ['gzip wrapper metadata', 'FASTQ line ending syntax', 'JSON object key order'],
            'numericPolicy': 'Exact integer and decimal values; no tolerances.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('expected')
    parser.add_argument('actual')
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--dataset', required=True)
    parser.add_argument('--case', required=True)
    parser.add_argument('--report', required=True)
    args = parser.parse_args()
    try:
        manifest_path = Path(args.manifest)
        manifest = read_json(manifest_path)
        plan = read_json(Path(__file__).with_name('cases.json'))
        case = next(item for item in plan['cases'] if item['id'] == args.case)
        data = manifest['datasets'][args.dataset]
        context = {**case, 'version': plan['version'], 'pythonVersion': plan['pythonVersion'],
                   'inputs': [fastq(manifest_path.parent / data[key]) for key in case.get('inputKeys', ['read1', 'read2'])]}
        result = compare_directories(args.expected, args.actual, context)
    except Exception as error:
        result = {'schema': 1, 'tool': 'cutadapt', 'equal': False, 'error': str(error)}
    Path(args.report).write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))
    return 0 if result['equal'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
