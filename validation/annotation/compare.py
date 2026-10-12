#!/usr/bin/env python3
"""Complete-output Prodigal/HMMER oracle comparison.

Only documented metadata with no biological meaning is normalized. All retained
characters, record order, sequences, model parameters, and printed numeric
values are exact; no numeric tolerance or generic comment removal is applied.
"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import unicodedata

PROGRAMS = 'hmmbuild|hmmsearch|hmmscan|hmmpress|hmmfetch|hmmalign|hmmconvert|hmmemit|hmmstat|phmmer|jackhmmer|nhmmer|nhmmscan'
NORMALISATION = [
    'CRLF becomes LF.',
    'HMM ASCII DATE values and HMMER table # Date values become <timestamp>.',
    'HMMER # Current dir becomes <working-directory>.',
    'Only the executable path in HMMER # Option settings and COM command metadata is reduced to its program basename.',
    'HMMER # CPU time and # Mc/sec lines retain their labels but replace measured runtimes with <runtime>.',
    'All other output characters, ordering and reported numeric precision are exact; no numeric tolerance.'
]


def digest(value):
    return hashlib.sha256(json.dumps(value, separators=(',', ':'), sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def safe_file(directory, name):
    path = PurePosixPath(name)
    if not name or path.is_absolute() or str(path) != name or any(p in ('..', '.') for p in path.parts):
        raise ValueError('unsafe artifact path: ' + name)
    base = Path(directory).resolve()
    resolved = (base / name).resolve(strict=True)
    if not resolved.is_relative_to(base) or not resolved.is_file():
        raise ValueError('artifact escapes result directory: ' + name)
    return resolved


def read_text(path):
    text = Path(path).read_bytes().decode('utf-8', errors='strict').replace('\r\n', '\n')
    if any(unicodedata.category(char) in ('Cc', 'Cf', 'Zl', 'Zp') and char not in '\n\t' for char in text):
        raise ValueError('invalid control character in ' + str(path))
    return text


def normalise(text, kind):
    if kind not in ('hmmer-text', 'hmmer-table', 'hmm'):
        return text
    lines = []
    for line in text.splitlines(keepends=True):
        ending = '\n' if line.endswith('\n') else ''
        body = line.removesuffix('\n')
        if kind == 'hmm' and re.fullmatch(r'DATE  .+', body):
            body = 'DATE  <timestamp>'
        elif re.fullmatch(r'# Date:\s+.+', body):
            body = '# Date: <timestamp>'
        elif re.fullmatch(r'# Current dir:\s+.+', body):
            body = '# Current dir: <working-directory>'
        elif re.fullmatch(r'# CPU time: [0-9.]+u [0-9.]+s [0-9:.]+ Elapsed: [0-9:.]+', body):
            body = '# CPU time: <runtime>'
        elif re.fullmatch(r'# Mc/sec: (?:[0-9.eE+-]+|inf|nan)', body):
            body = '# Mc/sec: <runtime>'
        elif body.startswith('# Option settings:'):
            body = re.sub(r'^(# Option settings:\s+)(?:\S*/)?(' + PROGRAMS + r')(?=\s|$)', r'\1\2', body)
        elif kind == 'hmm' and body.startswith('COM '):
            body = re.sub(r'^(COM\s+\[[0-9]+\]\s+)(?:\S*/)?(' + PROGRAMS + r')(?=\s|$)', r'\1\2', body)
        lines.append(body + ending)
    return ''.join(lines)


def fasta_records(text):
    records = []
    for line in text.splitlines():
        if line.startswith('>'):
            if not line[1:].strip():
                raise ValueError('FASTA header missing')
            records.append([line[1:], ''])
        elif line:
            if not records or not re.fullmatch(r'[A-Za-z*.-]+', line):
                raise ValueError('invalid FASTA sequence')
            records[-1][1] += line
    if not records or any(not sequence for _, sequence in records):
        raise ValueError('empty FASTA record set or sequence')
    ids = [header.split()[0] for header, _ in records]
    if len(set(ids)) != len(ids):
        raise ValueError('duplicate FASTA sequence identifier')
    return records


def invariants(text, artifact):
    kind = artifact['kind']
    if kind == 'fasta':
        records = fasta_records(text)
        result = {'records': len(records), 'residues': sum(len(seq) for _, seq in records), 'ids': [h.split()[0] for h, _ in records]}
    elif kind == 'gff':
        rows = [line.split('\t') for line in text.splitlines() if line and not line.startswith('#')]
        for row in rows:
            if len(row) != 9 or row[2] != 'CDS' or row[6] not in ('+', '-') or row[7] not in ('0', '1', '2'):
                raise ValueError('invalid Prodigal GFF record')
            if not row[3].isdigit() or not row[4].isdigit() or not 0 < int(row[3]) <= int(row[4]):
                raise ValueError('invalid GFF coordinates')
            if not re.search(r'(?:^|;)ID=[^;]+', row[8]):
                raise ValueError('GFF feature ID absent')
        result = {'records': len(rows), 'forward': sum(row[6] == '+' for row in rows), 'reverse': sum(row[6] == '-' for row in rows)}
    elif kind == 'hmmer-table':
        rows = [line.split() for line in text.splitlines() if line and not line.startswith('#')]
        table = artifact.get('table', 'sequence')
        fields = {'sequence': 18, 'domain': 22, 'dna': 15}[table]
        for row in rows:
            if len(row) < fields:
                raise ValueError('truncated HMMER ' + table + ' table')
            numeric = range(4, 18) if table == 'sequence' else ([2, 5] + list(range(6, 22)) if table == 'domain' else list(range(4, 11)) + [12, 13, 14])
            for index in numeric:
                if not re.fullmatch(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?', row[index]):
                    raise ValueError('invalid finite HMMER numeric column: ' + row[index])
            if table == 'domain':
                for lo, hi in ((15, 16), (17, 18), (19, 20)):
                    if not 0 < int(row[lo]) <= int(row[hi]):
                        raise ValueError('invalid HMMER domain coordinates')
                if not 0 <= float(row[21]) <= 1:
                    raise ValueError('invalid posterior alignment accuracy')
        result = {'records': len(rows), 'targets': len(set(row[0] for row in rows)), 'queries': len(set(row[3 if table == 'domain' else 2] for row in rows))}
    elif kind == 'stockholm':
        if not text.startswith('# STOCKHOLM 1.0\n') or text.count('\n//') != 1 or not text.rstrip().endswith('//'):
            raise ValueError('invalid Stockholm alignment boundaries')
        seqs = {}
        for line in text.splitlines():
            if not line or line.startswith('#') or line == '//':
                continue
            fields = line.split()
            if len(fields) != 2 or not re.fullmatch(r'[A-Za-z.*~_-]+', fields[1]):
                raise ValueError('invalid Stockholm sequence row')
            seqs[fields[0]] = seqs.get(fields[0], '') + fields[1]
        if not seqs or len(set(map(len, seqs.values()))) != 1:
            raise ValueError('empty or inconsistent Stockholm alignment')
        result = {'records': len(seqs), 'columns': len(next(iter(seqs.values())))}
    elif kind == 'hmm':
        models = text.rstrip().split('\n//')
        if models[-1] != '':
            raise ValueError('HMM lacks model terminator')
        models.pop()
        if not models or any(not model.lstrip().startswith('HMMER3/') or not re.search(r'^NAME  \S+', model, re.M) or not re.search(r'^LENG  [1-9][0-9]*$', model, re.M) for model in models):
            raise ValueError('invalid HMM model header')
        result = {'records': len(models), 'lengths': [int(re.search(r'^LENG  ([0-9]+)$', model, re.M)[1]) for model in models]}
    elif kind in ('text', 'hmmer-text'):
        result = {'records': len(text.splitlines())}
    else:
        raise ValueError('unknown artifact kind: ' + kind)
    if result['records'] < artifact.get('minimumRecords', 1):
        raise ValueError('required record coverage absent: ' + artifact['path'])
    for key, minimum in artifact.get('minimum', {}).items():
        if result.get(key, -1) < minimum:
            raise ValueError('required invariant absent: ' + key)
    return result


def parse_artifact(path, artifact):
    raw = Path(path).read_bytes()
    if not raw and not artifact.get('allowEmpty', False):
        raise ValueError('empty artifact: ' + artifact['path'])
    if artifact['kind'] == 'exact':
        return raw.hex(), {'bytes': len(raw), 'records': 1}
    text = read_text(path)
    evidence = invariants(text, artifact)
    return normalise(text, artifact['kind']), evidence


def compare(expected_dir, actual_dir, spec):
    artifacts = spec.get('artifacts', [])
    paths = [artifact['path'] for artifact in artifacts]
    if not artifacts or len(paths) != len(set(paths)):
        raise ValueError('empty or duplicate artifact plan')
    biological = spec.get('biologicalArtifacts', paths)
    if not biological or len(biological) != len(set(biological)) or any(path not in paths for path in biological):
        raise ValueError('invalid biological artifact selection')
    expected, actual, left_inv, right_inv, left_hash, right_hash, left_bytes, right_bytes = {}, {}, {}, {}, {}, {}, {}, {}
    for artifact in artifacts:
        name = artifact['path']
        left, right = safe_file(expected_dir, name), safe_file(actual_dir, name)
        expected[name], left_inv[name] = parse_artifact(left, artifact)
        actual[name], right_inv[name] = parse_artifact(right, artifact)
        left_raw, right_raw = left.read_bytes(), right.read_bytes()
        left_hash[name], right_hash[name] = hashlib.sha256(left_raw).hexdigest(), hashlib.sha256(right_raw).hexdigest()
        left_bytes[name], right_bytes[name] = len(left_raw), len(right_raw)
    def biology(values):
        return [('\n'.join(line for line in values[name].splitlines() if line and not line.startswith('#')) if next(a for a in artifacts if a['path'] == name)['kind'] == 'hmmer-table' else values[name]) for name in biological]
    return {'equal': expected == actual, 'semanticDigest': digest(actual), 'expectedSemanticDigest': digest(expected),
            'biologicalDigest': digest(biology(actual)), 'expectedBiologicalDigest': digest(biology(expected)),
            'nativeInvariants': left_inv, 'invariants': right_inv, 'expectedFiles': left_hash, 'actualFiles': right_hash,
            'expectedFileBytes': left_bytes, 'actualFileBytes': right_bytes, 'normalisation': NORMALISATION,
            'numericTolerance': 0, 'differences': [name for name in paths if expected[name] != actual[name]]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('expected', type=Path)
    parser.add_argument('actual', type=Path)
    parser.add_argument('--tool', choices=['prodigal', 'hmmer'], required=True)
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
    result.update({'schema': 1, 'tool': args.tool, 'dataset': args.dataset, 'case': args.case, 'actualTarget': args.actual_target,
                   'manifestSha256': hashlib.sha256(manifest_raw).hexdigest()})
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result['equal'] else 1)


if __name__ == '__main__':
    main()
