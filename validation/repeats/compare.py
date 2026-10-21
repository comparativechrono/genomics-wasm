#!/usr/bin/env python3
"""Compare repeat calls, graph alignments and SVGs without numeric tolerances.

JSON/VCF dictionaries, BAM auxiliary tags and XML attributes are mappings.
BAM records form a multiset because upstream EH iterates an unordered read map;
duplicate multiplicity and every alignment field remain significant. Everything
else, including VCF/XML order, genotype phase, intervals, depths, graph tags and
SVG geometry, is exact. BAM compression is decoded; raw bytes are hash-bound.
"""
from __future__ import annotations
import argparse
import gzip
import hashlib
import json
import math
from pathlib import Path
import re
import struct
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def unique_object(pairs):
    result = {}
    for name, value in pairs:
        require(name not in result, 'Duplicate JSON property ' + name)
        result[name] = value
    return result


def json_data(raw):
    return json.loads(raw, object_pairs_hook=unique_object,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Nonfinite JSON number ' + value)))


def info_map(text):
    result = {}
    if text == '.':
        return result
    for field in text.split(';'):
        key, separator, value = field.partition('=')
        require(key and key not in result, 'Duplicate/empty VCF INFO field')
        result[key] = value if separator else True
    return result


def parse_vcf(raw):
    headers, records, samples = [], [], None
    for line in raw.decode().splitlines():
        if line.startswith('##'):
            headers.append(line)
            continue
        if line.startswith('#CHROM\t'):
            require(samples is None, 'Duplicate VCF column header')
            samples = line.split('\t')[9:]
            continue
        if not line:
            continue
        require(samples is not None, 'VCF record before header')
        fields = line.split('\t')
        require(len(fields) == (9 + len(samples) if samples else 8), 'Invalid VCF column count')
        require(int(fields[1]) > 0, 'Invalid VCF coordinate')
        record = dict(zip(('chrom', 'pos', 'id', 'ref', 'alt', 'qual', 'filter'), fields[:7]))
        record['info'] = info_map(fields[7])
        if samples:
            keys = fields[8].split(':')
            require(len(keys) == len(set(keys)), 'Duplicate VCF FORMAT field')
            record['samples'] = []
            for sample in fields[9:]:
                values = sample.split(':')
                require(len(values) <= len(keys), 'Extra VCF FORMAT values')
                record['samples'].append(dict(zip(keys, values + ['.'] * (len(keys) - len(values)))))
        records.append(record)
    require(samples is not None, 'Missing VCF column header')
    return {'headers': headers, 'samples': samples, 'records': records}


def bam_aux(raw):
    values, offset = {}, 0
    formats = {'c': 'b', 'C': 'B', 's': 'h', 'S': 'H', 'i': 'i', 'I': 'I', 'f': 'f', 'd': 'd'}
    while offset < len(raw):
        require(offset + 3 <= len(raw), 'Truncated BAM auxiliary tag')
        name = raw[offset:offset + 2].decode('ascii')
        kind = chr(raw[offset + 2])
        offset += 3
        require(name not in values, 'Duplicate BAM tag ' + name)
        if kind in formats:
            fmt = '<' + formats[kind]
            size = struct.calcsize(fmt)
            require(offset + size <= len(raw), 'Truncated numeric BAM tag')
            value = struct.unpack_from(fmt, raw, offset)[0]
            offset += size
            require(not isinstance(value, float) or math.isfinite(value), 'Nonfinite BAM numeric tag')
        elif kind == 'A':
            require(offset < len(raw), 'Truncated BAM character tag')
            value = chr(raw[offset])
            offset += 1
        elif kind in ('Z', 'H'):
            end = raw.find(b'\0', offset)
            require(end >= offset, 'Unterminated BAM string tag')
            value = raw[offset:end].decode()
            offset = end + 1
        elif kind == 'B':
            require(offset + 5 <= len(raw), 'Truncated BAM array tag')
            subtype = chr(raw[offset])
            require(subtype in formats, 'Invalid BAM array type')
            count = struct.unpack_from('<I', raw, offset + 1)[0]
            offset += 5
            size = struct.calcsize(formats[subtype]) * count
            require(offset + size <= len(raw), 'Truncated BAM array values')
            items = list(struct.unpack_from('<' + str(count) + formats[subtype], raw, offset))
            require(all(not isinstance(x, float) or math.isfinite(x) for x in items), 'Nonfinite BAM array')
            value = {'type': subtype, 'values': items}
            offset += size
        else:
            raise ValueError('Unknown BAM tag type ' + kind)
        values[name] = {'type': kind, 'value': value}
    return values


def parse_bam(raw):
    data = gzip.decompress(raw)
    require(data.startswith(b'BAM\1'), 'Missing BAM magic')
    offset = 4

    def integer():
        nonlocal offset
        require(offset + 4 <= len(data), 'Truncated BAM integer')
        value = struct.unpack_from('<i', data, offset)[0]
        offset += 4
        return value

    def chunk(size):
        nonlocal offset
        require(size >= 0 and offset + size <= len(data), 'Truncated BAM data')
        value = data[offset:offset + size]
        offset += size
        return value

    header = chunk(integer()).decode()
    references = []
    count = integer()
    require(count >= 0, 'Invalid BAM reference count')
    for _ in range(count):
        name = chunk(integer())
        require(name.endswith(b'\0'), 'Unterminated BAM reference name')
        length = integer()
        require(length >= 0, 'Invalid BAM reference length')
        references.append({'name': name[:-1].decode(), 'length': length})
    records = []
    while offset < len(data):
        record = chunk(integer())
        require(len(record) >= 32, 'Truncated BAM alignment')
        ref, position, bin_mq_name, flag_cigar, length, mate_ref, mate_pos, template_length = struct.unpack_from('<iiIIiiii', record)
        name_size, mapq, bin_number = bin_mq_name & 255, (bin_mq_name >> 8) & 255, bin_mq_name >> 16
        cigar_size, flag = flag_cigar & 65535, flag_cigar >> 16
        require(length >= 0 and name_size > 0, 'Invalid BAM sequence/name length')
        boundary = 32 + name_size + 4 * cigar_size + (length + 1) // 2 + length
        require(boundary <= len(record), 'Truncated BAM alignment payload')
        name = record[32:32 + name_size]
        require(name.endswith(b'\0'), 'Unterminated BAM read name')
        cursor = 32 + name_size
        cigar = list(struct.unpack_from('<' + str(cigar_size) + 'I', record, cursor))
        cursor += 4 * cigar_size
        packed = record[cursor:cursor + (length + 1) // 2]
        sequence = ''.join('=ACMGRSVTWYHKDBN'[(packed[i // 2] >> (4 if i % 2 == 0 else 0)) & 15] for i in range(length))
        cursor += (length + 1) // 2
        qualities = list(record[cursor:cursor + length])
        cursor += length
        records.append({'name': name[:-1].decode(), 'ref': ref, 'position': position, 'mapq': mapq,
                        'bin': bin_number, 'flag': flag, 'cigar': cigar, 'sequence': sequence,
                        'qualities': qualities, 'mateRef': mate_ref, 'matePosition': mate_pos,
                        'templateLength': template_length, 'tags': bam_aux(record[cursor:])})
    return {'header': header, 'references': references, 'records': records}


def parse_svg(raw):
    require(b'<!DOCTYPE' not in raw.upper() and b'<!ENTITY' not in raw.upper(), 'SVG declarations are unsupported')
    element = ET.fromstring(raw)
    require(element.tag in ('svg', '{http://www.w3.org/2000/svg}svg'), 'Not an SVG document')

    def node(item):
        return {'tag': item.tag, 'attributes': dict(item.attrib), 'text': item.text or '',
                'tail': item.tail or '', 'children': [node(child) for child in item]}

    return node(element)


def canonical(raw, kind):
    if kind == 'json':
        return json_data(raw)
    if kind == 'vcf':
        return parse_vcf(raw)
    if kind == 'bam':
        value = parse_bam(raw)
        value['records'].sort(key=encoded)
        return value
    if kind == 'svg':
        return parse_svg(raw)
    if kind == 'tsv':
        return [row.split('\t') for row in raw.decode().splitlines()]
    if kind == 'text':
        return raw.decode()
    if kind == 'bytes':
        return {'sha256': sha(raw), 'bytes': len(raw)}
    raise ValueError('Unknown artifact type ' + kind)


def json_path(value, path):
    for name in path.split('/'):
        if name:
            value = value[int(name)] if isinstance(value, list) else value[name.replace('~1', '/').replace('~0', '~')]
    return value


def contract(raw, kind, specification, inputs):
    value, checks = canonical(raw, kind), {}

    def check(name, valid, observed):
        require(valid, f'{name} contract failed: {observed!r}')
        checks[name] = observed

    for name, expected in specification.items():
        if name == 'exact':
            check(name, value == expected, value)
        elif name == 'jsonPaths':
            observed = {path: json_path(value, path) for path in expected}
            check(name, observed == expected, observed)
        elif name == 'jsonNoCalls':
            observed = {}
            for locus in expected:
                variants = value['LocusResults'][locus]['Variants']
                require(bool(variants), 'Missing uncalled variants ' + locus)
                for variant in variants.values():
                    require('Genotype' not in variant and 'GenotypeConfidenceInterval' not in variant,
                            'Empty input produced a genotype ' + locus)
                observed[locus] = sorted(variants)
            check(name, bool(observed), observed)
        elif name == 'repeatTruth':
            truth = json_data((inputs / expected['source']).read_bytes())['loci']
            observed = {}
            for locus, entry in truth.items():
                if locus not in expected.get('loci', truth):
                    continue
                result = value['LocusResults'][locus]
                repeat = [v for v in result['Variants'].values() if v['VariantType'] == 'Repeat']
                require(len(repeat) == 1, 'Expected one repeat in synthetic locus ' + locus)
                call = repeat[0]
                alleles = expected.get('overrides', {}).get(locus, entry['alleles'])
                copies = list(map(int, call['Genotype'].split('/')))
                require(result['AlleleCount'] == len(alleles) == len(copies), 'Wrong repeat ploidy ' + locus)
                if entry['assertion'] == 'exact':
                    require(sorted(copies) == sorted(alleles), 'Wrong independent repeat genotype ' + locus)
                elif entry['assertion'] == 'confidenceIntervalContains':
                    intervals = [list(map(int, interval.split('-'))) for interval in call['GenotypeConfidenceInterval'].split('/')]
                    require(len(intervals) == len(alleles), 'Wrong confidence interval ploidy')
                    require(all(low <= count <= high for (low, high), count in zip(intervals, sorted(alleles))), 'Known synthetic allele outside confidence interval')
                    require(max(copies) > call.get('ReferenceRepeatCount', min(alleles)) * 2, 'Expanded allele was not detected')
                else:
                    raise ValueError('Unknown repeat truth assertion')
                observed[locus] = {'genotype': call['Genotype'], 'confidenceInterval': call['GenotypeConfidenceInterval'], 'alleles': alleles}
            check(name, bool(observed), observed)
        elif name == 'vcfGenotypes':
            observed = {}
            for record in value['records']:
                identifier = record['info'].get('VARID')
                if identifier in expected:
                    require(len(record['samples']) == 1, 'Expected one VCF sample')
                    observed[identifier] = {field: record['samples'][0].get(field) for field in expected[identifier]}
            check(name, observed == expected, observed)
        elif name == 'minimumRecords':
            check(name, len(value['records']) >= expected, len(value['records']))
        elif name == 'recordCount':
            check(name, len(value['records']) == expected, len(value['records']))
        elif name == 'graphLoci':
            observed = sorted({record['tags'].get('XG', {}).get('value', '').split(',')[0] for record in value['records']})
            check(name, observed == sorted(expected), observed)
        elif name == 'coordinateSorted':
            positions = [(r['ref'] if r['ref'] >= 0 else 2**31, r['position']) for r in parse_bam(raw)['records']]
            check(name, positions == sorted(positions), len(positions))
        elif name == 'tsvHeader':
            check(name, value and value[0] == expected, value[0] if value else [])
        elif name == 'tsvRows':
            check(name, value[1:] == expected, value[1:])
        elif name == 'tsvMinimumRows':
            check(name, len(value) - 1 >= expected, len(value) - 1)
        elif name == 'metricsGenotypes':
            observed = {row[0]: row[1] for row in value[1:]}
            check(name, observed == expected, observed)
        elif name == 'metricsAlleles':
            observed = {row[0]: sorted(map(int, row[1].split('/'))) for row in value[1:]}
            check(name, observed == {variant: sorted(alleles) for variant, alleles in expected.items()}, observed)
        elif name == 'svgMinimumElements':
            def count(node):
                return 1 + sum(count(child) for child in node['children'])
            observed = count(value)
            check(name, observed >= expected, observed)
        elif name == 'svgContains':
            text = raw.decode()
            check(name, all(fragment in text for fragment in expected), expected)
        elif name == 'nonempty':
            check(name, len(raw) > 0, len(raw))
        else:
            raise ValueError('Unknown contract ' + name)
    return checks


def first_difference(a, b, path='$'):
    if type(a) != type(b):
        return {'path': path, 'expected': str(a)[:500], 'actual': str(b)[:500]}
    if isinstance(a, dict):
        if a.keys() != b.keys():
            return {'path': path, 'expectedKeys': sorted(a), 'actualKeys': sorted(b)}
        for key in a:
            if a[key] != b[key]:
                return first_difference(a[key], b[key], path + '.' + key)
    elif isinstance(a, list):
        if len(a) != len(b):
            return {'path': path, 'expectedLength': len(a), 'actualLength': len(b)}
        for index, (x, y) in enumerate(zip(a, b)):
            if x != y:
                return first_difference(x, y, path + f'[{index}]')
    return {'path': path, 'expected': str(a)[:1000], 'actual': str(b)[:1000]}


def compare(expected, actual, test):
    result = {'schema': 1, 'case': test['id'], 'equal': True, 'expectedFiles': {}, 'actualFiles': {},
              'expectedFileBytes': {}, 'actualFileBytes': {}, 'artifacts': []}
    digests = {}
    for spec in test['artifacts']:
        path, kind = spec['path'], spec['kind']
        a, b = (expected / path).read_bytes(), (actual / path).read_bytes()
        for side, raw in [('expected', a), ('actual', b)]:
            result[side + 'Files'][path] = sha(raw)
            result[side + 'FileBytes'][path] = len(raw)
        av, bv = canonical(a, kind), canonical(b, kind)
        ac = contract(a, kind, spec.get('contract', {}), expected / 'inputs')
        bc = contract(b, kind, spec.get('contract', {}), actual / 'inputs')
        ah, bh = sha(encoded(av)), sha(encoded(bv))
        equal = av == bv
        result['artifacts'].append({'path': path, 'kind': kind, 'equal': equal,
                                    'expectedCanonicalSha256': ah, 'actualCanonicalSha256': bh,
                                    'contract': bc, 'expectedContract': ac})
        result['equal'] &= equal
        digests[path] = bh
        if not equal:
            result['artifacts'][-1]['difference'] = first_difference(av, bv)
    result['biologicalDigest'] = sha(encoded(digests))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('expected', type=Path)
    parser.add_argument('actual', type=Path)
    parser.add_argument('--case', required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    plan = json_data((ROOT / 'validation/repeats/cases.json').read_bytes())
    test = next(case for case in plan['cases'] if case['id'] == args.case)
    try:
        result = compare(args.expected, args.actual, test)
    except Exception as error:
        result = {'schema': 1, 'case': args.case, 'equal': False, 'error': str(error)}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    print(json.dumps(result))
    return 0 if result['equal'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
