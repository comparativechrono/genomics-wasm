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
from functools import lru_cache

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


def checked_ids(value):
    """Read the exact two-column sample identifiers emitted by this fixture."""
    if isinstance(value, str):
        rows = [line.split() for line in value.splitlines()]
        if not rows or rows.pop(0) != ['#FID', 'IID']:
            raise ValueError('independent truth: missing sample ID header')
    else:
        if value['columns'] != ['#FID', 'IID']:
            raise ValueError('independent truth: incorrect sample ID columns')
        rows = value['rows']
    if any(len(row) != 2 for row in rows):
        raise ValueError('independent truth: malformed sample ID row')
    result = [tuple(row) for row in rows]
    if len(set(result)) != len(result):
        raise ValueError('independent truth: duplicate sample ID')
    return result


@lru_cache(maxsize=16)
def bed_truth(bed_raw, bim_raw, fam_raw, keep_raw):
    """Decode SNP-major BED directly, independent of PLINK and the generator.

    Each sample is represented by three Python arbitrary-precision bitsets;
    pair counts are intersections, never the C/C++ packed transpose algorithm.
    """
    fam = [line.split() for line in text(fam_raw).splitlines()]
    if not fam or any(len(row) != 6 for row in fam):
        raise ValueError('independent truth: invalid FAM')
    all_ids = [tuple(row[:2]) for row in fam]
    if len(set(all_ids)) != len(all_ids):
        raise ValueError('independent truth: duplicate fixture sample')
    keep = checked_ids(text(keep_raw))
    if not keep or not set(keep).issubset(all_ids):
        raise ValueError('independent truth: invalid keep sample')
    selected = [i for i, pair in enumerate(all_ids) if pair in set(keep)]
    ids = [all_ids[i] for i in selected]
    bim = [line.split() for line in text(bim_raw).splitlines()]
    if not bim or any(len(row) != 6 or row[0] not in {str(i) for i in range(1, 23)} for row in bim):
        raise ValueError('independent truth: expected autosomal biallelic BIM')
    if len({row[1] for row in bim}) != len(bim):
        raise ValueError('independent truth: duplicate variant')
    stride = (len(fam) + 3) // 4
    if bed_raw[:3] != b'\x6c\x1b\x01' or len(bed_raw) != 3 + stride * len(bim):
        raise ValueError('independent truth: invalid BED magic or size')
    genotypes = [[0, 0, 0] for _ in ids]
    for variant in range(len(bim)):
        bit = 1 << variant
        offset = 3 + variant * stride
        for sample, raw_sample in enumerate(selected):
            code = (bed_raw[offset + raw_sample // 4] >> (2 * (raw_sample % 4))) & 3
            if code != 1:  # BED: 00=A1/A1, 10=A1/A2, 11=A2/A2, 01=missing.
                genotype = {0: 0, 2: 1, 3: 2}[code]
                genotypes[sample][genotype] |= bit
    pairs = {}
    for i in range(len(ids)):
        a0, ah, a2 = genotypes[i]
        for j in range(i):
            b0, bh, b2 = genotypes[j]
            n = ((a0 | ah | a2) & (b0 | bh | b2)).bit_count()
            hh = (ah & bh).bit_count()
            ibs0 = ((a0 & b2) | (a2 & b0)).bit_count()
            h1 = (ah & (b0 | b2)).bit_count()
            h2 = (bh & (a0 | a2)).bit_count()
            smaller_het = hh + min(h1, h2)
            if not n or not smaller_het:
                raise ValueError('independent truth: degenerate genotype pair')
            kin = Decimal('0.5') - Decimal(4 * ibs0 + h1 + h2) / Decimal(4 * smaller_het)
            ibs = Decimal(1) - Decimal(2 * ibs0 + h1 + h2) / Decimal(2 * n)
            pairs[(i, j)] = {'NSNP': n, 'HETHET': hh, 'IBS0': ibs0,
                             'HET1_HOM2': h1, 'HET2_HOM1': h2,
                             'KINSHIP': kin, 'IBS': ibs}
    return {'ids': ids, 'variants': len(bim), 'pairs': pairs}


def truth_number(token, expected, label):
    """Check dtoa_g's six significant digits against an exact count ratio.

    The bound is determined by the expected magnitude, not by the reported
    number of decimals, so a corrupted coarse token cannot enlarge it.
    """
    actual = number(token)
    expected = Decimal(expected)
    if expected in (0, Decimal('0.5'), 1):
        tolerance = Decimal(0)
    else:
        tolerance = Decimal('0.500001') * (Decimal(10) ** (expected.copy_abs().adjusted() - 5))
    if abs(actual - expected) > tolerance:
        raise ValueError(f'independent truth: {label}: {token} differs from {expected}')


def subset_pairs(raw, ids):
    lines = text(raw).splitlines()
    if not lines:
        raise ValueError('independent truth: empty pair subset')
    columns = lines.pop(0).split()
    if columns != ['#FID1', 'IID1', 'FID2', 'IID2']:
        raise ValueError('independent truth: invalid pair subset header')
    lookup = {sample: i for i, sample in enumerate(ids)}
    pairs = set()
    for line in lines:
        row = line.split()
        if len(row) != 4 or tuple(row[:2]) not in lookup or tuple(row[2:]) not in lookup:
            raise ValueError('independent truth: invalid subset sample')
        a, b = lookup[tuple(row[:2])], lookup[tuple(row[2:])]
        pair = (max(a, b), min(a, b))
        if a == b or pair in pairs:
            raise ValueError('independent truth: duplicate or self subset pair')
        pairs.add(pair)
    return pairs


def check_odd_sample_truth(values, spec, fixture_root):
    config = spec['oddSampleTruth']
    if fixture_root is None:
        raise ValueError('independent truth: fixture directory required')
    raw = {key: safe_file(fixture_root, config[key]).read_bytes() for key in ('bed', 'bim', 'fam', 'keep')}
    truth = bed_truth(*(raw[key] for key in ('bed', 'bim', 'fam', 'keep')))
    ids, pairs = truth['ids'], truth['pairs']
    lookup = {sample: i for i, sample in enumerate(ids)}
    mode = config['mode']
    expected_pairs = set(pairs)
    checked = 0
    first_pairs = 0
    if mode in ('king-table', 'king-subset'):
        if mode == 'king-subset':
            raw['subset'] = safe_file(fixture_root, config['subset']).read_bytes()
            expected_pairs = subset_pairs(raw['subset'], ids)
        if not {(i, 0) for i in range(1, len(ids))}.issubset(expected_pairs):
            raise ValueError('independent truth: first-sample pair coverage absent')
        value = values[config.get('output', 'king.kin0')]
        columns = [c.removeprefix('#') for c in value['columns']]
        required = ['FID1', 'IID1', 'FID2', 'IID2', 'NSNP', 'HETHET', 'IBS0', 'KINSHIP']
        if any(c not in columns for c in required):
            raise ValueError('independent truth: required KING columns absent')
        observed = set()
        for row in value['rows']:
            record = dict(zip(columns, row))
            a, b = (record['FID1'], record['IID1']), (record['FID2'], record['IID2'])
            if a not in lookup or b not in lookup or a == b:
                raise ValueError('independent truth: unknown or self KING pair')
            i, j = lookup[a], lookup[b]
            key = (max(i, j), min(i, j))
            if key in observed or key not in expected_pairs:
                raise ValueError('independent truth: duplicate or unexpected KING pair')
            observed.add(key)
            expected = pairs[key]
            for name in ('NSNP', 'HETHET', 'IBS0'):
                if record[name] != str(expected[name]):
                    raise ValueError('independent truth: incorrect KING count ' + name)
            truth_number(record['KINSHIP'], expected['KINSHIP'], 'KING kinship')
            checked += 1
            first_pairs += key[1] == 0
        if observed != expected_pairs:
            raise ValueError('independent truth: missing KING pairs')
    elif mode in ('king-matrix', 'distance-ibs'):
        matrix = values[config.get('output', 'king.king' if mode == 'king-matrix' else 'distance.mibs')]
        output_ids = checked_ids(values[config.get('ids', 'king.king.id' if mode == 'king-matrix' else 'distance.mibs.id')])
        if output_ids != ids or len(matrix['rows']) != len(ids) or len(matrix['columns']) != len(ids):
            raise ValueError('independent truth: incorrect matrix sample dimensions or order')
        for i, row in enumerate(matrix['rows']):
            if len(row) != len(ids):
                raise ValueError('independent truth: truncated matrix')
            for j, token in enumerate(row):
                expected = (Decimal('0.5') if mode == 'king-matrix' else Decimal(1)) if i == j else pairs[(max(i, j), min(i, j))]['KINSHIP' if mode == 'king-matrix' else 'IBS']
                truth_number(token, expected, mode)
                checked += 1
                first_pairs += i == 0 and j != 0
    elif mode == 'king-cutoff':
        retained = checked_ids(values[config.get('retained', 'cutoff.king.cutoff.in.id')])
        removed = checked_ids(values[config.get('removed', 'cutoff.king.cutoff.out.id')])
        if set(retained) & set(removed) or set(retained) | set(removed) != set(ids):
            raise ValueError('independent truth: cutoff IDs do not partition inputs')
        threshold = Decimal(str(config['threshold']))
        # Independent graph construction from genotype counts, followed by the
        # documented greedy policy. Recompute degrees instead of transcribing
        # PLINK's mutable bit matrix and degree-update implementation.
        edges = {key for key, counts in pairs.items() if counts['KINSHIP'] > threshold}
        remaining = set(range(len(ids)))
        while edges:
            neighbors = {i: set() for i in remaining}
            for i, j in edges:
                neighbors[i].add(j); neighbors[j].add(i)
            leaves = [i for i in sorted(remaining) if len(neighbors[i]) == 1]
            victim = next(iter(neighbors[leaves[0]])) if leaves else min(remaining, key=lambda i: (-len(neighbors[i]), i))
            remaining.remove(victim)
            edges = {edge for edge in edges if victim not in edge}
        if retained != [sample for i, sample in enumerate(ids) if i in remaining] or removed != [sample for i, sample in enumerate(ids) if i not in remaining]:
            raise ValueError('independent truth: incorrect kinship cutoff selection')
        checked, first_pairs = len(pairs), len(ids) - 1
    else:
        raise ValueError('independent truth: unknown regression mode')
    return {'method': 'direct SNP-major BED genotype intersections', 'mode': mode,
            'samples': len(ids), 'variants': truth['variants'], 'checkedValues': checked,
            'firstSamplePairs': first_pairs, 'firstSample': list(ids[0]),
            'inputs': {config[key]: hashlib.sha256(value).hexdigest() for key, value in raw.items()},
            'passed': True}


def compare(expected_dir, actual_dir, spec, fixture_root=None):
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
    independent_truth = {}
    if 'oddSampleTruth' in spec:
        for side in ('expected', 'actual'):
            independent_truth[side] = check_odd_sample_truth(values[side], spec, fixture_root)
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
            'numericComparisons': stats, 'differences': differences,
            **({'independentTruth': independent_truth} if independent_truth else {})}


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
    result = compare(args.expected, args.actual, spec, fixture_root=args.manifest.parent / args.dataset)
    result.update(schema=1, tool=args.tool, dataset=args.dataset, case=args.case,
                  actualTarget=args.actual_target, manifestSha256=hashlib.sha256(manifest_raw).hexdigest())
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result['equal'] else 1)


if __name__ == '__main__':
    main()
