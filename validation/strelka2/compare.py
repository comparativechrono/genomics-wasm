#!/usr/bin/env python3
"""Strict native/ported Strelka2 output comparison with independent reference checks.

No numeric tolerances or allele normalisation are used. Raw QUAL, INFO and FORMAT
values, phased genotypes, sample order, header definitions and record multiplicity
remain exact. Only explicitly described run provenance and unordered field order
are normalised.
"""
from collections import Counter
from datetime import datetime
from decimal import Decimal, InvalidOperation
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import shlex
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from validation.compare import FormatError, parse_vcf  # noqa: E402


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def read_text(path):
    path = Path(path)
    raw = path.read_bytes()
    if path.suffix == '.gz':
        if not raw.startswith(b'\x1f\x8b'):
            raise ValueError(f'{path}: expected gzip content')
        raw = gzip.decompress(raw)  # Also validates CRC and complete gzip members.
    text = raw.decode('utf-8').replace('\r\n', '\n')
    if any(ord(char) < 32 and char not in '\n\t' or char in '\x85\u2028\u2029' for char in text):
        raise ValueError(f'{path}: invalid control character or line ending')
    return text


def reference_sequences(path):
    sequences, name = {}, None
    for line in read_text(path).split('\n'):
        if not line:
            continue
        if line.startswith('>'):
            name = line[1:].split()[0]
            if name in sequences:
                raise ValueError('duplicate reference contig')
            sequences[name] = []
        elif name is None or not re.fullmatch('[ACGTNacgtn]+', line):
            raise ValueError('invalid reference FASTA')
        else:
            sequences[name].append(line.upper())
    result = {name: ''.join(parts) for name, parts in sequences.items()}
    if not result or any(not sequence for sequence in result.values()):
        raise ValueError('empty reference FASTA')
    return result


def relative_provenance_path(value, prefixes):
    marker = 'file://' if value.startswith('file://') else ''
    path = value[len(marker):]
    for prefix in sorted(prefixes, key=len, reverse=True):
        prefix = prefix.rstrip('/') + '/'
        if path.startswith(prefix):
            return marker + path[len(prefix):]
    return value


def command_provenance(value, context, prefixes):
    tokens = shlex.split(value)
    if not tokens:
        raise ValueError('empty VCF command provenance')
    aliases = context.get('executableAliases', [context['tool']])
    if PurePosixPath(tokens[0]).name in aliases:
        tokens[0] = ('configureStrelkaSomaticWorkflow.py' if PurePosixPath(tokens[0]).name == 'configureStrelkaSomaticWorkflow.py' else context['tool'])
    def normalise(token):
        if token.startswith('--') and '=' in token:
            key, value = token.split('=', 1)
            return key + '=' + relative_provenance_path(value, prefixes)
        return relative_provenance_path(token, prefixes)
    return tuple(normalise(token) for token in tokens)


def prepare_header(text, context, prefixes):
    """Keep provenance presence and command arguments, changing only run details."""
    lines, provenance = [], Counter()
    for line in text.split('\n'):
        if line.startswith('##fileDate='):
            value = line.split('=', 1)[1]
            if not re.fullmatch('[0-9]{8}', value):
                raise ValueError('VCF fileDate must be an eight-digit calendar date')
            datetime.strptime(value, '%Y%m%d')
            provenance[('fileDate', '<run-date>')] += 1
        elif line.startswith('##commandline='):
            provenance[('commandline', line.split('=', 1)[1])] += 1
        elif line.startswith('##startTime='):
            value = line.split('=', 1)[1]
            if not re.fullmatch(r'[A-Z][a-z]{2} [A-Z][a-z]{2} [ 0-9][0-9] [0-9]{2}:[0-9]{2}:[0-9]{2} [0-9]{4}', value):
                raise ValueError('invalid VCF startTime')
            datetime.strptime(value, '%a %b %d %H:%M:%S %Y')
            provenance[('startTime', '<run-time>')] += 1
            line = '##startTime=recorded-run-time'
        elif line.startswith('##cmdline='):
            provenance[('cmdline', command_provenance(line.split('=', 1)[1], context, prefixes))] += 1
            line = '##cmdline=recorded-separately'
        elif line.startswith('##reference='):
            line = '##reference=' + relative_provenance_path(line.split('=', 1)[1], prefixes)
        lines.append(line)
    for key in ('fileDate', 'cmdline', 'startTime'):
        if sum(count for (name, _), count in provenance.items() if name == key) > 1:
            raise ValueError(f'duplicate {key} VCF provenance')
    # The shared parser already ignores fileDate/commandline. Their validated,
    # narrowly normalised values are restored as comparison metadata below.
    return '\n'.join(lines), provenance


