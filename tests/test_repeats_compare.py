"""Mutations that must not pass repeat-caller / repeat-plot equivalence."""
import gzip
import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('repeat_compare', ROOT / 'validation/repeats/compare.py')
C = importlib.util.module_from_spec(spec)
spec.loader.exec_module(C)

VCF = (b'##fileformat=VCFv4.1\n'
       b'#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tsample\n'
       b'chr1\t4000\t.\tA\t<STR12>,<STR18>\t.\tPASS\tVARID=R;RU=CAG\tGT:REPCN:REPCI:ADSP\t1/2:12/18:12-12/18-18:20/21\n')


def bam(records):
    """A hand-specified BAM with two 4-base graph-aligned reads."""
    header = b'@HD\tVN:1.4\tSO:unknown\n'
    body = b'BAM\1' + struct.pack('<i', len(header)) + header
    body += struct.pack('<ii', 1, 5) + b'chr1\0' + struct.pack('<i', 8000)
    for name, position, sequence, graph in records:
        qname = name.encode() + b'\0'
        # ref,pos,bin/mapQ/nameLen,flag/nCigar,lSeq,mateRef,matePos,tLen
        record = struct.pack('<iiIIiiii', 0, position, (4681 << 16) | (60 << 8) | len(qname), 1, 4, -1, -1, 0)
        record += qname + struct.pack('<I', 4 << 4) + sequence + bytes([40] * 4)
        record += b'XGZ' + graph.encode() + b'\0'
        body += struct.pack('<i', len(record)) + record
    return gzip.compress(body, mtime=0)


