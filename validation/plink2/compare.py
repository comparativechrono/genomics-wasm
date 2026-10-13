#!/usr/bin/env python3
"""Compare PLINK 2 outputs without weakening biological identity or counts.

Only declared floating-point columns use the pinned upstream regression-test
printed-precision bound. All other cells, row order, dimensions, missing-value
markers and error codes are exact. Binary artifacts are byte-exact.
"""
import argparse
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path, PurePosixPath
import re

NUMERIC_POLICY = {'method': 'printed-precision', 'relativeSlack': 1e-8, 'roundingUnits': 0.5}
NORMALISATION = [
    'Text line endings are normalized from CRLF to LF; no rows or scientific fields are removed.',
    'VCF ##fileDate=YYYYMMDD is replaced by its date placeholder; the PLINK version source platform suffix alone is removed.',
    'Tables retain complete header and row order. Only the case-declared floatColumns use the upstream TEST_GLM_LOGISTIC_MISSING printed-precision bound; identifiers, alleles, counts, tests and error codes remain exact.',
]
NUMBER = re.compile(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?\Z')
MISSING = {'NA', '.'}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def safe_file(directory, name):
    path = PurePosixPath(name)
    if not name or path.is_absolute() or str(path) != name or any(p in ('.', '..') for p in path.parts):
        raise ValueError('unsafe artifact path: ' + name)
    base = Path(directory).resolve()
    resolved = (base / name).resolve(strict=True)
    if not resolved.is_relative_to(base) or not resolved.is_file():
        raise ValueError('artifact escapes result directory: ' + name)
    return resolved


def text(raw):
    value = raw.decode('utf-8', errors='strict').replace('\r\n', '\n')
    if any(ord(c) < 32 and c not in '\n\t' for c in value):
        raise ValueError('invalid text control character')
    return value


def number(value):
    if not NUMBER.fullmatch(value):
        raise ValueError('nonfinite or invalid numerical field: ' + value)
    try:
        result = Decimal(value)
    except InvalidOperation as error:
        raise ValueError('invalid number') from error
    if not result.is_finite():
        raise ValueError('nonfinite number')
    return result


def numeric_equal(a, b):
    """Same bound as upstream Tests/TEST_GLM_LOGISTIC_MISSING/compare.awk."""
    if a in MISSING or b in MISSING:
        return a == b, None, None
    x, y = number(a), number(b)
    if re.fullmatch(r'[+-]?\d+', a) or re.fullmatch(r'[+-]?\d+', b):
        return x == y, abs(x-y), Decimal(0)
    # Decimal retains trailing printed zeros and the scientific exponent.
    place_a, place_b = Decimal(10) ** x.as_tuple().exponent, Decimal(10) ** y.as_tuple().exponent
    tolerance = Decimal('0.5') * (place_a + place_b) + Decimal('1e-8') * (abs(x) + abs(y))
    difference = abs(x - y)
    return difference <= tolerance, difference, tolerance


def parse_artifact(path, spec):
    raw = path.read_bytes()
    if not raw and not spec.get('allowEmpty'):
        raise ValueError('empty artifact: ' + spec['path'])
    kind = spec['kind']
    if kind == 'exact':
        value, inv = raw.hex(), {'records': 1, 'bytes': len(raw)}
        if spec.get('magicHex') and not raw.startswith(bytes.fromhex(spec['magicHex'])):
            raise ValueError('invalid binary magic')
    elif kind == 'text':
        value = text(raw)
        inv = {'records': len(value.splitlines())}
    elif kind == 'vcf':
        lines = text(raw).splitlines()
        headers, rows = [], []
        columns = None
        for line in lines:
            if line.startswith('##'):
                if re.fullmatch(r'##fileDate=\d{8}', line):
                    line = '##fileDate=<DATE>'
                elif line.startswith('##source=PLINK'):
                    # Keep tool version/date. Platform descriptor is the only
                    # difference allowed here, and unknown forms fail equality.
                    line = re.sub(r' (?:64-bit|32-bit)(?: (?:SSE2|AVX2|ARM64))?(?=\s|$)', ' <PLATFORM>', line)
                headers.append(line)
            elif line.startswith('#CHROM\t') and columns is None:
                columns = line.split('\t')
                if columns[:9] != ['#CHROM','POS','ID','REF','ALT','QUAL','FILTER','INFO','FORMAT'] or len(set(columns[9:])) != len(columns[9:]):
                    raise ValueError('invalid VCF sample header')
            else:
                row = line.split('\t')
                if columns is None or len(row) != len(columns) or not row[1].isdigit() or int(row[1]) < 1:
                    raise ValueError('invalid VCF record')
                rows.append(row)
        if columns is None:
            raise ValueError('missing VCF column header')
        value = {'metadata': headers, 'columns': columns, 'rows': rows}
        inv = {'records': len(rows), 'samples': len(columns)-9,
               'phasedCalls': sum('|' in cell for row in rows for cell in row[9:]),
               'multiallelicRecords': sum(',' in row[4] for row in rows),
               'missingCalls': sum('.' in cell.split(':')[0] for row in rows for cell in row[9:])}
    elif kind == 'table':
        lines = text(raw).splitlines()
        if 'columns' in spec:
            columns = spec['columns']
        elif lines:
            columns = lines.pop(0).split()
        else:
            raise ValueError('missing table header')
        if not columns or len(set(columns)) != len(columns):
            raise ValueError('empty or duplicate table columns')
        rows = [line.split() for line in lines]
        if any(len(row) != len(columns) for row in rows):
            raise ValueError('ragged or truncated table')
        floats = spec.get('floatColumns', [])
        if len(set(floats)) != len(floats) or any(col not in columns for col in floats):
            raise ValueError('declared float column absent or duplicate')
        for row in rows:
            for col in floats:
                for value in row[columns.index(col)].split(','):
                    if value not in MISSING:
                        number(value)
        value = {'columns': columns, 'rows': rows}
        inv = {'records': len(rows), 'columns': len(columns), 'columnNames': columns,
               'numericValues': sum(part not in MISSING for row in rows for col in floats for part in row[columns.index(col)].split(',')),
               'exactCells': len(rows) * (len(columns)-len(floats))}
        for col in spec.get('nonmissingColumns', []):
            if col not in columns:
                raise ValueError('required result column absent: ' + col)
            inv['nonmissing:' + col] = sum(row[columns.index(col)] not in MISSING for row in rows)
        for col, expected in spec.get('exactColumnValues', {}).items():
            if col not in columns or any(row[columns.index(col)] != str(expected) for row in rows):
                raise ValueError('unexpected count or identity: ' + col)
    else:
        raise ValueError('unknown artifact kind: ' + kind)
    if inv['records'] < spec.get('minimumRecords', 0 if spec.get('allowEmpty') else 1):
        raise ValueError('required record coverage absent: ' + spec['path'])
    for name, expected in spec.get('expected', {}).items():
        if inv.get(name) != expected:
            raise ValueError('unexpected invariant: ' + name)
    for name, minimum in spec.get('minimum', {}).items():
        if inv.get(name, -1) < minimum:
            raise ValueError('required invariant absent: ' + name)
    return value, inv


def compare_table(a, b, spec):
    floats = spec.get('floatColumns', [])
    stats = {'numericFields': floats, 'exactFields': [col for col in a['columns'] if col not in floats],
             'comparedValues': 0, 'maxAbsoluteDifference': 0, 'maxTolerance': 0,
             'withinTolerance': True, 'outsideTolerance': 0}
    equal = a['columns'] == b['columns'] and len(a['rows']) == len(b['rows'])
    if not equal:
        return False, stats
    maximum_difference = maximum_tolerance = Decimal(0)
    for row_a, row_b in zip(a['rows'], b['rows']):
        for col, x, y in zip(a['columns'], row_a, row_b):
            if col not in floats:
                equal = equal and x == y
                continue
            xs, ys = x.split(','), y.split(',')
            if len(xs) != len(ys):
                equal = False
                continue
            for x_part, y_part in zip(xs, ys):
                passed, difference, tolerance = numeric_equal(x_part, y_part)
                equal = equal and passed
                if difference is not None:
                    stats['comparedValues'] += 1
                    maximum_difference = max(maximum_difference, difference)
                    maximum_tolerance = max(maximum_tolerance, tolerance)
                if not passed:
                    stats['outsideTolerance'] += 1
    stats.update(maxAbsoluteDifference=float(maximum_difference), maxTolerance=float(maximum_tolerance),
                 withinTolerance=stats['outsideTolerance'] == 0)
    return equal, stats


def compare(expected_dir, actual_dir, spec):
    artifacts = spec.get('artifacts', [])
    paths = [artifact['path'] for artifact in artifacts]
    if not artifacts or len(paths) != len(set(paths)):
        raise ValueError('empty or duplicate artifact plan')
    biological = spec.get('biologicalArtifacts', paths)
    if not biological or len(biological) != len(set(biological)) or any(path not in paths for path in biological):
        raise ValueError('invalid biological artifact selection')
    values, invariants, hashes, sizes = {}, {}, {}, {}
    for side, directory in [('expected', expected_dir), ('actual', actual_dir)]:
        values[side], invariants[side], hashes[side], sizes[side] = {}, {}, {}, {}
        for artifact in artifacts:
            name = artifact['path']
            path = safe_file(directory, name)
            values[side][name], invariants[side][name] = parse_artifact(path, artifact)
            raw = path.read_bytes()
            hashes[side][name], sizes[side][name] = hashlib.sha256(raw).hexdigest(), len(raw)
    expected, actual = values['expected'], values['actual']
    differences, stats = [], {}
    for artifact in artifacts:
        name = artifact['path']
        if artifact['kind'] == 'table':
            equal, comparison_stats = compare_table(expected[name], actual[name], artifact)
            if artifact.get('floatColumns'):
                stats[name] = comparison_stats
        else:
            equal = expected[name] == actual[name]
        if not equal:
            differences.append(name)
    return {'equal': not differences, 'semanticDigest': digest(actual), 'expectedSemanticDigest': digest(expected),
            'biologicalDigest': digest([actual[p] for p in biological]), 'expectedBiologicalDigest': digest([expected[p] for p in biological]),
            'nativeInvariants': invariants['expected'], 'invariants': invariants['actual'],
            'expectedFiles': hashes['expected'], 'actualFiles': hashes['actual'],
            'expectedFileBytes': sizes['expected'], 'actualFileBytes': sizes['actual'],
            'expectedCanonicalFiles': {p: digest(expected[p]) for p in paths}, 'canonicalFiles': {p: digest(actual[p]) for p in paths},
            'normalisation': NORMALISATION, 'numericTolerance': NUMERIC_POLICY if stats else 0,
            'numericComparisons': stats, 'differences': differences}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('expected', type=Path)
    parser.add_argument('actual', type=Path)
    parser.add_argument('--tool', choices=['plink2'], required=True)
    parser.add_argument('--dataset', required=True)
    parser.add_argument('--case', required=True)
    parser.add_argument('--actual-target', required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    plan = json.loads((Path(__file__).parent/'cases.json').read_text())
    spec = next(test for test in plan['tools'][args.tool]['cases'] if test['id'] == args.case)
    if args.dataset not in spec.get('datasets', plan['tools'][args.tool]['datasets']):
        raise ValueError('case dataset mismatch')
    manifest_raw = args.manifest.read_bytes()
    json.loads(manifest_raw)['datasets'][args.dataset]
    result = compare(args.expected, args.actual, spec)
    result.update(schema=1, tool=args.tool, dataset=args.dataset, case=args.case,
                  actualTarget=args.actual_target, manifestSha256=hashlib.sha256(manifest_raw).hexdigest())
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result['equal'] else 1)


if __name__ == '__main__':
    main()