def nonnegative(value, label, integer=False):
    try:
        number = Decimal(value)
    except (InvalidOperation, TypeError) as error:
        raise ValueError(f'{label}: invalid number') from error
    if not number.is_finite() or number < 0 or (integer and number != number.to_integral_value()):
        raise ValueError(f'{label}: expected finite non-negative {"integer" if integer else "number"}')
    return number


def genotype(value, alternate_count):
    if not re.fullmatch(r'(?:\.|[0-9]+)(?:[/|](?:\.|[0-9]+))*', value):
        raise ValueError('invalid genotype')
    if any(allele != '.' and int(allele) > alternate_count for allele in re.split(r'[/|]', value)):
        raise ValueError('genotype allele exceeds ALT count')


def parse_output(path, context, output_spec, prefixes=()):
    text, provenance = prepare_header(read_text(path), context, prefixes)
    parsed = parse_vcf(io.StringIO(text))
    parsed.metadata.update(provenance)
    contigs, header = parsed.ordered_header
    references = context['references']
    definitions = {'INFO': set(), 'FORMAT': set(), 'FILTER': set()}
    for (kind, item), count in parsed.metadata.items():
        if kind in definitions:
            fields = dict(item)
            definitions[kind].add(fields['ID'])
    for contig in contigs:
        fields = dict(contig)
        if fields['ID'] not in references:
            raise ValueError('VCF contig is missing from the bundled reference')
        if 'length' in fields and int(fields['length']) != len(references[fields['ID']]):
            raise ValueError('VCF contig length disagrees with the bundled reference')
    samples = list(header[9:])
    if 'expectedSamples' in output_spec and samples != output_spec['expectedSamples']:
        raise ValueError('VCF samples differ from the planned normal/tumour order')
    counts = {'records': sum(parsed.records.values()), 'variantRecords': 0, 'snvRecords': 0,
              'indelRecords': 0, 'somaticRecords': 0, 'referenceCheckedRecords': 0, 'samples': samples}
    for (fixed, filters, info_items, sample_values), multiplicity in parsed.records.items():
        chrom, position_text, identifier, reference, alternate, quality = fixed
        position = int(position_text)
        if chrom not in references or position + len(reference) - 1 > len(references[chrom]):
            raise ValueError('VCF coordinate is outside the bundled reference')
        if references[chrom][position - 1:position - 1 + len(reference)] != reference.upper():
            raise ValueError('VCF REF does not match the bundled reference')
        counts['referenceCheckedRecords'] += multiplicity
        if quality != '.':
            number = nonnegative(quality, 'QUAL')
            if number < Decimal(output_spec.get('minQual', '0')): raise ValueError('VCF QUAL is below planned filter threshold')
        elif 'minQual' in output_spec: raise ValueError('filtered VCF has missing QUAL')
        for value in filters:
            if value not in {'.', 'PASS'} and value not in definitions['FILTER']:
                raise ValueError('VCF FILTER has no header definition: ' + value)
        info = dict(info_items)
        undefined = set(info) - definitions['INFO']
        if undefined:
            raise ValueError('VCF INFO has no header definition: ' + ','.join(sorted(undefined)))
        alts = [] if alternate == '.' else alternate.split(',')
        if any(not re.fullmatch('[ACGTNacgtn]+', allele) for allele in alts):
            raise ValueError('Somatic small-variant cases require explicit sequence ALT alleles')
        if alts:
            counts['variantRecords'] += multiplicity
            if len(reference) == 1 and all(len(allele) == 1 for allele in alts):
                counts['snvRecords'] += multiplicity
            elif any(len(allele) != len(reference) for allele in alts):
                counts['indelRecords'] += multiplicity
        somatic = 'SOMATIC' in info
        for fields_tuple in sample_values:
            fields = dict(fields_tuple)
            if set(fields) - definitions['FORMAT']:
                raise ValueError('VCF FORMAT has no header definition')
            for key in ('GT', 'IGT'):
                if key in fields:
                    genotype(fields[key], len(alts))
            for key in ('DP', 'DP2', 'FDP', 'SDP', 'SUBDP', 'AU', 'CU', 'GU', 'TU', 'TAR', 'TIR', 'TOR'):
                for value in fields.get(key, '.').split(','):
                    if value != '.': nonnegative(value, 'FORMAT/' + key, integer=True)
            for key, value in fields.items():
                if re.fullmatch(r'(?:DP|FDP|SUBDP|BCN)[0-9]+', key) and value != '.':
                    nonnegative(value, 'FORMAT/' + key)
        for key in ('DP', 'MQ0', 'QSS', 'QSS_NT', 'QSI', 'QSI_NT', 'RC', 'IC', 'IHP'):
            for value in info.get(key, '.').split(','):
                if value != '.': nonnegative(value, 'INFO/' + key, integer=True)
        for key in ('TQSS', 'TQSS_NT', 'TQSI', 'TQSI_NT'):
            if key in info and info[key] not in ('1', '2'): raise ValueError('Invalid tier selection: ' + key)
        for key in ('MQ', 'SomaticEVS'):
            if key in info and info[key] != '.': nonnegative(info[key], 'INFO/' + key)
        for value in info.get('AF', '.').split(','):
            if value != '.' and nonnegative(value, 'INFO/AF') > 1:
                raise ValueError('allele frequency exceeds one')
        if somatic:
            counts['somaticRecords'] += multiplicity
    for name, field in [('minRecords', 'records'), ('minVariants', 'variantRecords'),
                        ('minSnvs', 'snvRecords'), ('minIndels', 'indelRecords'), ('minSomatic', 'somaticRecords')]:
        if counts[field] < output_spec.get(name, 0):
            raise ValueError(f'vacuous {Path(path).name}: expected {name}>={output_spec[name]}, found {counts[field]}')
    representation = {'orderedHeader': parsed.ordered_header,
                      'metadata': sorted(parsed.metadata.items(), key=repr),
                      'records': sorted(parsed.records.items(), key=repr)}
    return representation, counts


