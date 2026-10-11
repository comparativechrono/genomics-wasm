#!/usr/bin/env python3
"""Strict native/ported somatic VCF comparison with independent reference checks.

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
from validation.somatic.bam import parse_bam, parse_bai, indelqual_invariants
from validation.somatic.numerics import compare_numerics
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
    if any(ord(char) < 32 and char not in '\n\t' for char in text):
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
        tokens[0] = context['tool']
    return tuple(relative_provenance_path(token, prefixes) for token in tokens)


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
            provenance[('commandline', command_provenance(line.split('=', 1)[1], context, prefixes))] += 1
        elif line.startswith('##source=') and context['tool'] == 'lofreq':
            value = command_provenance(line.split('=', 1)[1], context, prefixes)
            line = '##source=' + shlex.join(value)
        elif line.startswith('##reference='):
            line = '##reference=' + relative_provenance_path(line.split('=', 1)[1], prefixes)
        lines.append(line)
    for key in ('fileDate', 'commandline'):
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
        allowed = set(output_spec.get('upstreamUndefinedFlags', []))
        if context['tool'] != 'lofreq' or allowed - {'SOMATIC'}: allowed = set()
        if undefined - allowed or any(info[key] is not None for key in undefined & allowed):
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
            for key in ('DP', 'DP4', 'BCOUNT', 'GQ', 'JGQ', 'VAQ', 'BQ', 'MQ', 'AMQ', 'SSC'):
                for value in fields.get(key, '.').split(','):
                    if value != '.':
                        nonnegative(value, key, integer=True)
            if fields.get('SS', '.') != '.':
                if fields['SS'] not in ('0', '1', '2', '3', '4'):
                    raise ValueError('invalid SomaticSniper somatic status')
                somatic |= fields['SS'] == '2'
        for key in ('DP', 'DP4', 'SB', 'UQ', 'HRUN'):
            for value in info.get(key, '.').split(','):
                if value != '.':
                    nonnegative(value, 'INFO/' + key, integer=True)
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


def compare_directories(expected, actual, context, expected_prefixes=(), actual_prefixes=('/work',)):
    left, right, native_checks, checks = {}, {}, {}, {}
    for spec in context.get('vcfs', []):
        path = spec['path']
        left[path], native_checks[path] = parse_output(Path(expected) / path, context, spec, expected_prefixes)
        right[path], checks[path] = parse_output(Path(actual) / path, context, spec, actual_prefixes)
        if spec.get('subsetOfInput'):
            for directory, output, invariant in [(expected,left[path],native_checks[path]),(actual,right[path],checks[path])]:
                source,_ = parse_output(Path(directory) / spec['subsetOfInput'], context, {}, ())
                before = Counter({record:count for record,count in source['records']}); after = Counter({record:count for record,count in output['records']})
                # Filter only changes FILTER values; all other fields are retained.
                key=lambda record:(record[0],record[2],record[3])
                def collapsed(records):
                    result=Counter()
                    for record,count in records.items(): result[key(record)]+=count
                    return result
                before,after=collapsed(before),collapsed(after)
                removed=sum(before.values())-sum(after.values())
                if after-before or removed<spec.get('minRemoved',0): raise ValueError('filter did not remove the required real input records')
                invariant['removedInputRecords']=removed
    for spec in context.get('bams', []):
        path = spec['path']
        original = parse_bam(Path(context['datasetDirectory']) / spec['original'])
        left[path], right[path] = parse_bam(Path(expected) / path), parse_bam(Path(actual) / path)
        native_checks[path] = indelqual_invariants(left[path], original)
        checks[path] = indelqual_invariants(right[path], original)
    for spec in context.get('indexes', []):
        path = spec['path']
        # These indexes are also consumed by the mandatory nonempty call-region
        # case. Exact bytes are required: indexed BAMs retain identical BGZF
        # layout across the three builds on these fixtures.
        left_bam = parse_bam(Path(expected) / spec['bam'])
        right_bam = parse_bam(Path(actual) / spec['bam'])
        native_checks[path] = parse_bai(Path(expected) / path, left_bam)
        checks[path] = parse_bai(Path(actual) / path, right_bam)
        left[path], right[path] = {'indexBytes': (Path(expected) / path).read_bytes().hex()}, {'indexBytes': (Path(actual) / path).read_bytes().hex()}
    if context.get('knownExclusion'):
        spec=context['knownExclusion']
        for directory, values, invariants in [(expected,left,native_checks),(actual,right,checks)]:
            source,_=parse_output(Path(directory)/spec['input'],context,{},())
            alleles=lambda value:{(record[0][0],record[0][1],record[0][3],record[0][4]) for record,count in value['records']}
            before,after,known=alleles(values[spec['before']]),alleles(values[spec['after']]),alleles(source)
            if after != before-known or len(before-after)<spec['minRemoved']: raise ValueError('known-variant exclusion did not match the supplied real call list')
            invariants[spec['after']]['removedKnownRecords']=len(before-after)
    if context.get('numerics'):
        return compare_numerics(Path(expected) / 'numerics.json', Path(actual) / 'numerics.json')
    if not left:
        raise ValueError('case has no declared biological outputs')
    return {'schema': 1, 'tool': context['tool'], 'equal': left == right,
            'expectedSemanticDigest': digest(left), 'semanticDigest': digest(right),
            'biologicalDigest': digest({path: value.get('records', value) for path, value in right.items()}),
            'invariants': checks, 'nativeInvariants': native_checks,
            'normalisation': ['gzip wrapper metadata', 'LF/CRLF line endings', 'record and unordered field order',
                              'valid fileDate value (presence retained)', 'declared executable aliases and working-directory prefixes in command/reference provenance'],
            'numericPolicy': 'Exact VCF numeric strings; no tolerances or allele normalisation.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('tool', choices=['somaticsniper', 'lofreq'])
    parser.add_argument('expected')
    parser.add_argument('actual')
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--dataset', required=True)
    parser.add_argument('--case', required=True)
    parser.add_argument('--report', required=True)
    parser.add_argument('--expected-prefix', action='append', default=[])
    parser.add_argument('--actual-prefix', action='append', default=['/work'])
    args = parser.parse_args()
    try:
        manifest_path = Path(args.manifest)
        manifest = json.loads(manifest_path.read_text())
        plan = json.loads(Path(__file__).with_name('cases.json').read_text())
        tool = plan['tools'][args.tool]
        spec = next(item for item in tool['cases'] if item['id'] == args.case)
        data = manifest['datasets'][args.dataset]
        context = {**spec, 'tool': args.tool, 'executableAliases': tool.get('executableAliases', [args.tool]),
                   'datasetDirectory': str(manifest_path.parent / Path(data['reference']).parent),
                   'references': reference_sequences(manifest_path.parent / data['reference'])}
        result = compare_directories(args.expected, args.actual, context, args.expected_prefix, args.actual_prefix)
    except Exception as error:
        result = {'schema': 1, 'tool': args.tool, 'equal': False, 'error': str(error)}
    Path(args.report).write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))
    return 0 if result['equal'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