class RepeatComparison(unittest.TestCase):
    def test_vcf_preserves_repeat_and_snv_science(self):
        original = C.canonical(VCF, 'vcf')
        for old, new in [(b'1/2:12/18', b'2/1:18/12'), (b'12-12/18-18', b'12-12/17-19'),
                         (b'20/21', b'20/20'), (b'RU=CAG', b'RU=CAA'), (b'\t4000\t', b'\t4001\t')]:
            with self.subTest(old=old):
                self.assertNotEqual(original, C.canonical(VCF.replace(old, new), 'vcf'))
        reordered = VCF.replace(b'VARID=R;RU=CAG', b'RU=CAG;VARID=R')
        self.assertEqual(original, C.canonical(reordered, 'vcf'))

    def test_json_values_are_exact_and_duplicate_fields_rejected(self):
        a = b'{"Coverage":42.125,"Genotype":"12/18","CI":"12-12/18-18"}'
        b = b'{"CI":"12-12/18-18","Genotype":"12/18","Coverage":42.125}'
        self.assertEqual(C.canonical(a, 'json'), C.canonical(b, 'json'))
        for old, new in [(b'42.125', b'42.12500001'), (b'12/18', b'12|18'), (b'18-18', b'17-19')]:
            self.assertNotEqual(C.canonical(a, 'json'), C.canonical(a.replace(old, new), 'json'))
        for raw in [b'{"Genotype":"12/18","Genotype":"12/12"}', b'{"Coverage":NaN}']:
            with self.assertRaises((ValueError, AssertionError)):
                C.canonical(raw, 'json')

    def test_bam_multiset_retains_multiplicity_and_graph_alignment(self):
        records = [('read1', 3990, b'\x12\x48', 'R,0,4M'), ('read2', 4000, b'\x24\x81', 'R,3,4M')]
        reference = C.canonical(bam(records), 'bam')
        self.assertEqual(reference, C.canonical(bam(records[::-1]), 'bam'))
        self.assertNotEqual(reference, C.canonical(bam(records + records[:1]), 'bam'))
        for altered in [('read1', 3991, b'\x12\x48', 'R,0,4M'), ('read1', 3990, b'\x12\x88', 'R,0,4M'),
                        ('read1', 3990, b'\x12\x48', 'R,1,4M'), ('other', 3990, b'\x12\x48', 'R,0,4M')]:
            self.assertNotEqual(reference, C.canonical(bam([altered, records[1]]), 'bam'))
        C.contract(bam(records), 'bam', {'coordinateSorted': True, 'graphLoci': ['R'], 'recordCount': 2}, ROOT)
        with self.assertRaises(AssertionError):
            C.contract(bam(records[::-1]), 'bam', {'coordinateSorted': True}, ROOT)

    def test_bam_compression_integrity_and_bounds_are_checked(self):
        raw = bam([('r', 1, b'\x12\x48', 'R,0,4M')])
        broken = bytearray(raw)
        broken[-8] ^= 1
        with self.assertRaises((OSError, AssertionError)):
            C.canonical(bytes(broken), 'bam')
        decoded = gzip.decompress(raw)
        with self.assertRaises(AssertionError):
            C.canonical(gzip.compress(decoded[:-1], mtime=0), 'bam')

    def test_svg_only_attribute_order_is_ignored(self):
        a = b'<svg xmlns="http://www.w3.org/2000/svg" width="100" height="20"><rect x="2" width="30" fill="red"/><text>12/18</text></svg>'
        b = a.replace(b'x="2" width="30" fill="red"', b'fill="red" width="30" x="2"')
        self.assertEqual(C.canonical(a, 'svg'), C.canonical(b, 'svg'))
        for old, new in [(b'width="30"', b'width="31"'), (b'fill="red"', b'fill="blue"'), (b'12/18', b'12/12')]:
            self.assertNotEqual(C.canonical(a, 'svg'), C.canonical(a.replace(old, new), 'svg'))
        with self.assertRaises(AssertionError):
            C.canonical(b'<!DOCTYPE svg><svg/>', 'svg')

    def test_metrics_depth_and_phasing_score_are_exact(self):
        rows = b'VariantId\tGenotype\tAlleleDepth\nR\t12/18\t9.20/13.05\n'
        C.contract(rows, 'tsv', {'metricsGenotypes': {'R': '12/18'}}, ROOT)
        self.assertNotEqual(C.canonical(rows, 'tsv'), C.canonical(rows.replace(b'13.05', b'13.06'), 'tsv'))
        phase = b'LocusId\tDiplotype\tScore\nR\t(LF)(CAG){12}/(LF)(CAG){18}\t310\n'
        self.assertNotEqual(C.canonical(phase, 'tsv'), C.canonical(phase.replace(b'310', b'309'), 'tsv'))

    def test_unlinked_allele_truth_does_not_relax_phase_comparison(self):
        a = b'VariantId\tGenotype\tAlleleDepth\nR\t12/18\t9.20/13.05\n'
        b = a.replace(b'12/18', b'18/12')
        for raw in [a, b]:
            C.contract(raw, 'tsv', {'metricsAlleles': {'R': [12, 18]}}, ROOT)
        self.assertNotEqual(C.canonical(a, 'tsv'), C.canonical(b, 'tsv'))
        with self.assertRaises(AssertionError):
            C.contract(a.replace(b'12/18', b'12/19'), 'tsv', {'metricsAlleles': {'R': [12, 18]}}, ROOT)

    def test_empty_input_must_not_invent_genotypes(self):
        value = {'LocusResults': {'R': {'Variants': {'R': {'VariantType': 'Repeat'}}}}}
        C.contract(json.dumps(value).encode(), 'json', {'jsonNoCalls': ['R']}, ROOT)
        for key in ['Genotype', 'GenotypeConfidenceInterval']:
            changed = json.loads(json.dumps(value))
            changed['LocusResults']['R']['Variants']['R'][key] = '12/12'
            with self.assertRaises(AssertionError):
                C.contract(json.dumps(changed).encode(), 'json', {'jsonNoCalls': ['R']}, ROOT)

    def test_truth_rejects_wrong_repeat_count_ploidy_and_interval(self):
        truth = {'loci': {'R': {'alleles': [12, 75], 'ploidy': 2, 'assertion': 'confidenceIntervalContains'}}}
        data = {'LocusResults': {'R': {'AlleleCount': 2, 'Variants': {'R': {'VariantType': 'Repeat',
                'Genotype': '12/74', 'GenotypeConfidenceInterval': '12-12/70-80'}}}}}
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)
            (path / 'truth.json').write_text(json.dumps(truth))
            C.contract(json.dumps(data).encode(), 'json', {'repeatTruth': {'source': 'truth.json'}}, path)
            for key, value in [('GenotypeConfidenceInterval', '12-12/60-70'), ('Genotype', '12/13')]:
                mutated = json.loads(json.dumps(data))
                mutated['LocusResults']['R']['Variants']['R'][key] = value
                with self.assertRaises(AssertionError):
                    C.contract(json.dumps(mutated).encode(), 'json', {'repeatTruth': {'source': 'truth.json'}}, path)
            data['LocusResults']['R']['AlleleCount'] = 1
            with self.assertRaises(AssertionError):
                C.contract(json.dumps(data).encode(), 'json', {'repeatTruth': {'source': 'truth.json'}}, path)

    def test_native_self_comparison_still_enforces_independent_truth(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)
            (path / 'inputs').mkdir()
            (path / 'out.vcf').write_bytes(VCF)
            test = {'id': 'truth', 'artifacts': [{'path': 'out.vcf', 'kind': 'vcf',
                    'contract': {'vcfGenotypes': {'R': {'REPCN': '12/12'}}}}]}
            with self.assertRaises(AssertionError):
                C.compare(path, path, test)


if __name__ == '__main__':
    unittest.main()