def parse_auxiliary(path, spec, context):
    import xml.etree.ElementTree as ET
    text = read_text(path)
    kind = spec['kind']
    if kind == 'stats-xml':
        node = ET.fromstring(text)
        if node.tag != 'boost_serialization': raise ValueError('unexpected run statistics XML root')
        data = node.find('runStatsData')
        if data is None or [child.tag for child in data] != ['lifeTime', 'candidateIndels', 'nonCandidateIndels']:
            raise ValueError('incomplete run statistics XML')
        life = data.find('lifeTime')
        if [child.tag for child in life] != ['wall', 'user', 'system']: raise ValueError('incomplete timing fields')
        for child in life:
            nonnegative(child.text, 'runStats/' + child.tag)
            child.text = '<run-timing>'
        counts = {key: int(nonnegative(data.find(key).text, key, True)) for key in ('candidateIndels', 'nonCandidateIndels')}
        if counts['candidateIndels'] < spec.get('minCandidateIndels', 0): raise ValueError('vacuous candidate indel statistics')
        def representation(item):
            if (item.tail or '').strip(): raise ValueError('unexpected XML tail content')
            return (item.tag, sorted(item.attrib.items()), (item.text or '').strip(), tuple(representation(child) for child in item))
        return representation(node), counts
    if kind == 'stats-tsv':
        lines = text.split('\n')
        if not lines or lines[0] != 'StrelkaRunStatsReport': raise ValueError('unexpected run statistics TSV')
        timings = [line for line in lines if line.startswith('TotalHours\t')]
        if len(timings) != 1 or not re.fullmatch(r'TotalHours\t[0-9.]+h wall, [0-9.]+h user \+ [0-9.]+h system = [0-9.]+h CPU \((?:[0-9.]+|-?nan|-?inf)%\)', timings[0]):
            raise ValueError('invalid timing report')
        hours = re.findall(r'([0-9.]+)h', timings[0])
        for value in hours: nonnegative(value, 'TotalHours')
        percent = timings[0].rsplit('(', 1)[1][:-2]
        if percent not in ('nan', '-nan', 'inf', '-inf'): nonnegative(percent, 'CPU percentage')
        rows = [line for line in lines if line and not line.startswith('TotalHours\t')]
        if len(rows) != 3 or any(len(line.split('\t')) != 2 for line in rows[1:]) or not rows[1].startswith('CallRegionCandidateIndels\t') or not rows[2].startswith('CallRegionNonCandidateIndels\t'):
            raise ValueError('incomplete statistics counters')
        counts = {line.split('\t')[0]: int(nonnegative(line.split('\t')[1], 'runStats count', True)) for line in rows[1:]}
        return rows, counts
    if kind == 'depth':
        rows = [line.split('\t') for line in text.split('\n') if line]
        if not rows or any(len(row) != 2 for row in rows): raise ValueError('invalid chromosome depth table')
        if len({row[0] for row in rows}) != len(rows): raise ValueError('duplicate chromosome depth')
        for chrom, value in rows:
            if chrom not in context['references']: raise ValueError('unknown depth chromosome')
            nonnegative(value, 'chromosome depth')
        positive = sum(Decimal(row[1]) > 0 for row in rows)
        if positive < spec.get('minPositiveContigs', 0): raise ValueError('vacuous chromosome depth')
        return sorted(rows), {'contigs': len(rows), 'positiveContigs': positive}
    if kind == 'bed':
        rows = [line.split('\t') for line in text.split('\n') if line and not line.startswith('#')]
        last = {}
        for row in rows:
            if len(row) != 3 or row[0] not in context['references']: raise ValueError('invalid callable BED')
            start, end = int(row[1]), int(row[2])
            if start < last.get(row[0], 0) or not start < end <= len(context['references'][row[0]]): raise ValueError('invalid or overlapping callable BED interval')
            last[row[0]] = end
        if len(rows) < spec.get('minRecords', 0): raise ValueError('empty callable BED')
        return rows, {'records': len(rows), 'bases': sum(int(row[2])-int(row[1]) for row in rows)}
    raise ValueError('undeclared auxiliary output kind')


