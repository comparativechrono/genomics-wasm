#!/usr/bin/env python3
"""Compare complete bustools outputs and validate their structural invariants.

Every output byte, BUS header field, record order, matrix coordinate/count and
row/column identifier is retained. There is no numerical tolerance. Parsing
adds independent malformed/empty-output checks; it never replaces comparison
with an aggregate or a newly implemented biological algorithm.
"""
import argparse
import gzip
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import struct

NORMALISATION = ['None: all declared artifacts are compared byte for byte.']


def digest(value):
    return hashlib.sha256(json.dumps(value, separators=(',', ':'), sort_keys=True).encode()).hexdigest()


def safe_file(directory, name):
    path = PurePosixPath(name)
    if not name or path.is_absolute() or str(path) != name or any(p in ('..', '.') for p in path.parts):
        raise ValueError('unsafe artifact path: ' + name)
    base = Path(directory).resolve()
    resolved = (base / name).resolve(strict=True)
    if not resolved.is_relative_to(base) or not resolved.is_file():
        raise ValueError('artifact escapes result directory: ' + name)
    return resolved


def bus_invariants(raw, artifact):
    if len(raw) < 20 or raw[:4] != b'BUS\0':
        raise ValueError('invalid BUS magic/header')
    version, bc, umi, length = struct.unpack_from('<4I', raw, 4)
    if version != 1 or not 1 <= bc <= 32 or not 1 <= umi <= 32 or length > len(raw)-20:
        raise ValueError('invalid BUS version, sequence lengths or text length')
    raw[20:20+length].decode('utf-8', errors='strict')
    body = raw[20+length:]
    if len(body) % 32:
        raise ValueError('truncated BUS record')
    rows = list(struct.iter_unpack('<QQiIII', body))
    if any(row[2] < 0 or row[3] < 1 for row in rows):
        raise ValueError('invalid BUS EC or count')
    if artifact.get('sortedBy'):
        columns = {'barcode': 0, 'umi': 1, 'ec': 2, 'count': 3, 'flags': 4, 'pad': 5}
        keys = [tuple(row[columns[col]] for col in artifact['sortedBy']) for row in rows]
        if keys != sorted(keys):
            raise ValueError('BUS records violate specified sort order')
    return {'records': len(rows), 'barcodes': len({row[0] for row in rows}),
            'umis': len({row[1] for row in rows}), 'ecs': len({row[2] for row in rows}),
            'totalCount': sum(row[3] for row in rows), 'duplicateMultiplicity': sum(row[3] > 1 for row in rows),
            'barcodeLength': bc, 'umiLength': umi, 'headerTextBytes': length,
            'nonzeroFlags': sum(row[4] != 0 for row in rows)}


def text(raw):
    value = raw.decode('utf-8', errors='strict')
    if '\0' in value or any(ord(c) < 32 and c not in '\n\r\t' for c in value):
        raise ValueError('invalid text control character')
    return value


def matrix_invariants(raw):
    lines = text(raw).splitlines()
    if not lines or lines[0] not in ('%%MatrixMarket matrix coordinate integer general', '%%MatrixMarket matrix coordinate real general'):
        raise ValueError('invalid Matrix Market header')
    rows = [line.split() for line in lines[1:] if line and not line.startswith('%')]
    if not rows or len(rows[0]) != 3 or any(not re.fullmatch(r'\d+', x) for x in rows[0]):
        raise ValueError('invalid matrix dimensions')
    nrow, ncol, nnz = map(int, rows.pop(0))
    if len(rows) != nnz or nrow < 1 or ncol < 1:
        raise ValueError('matrix shape or nonzero count mismatch')
    coordinates, total = set(), 0
    integer = ' integer ' in lines[0]
    for row in rows:
        if len(row) != 3 or not row[0].isdigit() or not row[1].isdigit():
            raise ValueError('invalid matrix entry')
        i, j = int(row[0]), int(row[1])
        if not 1 <= i <= nrow or not 1 <= j <= ncol or (i, j) in coordinates:
            raise ValueError('out of bounds or duplicate matrix coordinate')
        if not re.fullmatch(r'(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?', row[2]):
            raise ValueError('invalid matrix count')
        value = float(row[2])
        if not math.isfinite(value) or value <= 0 or integer and not re.fullmatch(r'\d+', row[2]):
            raise ValueError('nonpositive, nonfinite or noninteger matrix count')
        total += value
        coordinates.add((i, j))
    return {'records': nnz, 'rows': nrow, 'columns': ncol, 'totalCount': total,
            'nonemptyRows': len({i for i, _ in coordinates}), 'nonemptyColumns': len({j for _, j in coordinates})}


