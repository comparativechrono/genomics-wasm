#!/usr/bin/env python3
"""Strict biological comparison of RNA analysis output, independent of gzip headers.

Only command/provenance comments, input-path prefixes, row/attribute order and
gzip wrapper metadata are normalised. Gene and transcript identities remain exact. Numeric fields remain
exact Decimal values. Each side independently passes structural validation.
"""
import argparse
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
import gzip
import hashlib
import json
from pathlib import Path
import re
import struct


def number(value, label, integer=False):
    try:
        result = Decimal(value)
    except (InvalidOperation, TypeError):
        raise ValueError(f"{label}: invalid number {value!r}")
    if not result.is_finite() or result < 0 or (integer and result != result.to_integral_value()):
        raise ValueError(f"{label}: expected a finite non-negative {'integer' if integer else 'number'}")
    return result


def canonical(value):
    if isinstance(value, Decimal):
        return format(value.normalize(), 'f') if value else '0'
    if isinstance(value, dict):
        return {str(key): canonical(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, (list, tuple)):
        return [canonical(item) for item in value]
    return value


def digest(value):
    return hashlib.sha256(json.dumps(canonical(value), sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def lines(path):
    if not path.is_file():
        raise ValueError(f"missing required output: {path}")
    text = gzip.open(path, 'rt', encoding='utf-8').read() if path.suffix == '.gz' else path.read_text(encoding='utf-8')
    if not text.strip():
        raise ValueError(f"empty required output: {path}")
    return [line for line in text.splitlines() if line]


def reference_lengths(path):
    lengths, name = {}, None
    for line in path.read_text().splitlines():
        if line.startswith('>'):
            name = line[1:].split()[0]
            if name in lengths:
                raise ValueError('duplicate reference contig')
            lengths[name] = 0
        elif line.strip():
            if name is None or not re.fullmatch('[ACGTNacgtn]+', line.strip()):
                raise ValueError('invalid reference FASTA')
            lengths[name] += len(line.strip())
    if not lengths or any(length <= 0 for length in lengths.values()):
        raise ValueError('empty reference FASTA')
    return lengths


def interval(chrom, start, end, refs, label, one_based=False):
    start = int(number(start, label + ': start', True))
    end = int(number(end, label + ': end', True))
    if chrom not in refs or (start < 1 if one_based else start < 0) or (end < start if one_based else end <= start) or end > refs[chrom]:
        raise ValueError(f'{label}: interval outside the independent reference')
    return start, end


def attributes(text):
    result = {}
    pattern = re.compile(r'\s*([^\s;]+)\s+"([^"]*)"\s*;?')
    pos = 0
    while pos < len(text):
        if not text[pos:].strip():
            break
        match = pattern.match(text, pos)
        if not match:
            raise ValueError('invalid GTF attributes')
        key, value = match.groups()
        if key in result:
            raise ValueError('duplicate GTF attribute ' + key)
        result[key] = value
        pos = match.end()
    return result


def featurecounts(directory, context):
    table = lines(directory / 'counts.tsv')
    comments = [line for line in table if line.startswith('#')]
    versions = [re.search(r'featureCounts\s+v?([0-9.]+)', line) for line in comments]
    versions = [match.group(1) for match in versions if match]
    if not versions or len(set(versions)) != 1:
        raise ValueError('featureCounts version comment missing or ambiguous')
    table = [line for line in table if not line.startswith('#')]
    header = table[0].split('\t')
    if header[:6] != ['Geneid', 'Chr', 'Start', 'End', 'Strand', 'Length'] or len(header) < 7:
        raise ValueError('invalid featureCounts table header')
    samples = [Path(name).name for name in header[6:]]
    if len(set(samples)) != len(samples):
        raise ValueError('duplicate featureCounts input sample basenames')
    records, totals = {}, [Decimal(0)] * len(samples)
    for line in table[1:]:
        row = line.split('\t')
        if len(row) != len(header):
            raise ValueError('ragged featureCounts row')
        chroms, starts, ends, strands = [item.split(';') for item in row[1:5]]
        if not len(chroms) == len(starts) == len(ends) == len(strands):
            raise ValueError('featureCounts annotation coordinate count mismatch')
        loci = []
        for chrom, start, end, strand in zip(chroms, starts, ends, strands):
            a, b = interval(chrom, start, end, context['refs'], 'featureCounts', True)
            if strand not in ['+', '-', '.']:
                raise ValueError('invalid featureCounts strand')
            loci.append((chrom, a, b, strand))
        length = number(row[5], 'feature length', True)
        if not length or length > sum(end - start + 1 for _, start, end, _ in loci):
            raise ValueError('invalid featureCounts feature length')
        key = (row[0], tuple(sorted(loci)), length)
        if key in records and not context.get('feature_level'):
            raise ValueError('duplicate featureCounts feature')
        if context.get('feature_level'):
            occurrence = sum(1 for existing in records if existing[:3] == key)
            key = (*key, occurrence)
        values = [number(value, 'feature count') for value in row[6:]]
        records[key] = values
        totals = [a + b for a, b in zip(totals, values)]
    if not records:
        raise ValueError('featureCounts output contains no annotated features')
    annotation_ids = context.get('annotation_ids')
    if annotation_ids is not None and {key[0] for key in records} != annotation_ids:
        raise ValueError('featureCounts gene identities differ from independent annotation')
    summary_lines = lines(directory / 'counts.tsv.summary')
    sh = summary_lines[0].split('\t')
    if sh[0] != 'Status' or [Path(name).name for name in sh[1:]] != samples:
        raise ValueError('featureCounts summary sample columns differ from counts')
    summary = {}
    for line in summary_lines[1:]:
        row = line.split('\t')
        if len(row) != len(sh) or row[0] in summary:
            raise ValueError('invalid featureCounts summary row')
        summary[row[0]] = [number(value, 'assignment summary', True) for value in row[1:]]
    if 'Assigned' not in summary or len(summary) < 2:
        raise ValueError('incomplete featureCounts assignment summary')
    if any(sum(values[i] for values in summary.values()) <= 0 for i in range(len(samples))):
        raise ValueError('featureCounts summary did not account for any reads')
    if context.get('require_nonzero', True) and not sum(totals):
        raise ValueError('featureCounts test is vacuous: all counts are zero')
    return {'version': versions[0], 'samples': samples, 'records': records, 'summary': summary}, {'features': len(records), 'countSum': totals, 'assigned': summary['Assigned']}


def gtf(path, refs, require_nonzero=True, require_records=True):
    rows, transcripts, exons, versions = [], {}, defaultdict(list), []
    for line in lines(path):
        if line.startswith('#'):
            match = re.fullmatch(r'# StringTie version (.+)', line)
            if match: versions.append(match.group(1))
            continue
        row = line.split('\t')
        if len(row) != 9 or row[2] not in ['transcript', 'exon']:
            raise ValueError('invalid StringTie GTF feature')
        start, end = interval(row[0], row[3], row[4], refs, 'StringTie GTF', True)
        if row[6] not in ['+', '-', '.']:
            raise ValueError('invalid StringTie GTF strand')
        attrs = attributes(row[8])
        if not attrs.get('transcript_id') or not attrs.get('gene_id'):
            raise ValueError('missing StringTie transcript or gene identifier')
        tid = attrs['transcript_id']
        if row[2] == 'transcript':
            if tid in transcripts:
                raise ValueError('duplicate transcript record')
            transcripts[tid] = (row[0], start, end, row[6], attrs)
        else:
            exons[tid].append((row[0], start, end, row[6]))
        for key in ['cov', 'FPKM', 'TPM']:
            if key in attrs:
                attrs[key] = number(attrs[key], 'GTF ' + key)
        rows.append((tuple(row[:3]) + (start, end, row[5], row[6], row[7]), attrs))
    if len(versions) != 1:
        raise ValueError('missing or duplicate StringTie version metadata')
    if (not transcripts and require_records) or set(transcripts) != set(exons):
        raise ValueError('GTF requires transcripts and matching exon records')
    structures = {}
    for tid, (chrom, start, end, strand, attrs) in transcripts.items():
        chain = tuple(sorted(exons[tid]))
        if len(set(chain)) != len(chain) or any(c != chrom or a < start or b > end or s != strand for c, a, b, s in chain):
            raise ValueError('invalid or duplicate transcript exon chain')
        if any(chain[i][2] >= chain[i + 1][1] for i in range(len(chain) - 1)):
            raise ValueError('overlapping exons within a transcript')
        structures[tid] = (chrom, start, end, strand, chain)
    if len(set(structures.values())) != len(structures):
        # Duplicate chains can still differ by reference association; preserve multiplicity below.
        pass
    gene_members = defaultdict(list)
    for tid, item in transcripts.items():
        gene_members[item[4]['gene_id']].append(structures[tid])
    normal = []
    for fields, attrs in rows:
        mapped = dict(attrs)
        if attrs['gene_id'] != transcripts[attrs['transcript_id']][4]['gene_id']:
            raise ValueError('exon gene identity disagrees with parent transcript')
        normal.append((fields, mapped))
    normal.sort(key=lambda row: json.dumps(canonical(row), sort_keys=True))
    coverage = sum(item[4].get('cov', Decimal(0)) for item in transcripts.values())
    if require_nonzero and coverage <= 0:
        raise ValueError('StringTie test is vacuous: no positive transcript coverage')
    return {'version': versions[0], 'records': normal}, {'transcripts': len(transcripts), 'exons': sum(map(len, exons.values())), 'coverageSum': coverage}


def stringtie(directory, context):
    records, invariants = gtf(directory / 'transcripts.gtf', context['refs'], context.get('require_nonzero', True), context.get('require_records', True))
    semantic = {'gtf': records}
    if 'abundance.tsv' in context['outputs']:
        table = lines(directory / 'abundance.tsv')
        if table[0].split('\t') != ['Gene ID', 'Gene Name', 'Reference', 'Strand', 'Start', 'End', 'Coverage', 'FPKM', 'TPM']:
            raise ValueError('invalid StringTie gene abundance header')
        abundance = []
        for line in table[1:]:
            row = line.split('\t')
            if len(row) != 9:
                raise ValueError('ragged StringTie gene abundance row')
            a, b = interval(row[2], row[4], row[5], context['refs'], 'StringTie abundance', True)
            if row[3] not in ['+', '-', '.']:
                raise ValueError('invalid abundance strand')
            # Preserve reference and generated gene identities; observed single-thread outputs are deterministic.
            identity = row[0]
            abundance.append([identity, row[1], row[2], row[3], a, b] + [number(v, 'gene abundance') for v in row[6:]])
        if not abundance and context.get('require_records', True):
            raise ValueError('empty StringTie abundance table')
        semantic['abundance'] = sorted(abundance, key=lambda row: json.dumps(canonical(row)))
        invariants['genes'] = len(abundance)
    return semantic, invariants


def coverage_table(path, refs, integer_depth=False):
    rows, previous = [], {}
    for line in lines(path):
        row = line.split('\t')
        if len(row) < 4:
            raise ValueError('invalid mosdepth BED output')
        start, end = interval(row[0], row[1], row[2], refs, path.name)
        if path.name.endswith('.per-base.bed.gz') and start < previous.get(row[0], 0):
            raise ValueError('overlapping or unordered per-base intervals')
        previous[row[0]] = end
        values = [number(row[-1], path.name, integer_depth)]
        rows.append([row[0], start, end, row[3:-1], values[0]])
    if path.name.endswith('.per-base.bed.gz'):
        for chrom in {row[0] for row in rows}:
            selected = [row for row in rows if row[0] == chrom]
            if selected[0][1] != 0 or selected[-1][2] != refs[chrom] or any(a[2] != b[1] for a, b in zip(selected, selected[1:])):
                raise ValueError('per-base coverage does not tile its reference contig')
    return rows


def check_csi(raw, refs):
    if raw[:2] == b'\x1f\x8b':
        raw = gzip.decompress(raw)
    if raw[:4] != b'CSI\x01':
        raise ValueError('invalid CSI coverage index')
    offset = 4
    def take(fmt):
        nonlocal offset
        size = struct.calcsize(fmt)
        if offset + size > len(raw): raise ValueError('truncated CSI index')
        value = struct.unpack_from(fmt, raw, offset); offset += size
        return value
    min_shift, depth, aux_size = take('<iii')
    if not (0 <= min_shift <= 31 and 0 <= depth <= 10 and 0 <= aux_size <= len(raw) - offset): raise ValueError('invalid CSI header')
    offset += aux_size
    nref, = take('<i')
    if not 0 < nref <= len(refs): raise ValueError('invalid CSI reference count')
    for _ in range(nref):
        nbins, = take('<i')
        if not 0 <= nbins <= len(raw) // 16: raise ValueError('invalid CSI bin count')
        bins = set()
        for _ in range(nbins):
            bin_id, loffset, nchunks = take('<IQi')
            if bin_id in bins or not 0 <= nchunks <= len(raw) // 16: raise ValueError('invalid CSI bins/chunks')
            bins.add(bin_id)
            for _ in range(nchunks):
                begin, end = take('<QQ')
                # The reserved metadata bin stores mapped/unmapped counts, not virtual offsets.
                if bin_id < ((1 << (3 * (depth + 1))) - 1) // 7 and begin > end: raise ValueError('reversed CSI chunk')
    if len(raw) - offset not in [0, 8]: raise ValueError('trailing/truncated CSI data')
    if len(raw) - offset == 8: take('<Q')


def mosdepth(directory, context):
    semantic, invariants = {}, {}
    for output in context['outputs']:
        path = directory / output
        if output.endswith('.csi'):
            if not path.is_file():
                raise ValueError('missing coverage index ' + output)
            raw = path.read_bytes()
            check_csi(raw, context['refs'])
            continue
        if output.endswith('.mosdepth.summary.txt'):
            table = lines(path)
            if table[0].split('\t') != ['chrom', 'length', 'bases', 'mean', 'min', 'max']:
                raise ValueError('invalid mosdepth summary header')
            summary = {}
            for line in table[1:]:
                row = line.split('\t')
                if len(row) != 6 or row[0] in summary:
                    raise ValueError('invalid mosdepth summary row')
                values = [number(x, 'mosdepth summary', i != 2) for i, x in enumerate(row[1:])]
                if values[3] > values[4] or values[2] < values[3] or values[2] > values[4]:
                    raise ValueError('mosdepth mean/min/max inconsistent')
                if row[0] in context['refs'] and values[0] != context['refs'][row[0]]:
                    raise ValueError('mosdepth contig length differs from reference')
                if not values[0] or abs(values[1] / values[0] - values[2]) > Decimal('0.0051'):
                    raise ValueError('mosdepth rounded mean does not match bases/length')
                summary[row[0]] = values
            if 'total' not in summary or not any(chrom in summary for chrom in context['refs']):
                raise ValueError('missing mosdepth total or reference contigs')
            if context.get('require_nonzero', True) and summary['total'][1] <= 0:
                raise ValueError('mosdepth test is vacuous: no covered bases')
            semantic[output] = summary
            invariants['coveredBaseSum'] = summary['total'][1]
        elif output.endswith('.dist.txt'):
            rows = []
            distributions = defaultdict(dict)
            for line in lines(path):
                row = line.split('\t')
                if len(row) != 3:
                    raise ValueError('invalid mosdepth depth distribution')
                depth, fraction = number(row[1], 'distribution depth', True), number(row[2], 'distribution fraction')
                if fraction > 1 or depth in distributions[row[0]]:
                    raise ValueError('invalid cumulative coverage distribution')
                distributions[row[0]][depth] = fraction
                rows.append([row[0], depth, fraction])
            for chrom, values in distributions.items():
                ordered = sorted(values.items())
                if ordered[0] != (0, 1) or any(a[1] < b[1] for a, b in zip(ordered, ordered[1:])):
                    raise ValueError('invalid cumulative coverage distribution')
            semantic[output] = sorted(rows)
        elif output.endswith('.thresholds.bed.gz'):
            table = lines(path)
            if not table[0].startswith('#chrom\tstart\tend\tregion\t'):
                raise ValueError('invalid mosdepth threshold header')
            rows = []
            for line in table[1:]:
                row = line.split('\t'); a, b = interval(row[0], row[1], row[2], context['refs'], 'thresholds')
                values = [number(x, 'threshold base count', True) for x in row[4:]]
                if len(row) != len(table[0].split('\t')) or any(v > b - a for v in values) or any(x < y for x, y in zip(values, values[1:])):
                    raise ValueError('invalid threshold base counts')
                rows.append([row[0], a, b, row[3], values])
            semantic[output] = {'header': table[0], 'rows': sorted(rows)}
        elif output.endswith('.quantized.bed.gz'):
            rows = []
            for line in lines(path):
                row = line.split('\t')
                if len(row) != 4:
                    raise ValueError('invalid quantised coverage record')
                a, b = interval(row[0], row[1], row[2], context['refs'], 'quantised coverage')
                if not re.fullmatch(r'\d+:(?:\d+|inf)', row[3]):
                    raise ValueError('invalid quantised coverage label')
                rows.append([row[0], a, b, row[3]])
            semantic[output] = sorted(rows)
        elif output.endswith('.bed.gz'):
            rows = coverage_table(path, context['refs'], output.endswith('.per-base.bed.gz'))
            semantic[output] = rows
            invariants[output + ':intervals'] = len(rows)
        else:
            raise ValueError('unrecognised mosdepth output ' + output)
    per_base = next((value for key, value in semantic.items() if key.endswith('.per-base.bed.gz')), None)
    summary = next((value for key, value in semantic.items() if key.endswith('.mosdepth.summary.txt')), None)
    if per_base is not None:
        for chrom in {row[0] for row in per_base}:
            observed = sum((row[2] - row[1]) * row[4] for row in per_base if row[0] == chrom)
            if not summary or chrom not in summary or observed != summary[chrom][1]:
                raise ValueError('per-base coverage sum disagrees with summary')
    if 'coveredBaseSum' not in invariants:
        raise ValueError('mosdepth summary was not validated')
    return semantic, invariants


LOADERS = {'featurecounts': featurecounts, 'stringtie': stringtie, 'mosdepth': mosdepth}


def compare_directories(expected, actual, tool, context):
    expected, actual = Path(expected), Path(actual)
    for path in context['outputs']:
        for directory in [expected, actual]:
            if not (directory / path).is_file():
                raise ValueError(f'missing required output {directory / path}')
    left, left_invariants = LOADERS[tool](expected, context)
    right, right_invariants = LOADERS[tool](actual, context)
    left_hash, right_hash = digest(left), digest(right)
    return {'schema': 1, 'tool': tool, 'equal': left_hash == right_hash,
            'expectedSemanticDigest': left_hash, 'semanticDigest': right_hash,
            'biologicalDigest': digest({key: value for key, value in right.items() if key not in ['version', 'samples']}),
            'invariants': canonical(right_invariants), 'nativeInvariants': canonical(left_invariants),
            'normalisation': ['command/provenance comments', 'sample path prefixes', 'row/attribute order',
                              'gzip wrapper and structurally validated CSI binary layout'],
            'numericPolicy': 'exact Decimal equality; no numeric tolerances'}


def load_context(manifest_path, dataset, case):
    manifest = json.loads(manifest_path.read_text())
    data = manifest['datasets'][dataset]
    refs = reference_lengths(manifest_path.parent / data['reference'])
    ids = set()
    for line in (manifest_path.parent / data['annotation']).read_text().splitlines():
        if line and not line.startswith('#'):
            row = line.split('\t')
            if len(row) == 9 and row[2] == 'exon':
                attrs = attributes(row[8])
                if 'gene_id' in attrs:
                    ids.add(attrs['gene_id'])
    return {'refs': refs, 'annotation_ids': ids, 'outputs': case['outputs'], 'require_nonzero': case.get('requireNonzero', True), 'feature_level': case.get('featureLevel', False), 'require_records': case.get('requireRecords', True)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('tool', choices=LOADERS)
    parser.add_argument('expected', type=Path); parser.add_argument('actual', type=Path)
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--dataset', required=True); parser.add_argument('--case', required=True)
    parser.add_argument('--report', required=True, type=Path)
    args = parser.parse_args()
    plan = json.loads((Path(__file__).parent / 'cases.json').read_text())
    case = next(c for c in plan['tools'][args.tool]['cases'] if c['id'] == args.case)
    try:
        result = compare_directories(args.expected, args.actual, args.tool, load_context(args.manifest, args.dataset, case))
    except Exception as error:
        result = {'schema': 1, 'tool': args.tool, 'equal': False, 'error': str(error)}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    return 0 if result.get('equal') else 1


if __name__ == '__main__':
    raise SystemExit(main())