def compare_directories(expected, actual, context, expected_prefixes=(), actual_prefixes=('/work',)):
    left, right, native_checks, checks = {}, {}, {}, {}
    for spec in context.get('vcfs', []):
        path = spec['path']
        left[path], native_checks[path] = parse_output(Path(expected) / path, context, spec, expected_prefixes)
        right[path], checks[path] = parse_output(Path(actual) / path, context, spec, actual_prefixes)
    for spec in context.get('auxiliary', []):
        path = spec['path']
        left[path], native_checks[path] = parse_auxiliary(Path(expected) / path, spec, context)
        right[path], checks[path] = parse_auxiliary(Path(actual) / path, spec, context)
        for count in (native_checks[path], checks[path]):
            if any(count.get(key) != value for key,value in spec.get('expectedCounts', {}).items()):
                raise ValueError('statistics arithmetic did not match the independent expected integer sum')
    if not left: raise ValueError('case has no declared biological outputs')
    # An option-effect digest covers records only, independent of command metadata,
    # file naming, depth/timing outputs, and the number of retained intermediates.
    biological = {Path(path).name: value['records'] for path, value in right.items()
                  if isinstance(value, dict) and 'records' in value and '/results/variants/' in '/' + path}
    if not biological: biological = {path: value.get('records', value) if isinstance(value, dict) else value for path,value in right.items()}
    return {'schema': 1, 'tool': 'strelka2', 'equal': left == right,
            'expectedSemanticDigest': digest(left), 'semanticDigest': digest(right),
            'biologicalDigest': digest(biological), 'invariants': checks, 'nativeInvariants': native_checks,
            'normalisation': ['gzip wrapper metadata', 'LF/CRLF line endings', 'record and unordered field order',
                              'validated fileDate/startTime values (presence retained)',
                              'declared executable aliases and directory prefixes in command/reference provenance',
                              'runStats lifeTime wall/user/system and TotalHours values only',
                              'chromosome-keyed depth row order (all numeric strings retained)'],
            'numericPolicy': 'Exact VCF numeric strings; no tolerance or allele normalisation.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('expected'); parser.add_argument('actual')
    parser.add_argument('--manifest', required=True); parser.add_argument('--dataset', required=True)
    parser.add_argument('--case', required=True); parser.add_argument('--report', required=True)
    parser.add_argument('--expected-prefix', action='append', default=[])
    parser.add_argument('--actual-prefix', action='append', default=['/work'])
    args = parser.parse_args()
    try:
        manifest_path = Path(args.manifest)
        manifest = json.loads(manifest_path.read_text())
        plan = json.loads(Path(__file__).with_name('cases.json').read_text())
        spec = next(item for item in plan['cases'] if item['id'] == args.case)
        context = {**spec, 'tool': 'strelka2', 'executableAliases': plan['executableAliases'],
                   'references': reference_sequences(manifest_path.parent / manifest['datasets'][args.dataset]['reference'])}
        result = compare_directories(args.expected, args.actual, context, args.expected_prefix, args.actual_prefix)
    except Exception as error:
        result = {'schema': 1, 'tool': 'strelka2', 'equal': False, 'error': str(error)}
    Path(args.report).write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))
    return 0 if result['equal'] else 1

if __name__ == '__main__': raise SystemExit(main())