def parse_artifact(path, artifact):
    raw = Path(path).read_bytes()
    if not raw and not artifact.get('allowEmpty', False):
        raise ValueError('empty artifact: ' + artifact['path'])
    kind = artifact['kind']
    if kind == 'bus':
        inv = bus_invariants(raw, artifact)
    elif kind == 'busz':
        if len(raw) < 32 or raw[:4] != b'BUS\1' or struct.unpack_from('<I', raw, 4)[0] != 1:
            raise ValueError('invalid compressed BUS header')
        length = struct.unpack_from('<I', raw, 16)[0]
        if len(raw) < 20+length+12+8 or raw[-8:] != bytes(8):
            raise ValueError('truncated compressed BUS data')
        inv = {'records': 1, 'bytes': len(raw)}
    elif kind == 'matrix':
        inv = matrix_invariants(raw)
    elif kind == 'identifiers':
        values = text(raw).splitlines()
        if any(not value or value.strip() != value or '\t' in value for value in values) or len(values) != len(set(values)):
            raise ValueError('empty, malformed or duplicate row/column identifier')
        inv = {'records': len(values)}
    elif kind == 'ec':
        rows = [line.split('\t') for line in text(raw).splitlines()]
        if any(len(row) != 2 or not row[0].isdigit() or not re.fullmatch(r'\d+(,\d+)*', row[1]) for row in rows):
            raise ValueError('invalid equivalence class mapping')
        if len({row[0] for row in rows}) != len(rows):
            raise ValueError('duplicate EC ID')
        inv = {'records': len(rows), 'transcripts': len({tid for row in rows for tid in row[1].split(',')})}
    elif kind == 'json':
        obj = json.loads(text(raw), parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON')))
        if not isinstance(obj, dict) or not obj:
            raise ValueError('empty or non-object JSON report')
        inv = {'records': len(obj)}
        for field in artifact.get('positiveFields', []):
            if not isinstance(obj.get(field), (int, float)) or obj[field] <= 0:
                raise ValueError('required positive report field absent: ' + field)
    elif kind == 'fastq-gzip':
        lines = text(gzip.decompress(raw)).splitlines()
        if len(lines) % 4 or any(not lines[i].startswith('@') or not lines[i+2].startswith('+') or len(lines[i+1]) != len(lines[i+3]) for i in range(0, len(lines), 4)):
            raise ValueError('invalid extracted FASTQ')
        inv = {'records': len(lines)//4}
    elif kind == 'text':
        inv = {'records': len(text(raw).splitlines())}
    elif kind == 'exact':
        inv = {'records': 1, 'bytes': len(raw)}
    else:
        raise ValueError('unknown artifact kind: ' + kind)
    if inv['records'] < artifact.get('minimumRecords', 0 if artifact.get('allowEmpty') else 1):
        raise ValueError('required record coverage absent: ' + artifact['path'])
    for key, minimum in artifact.get('minimum', {}).items():
        if inv.get(key, -1) < minimum:
            raise ValueError('required invariant absent: ' + key)
    for key, value in artifact.get('expected', {}).items():
        if inv.get(key) != value:
            raise ValueError('unexpected invariant: ' + key)
    return raw.hex(), inv


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
        for artifact in artifacts:
            if artifact['kind'] == 'matrix':
                for field, axis in [('rowLabels', 'rows'), ('columnLabels', 'columns')]:
                    if field in artifact and (artifact[field] not in invariants[side] or invariants[side][artifact[field]]['records'] != invariants[side][artifact['path']][axis]):
                        raise ValueError('matrix identity file does not match dimension: ' + field)
    expected, actual = values['expected'], values['actual']
    return {'equal': expected == actual, 'semanticDigest': digest(actual), 'expectedSemanticDigest': digest(expected),
            'biologicalDigest': digest([actual[p] for p in biological]), 'expectedBiologicalDigest': digest([expected[p] for p in biological]),
            'nativeInvariants': invariants['expected'], 'invariants': invariants['actual'],
            'expectedFiles': hashes['expected'], 'actualFiles': hashes['actual'],
            'expectedFileBytes': sizes['expected'], 'actualFileBytes': sizes['actual'],
            'normalisation': NORMALISATION, 'numericTolerance': 0,
            'differences': [p for p in paths if expected[p] != actual[p]]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('expected', type=Path)
    parser.add_argument('actual', type=Path)
    parser.add_argument('--tool', choices=['bustools'], required=True)
    parser.add_argument('--dataset', required=True)
    parser.add_argument('--case', required=True)
    parser.add_argument('--actual-target', required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    plan = json.loads((Path(__file__).parent / 'cases.json').read_text())
    spec = next(test for test in plan['tools'][args.tool]['cases'] if test['id'] == args.case)
    if args.dataset not in spec.get('datasets', plan['tools'][args.tool]['datasets']):
        raise ValueError('case dataset mismatch')
    manifest_raw = args.manifest.read_bytes()
    json.loads(manifest_raw)['datasets'][args.dataset]
    result = compare(args.expected, args.actual, spec)
    result.update({'schema': 1, 'tool': args.tool, 'dataset': args.dataset, 'case': args.case,
                   'actualTarget': args.actual_target, 'manifestSha256': hashlib.sha256(manifest_raw).hexdigest()})
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result['equal'] else 1)


if __name__ == '__main__':
    main()
