"""Adversarial comparator fixtures, not simulated biological tool results."""
import copy
import gzip
import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import unittest
import zlib


ROOT = Path(__file__).resolve().parents[1]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


compare = load('somatic_compare_tests', 'validation/somatic/compare.py')
bam = load('somatic_bam_tests', 'validation/somatic/bam.py')
numerics = load('somatic_numerics_tests', 'validation/somatic/numerics.py')

# Captured from the actual unchanged-native LoFreq primitive probe built from
# tools/lofreq/numerics.c on 2026-10-11 (x87 long double, 64 mantissa bits).
# This is a numerical test fixture, not fabricated variant-calling evidence.
NATIVE_NUMERICS = json.loads(r'''{"mantissaBits":64,"minExponent":-16381,"cases":[{"x":"-1","rawProbability":"3.678794411714423216101626e-01","probability":"3.678794411714423216101626e-01","positive":true,"rangeError":false,"log10":"-4.342944819032518276454794e-01","phred":4},{"x":"-100","rawProbability":"3.720075976020835962976044e-44","probability":"3.720075976020835962976044e-44","positive":true,"rangeError":false,"log10":"-4.342944819032518276660793e+01","phred":434},{"x":"-1000","rawProbability":"5.075958897549456765482395e-435","probability":"5.075958897549456765482395e-435","positive":true,"rangeError":false,"log10":"-4.342944819032518276591404e+02","phred":4342},{"x":"-11000","rawProbability":"5.763669429168183696403012e-4778","probability":"5.763669429168183696403012e-4778","positive":true,"rangeError":false,"log10":"-4.777239300935770104139522e+03","phred":47772},{"x":"-11356","rawProbability":"1.418611599723237307731374e-4932","probability":"3.362103143112093506262678e-4932","positive":true,"rangeError":true,"log10":"-4.931473388967339940069934e+03","phred":49314},{"x":"-11357","rawProbability":"5.218780425455103615074749e-4933","probability":"3.362103143112093506262678e-4932","positive":true,"rangeError":true,"log10":"-4.931473388967339940069934e+03","phred":49314},{"x":"-11400","rawProbability":"0.000000000000000000000000e+00","probability":"3.362103143112093506262678e-4932","positive":true,"rangeError":true,"log10":"-4.931473388967339940069934e+03","phred":49314},{"x":"-12000","rawProbability":"0.000000000000000000000000e+00","probability":"3.362103143112093506262678e-4932","positive":true,"rangeError":true,"log10":"-4.931473388967339940069934e+03","phred":49314}],"tailCases":[{"a":"-1","b":"-2","rawProbability":"5.032147244080550008344700e-01","probability":"5.032147244080550008344700e-01","positive":true,"rangeError":false,"log10":"-2.982466596223868830328046e-01","phred":2},{"a":"-1","b":"-1000","rawProbability":"3.678794411714423216101626e-01","probability":"1.189731495357231765021264e+4932","positive":true,"rangeError":true,"log10":"4.932075448958667902576991e+03","phred":-49320},{"a":"-1000","b":"-11000","rawProbability":"5.075958897549456765482395e-435","probability":"3.362103143112093506262678e-4932","positive":true,"rangeError":true,"log10":"-4.931473388967339940069934e+03","phred":49314}]}''')


VCF_HEADER = '''##fileformat=VCFv4.1
##fileDate=20261011
##source=lofreq call -f reference.fa --call-indels -o out.vcf tumor.bam
##reference=reference.fa
##contig=<ID=chr1,length=12>
##INFO=<ID=DP,Number=1,Type=Integer,Description="Depth">
##INFO=<ID=AF,Number=A,Type=Float,Description="Frequency">
##INFO=<ID=SB,Number=1,Type=Integer,Description="Strand statistic">
##INFO=<ID=SOMATIC,Number=0,Type=Flag,Description="Somatic">
##INFO=<ID=INDEL,Number=0,Type=Flag,Description="Indel">
##FILTER=<ID=q10,Description="Low quality">
##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">
##FORMAT=<ID=IGT,Number=1,Type=String,Description="Initial genotype">
##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Depth">
##FORMAT=<ID=SS,Number=1,Type=Integer,Description="Somatic status">
##FORMAT=<ID=SSC,Number=1,Type=Integer,Description="Somatic score">
#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tNORMAL\tTUMOR
'''
VCF_ROWS = [
    'chr1\t2\t.\tC\tT\t42.125\tPASS\tDP=20;AF=0.25;SB=0;SOMATIC\tGT:IGT:DP:SS:SSC\t0/0:0/0:10:0:0\t0/1:0/1:20:2:42',
    'chr1\t5\t.\tAC\tA\t31\tPASS\tDP=18;AF=0.5;SB=1;SOMATIC;INDEL\tGT:IGT:DP:SS:SSC\t0/0:0/0:12:0:0\t0/1:0/1:18:2:31',
]
VCF = VCF_HEADER + '\n'.join(VCF_ROWS) + '\n'
CONTEXT = {'tool': 'lofreq', 'references': {'chr1': 'ACGTACGTACGT'}, 'executableAliases': ['lofreq', 'worker.mjs']}
SPEC = {'path': 'out.vcf.gz', 'minSnvs': 1, 'minIndels': 1, 'minSomatic': 2, 'expectedSamples': ['NORMAL', 'TUMOR']}


def bgzf_block(data):
    compressor = zlib.compressobj(wbits=-15)
    body = compressor.compress(data) + compressor.flush()
    size = len(body) + 26
    header = b'\x1f\x8b\x08\x04\0\0\0\0\0\xff' + struct.pack('<H', 6) + b'BC' + struct.pack('<HH', 2, size - 1)
    return header + body + struct.pack('<II', zlib.crc32(data), len(data))


def bam_fixture(with_indel=False, mutation=None):
    """Tiny labelled structural BAM fixture, made independently of the decoder."""
    header = b'@HD\tVN:1.6\tSO:coordinate\n@SQ\tSN:chr1\tLN:100\n@RG\tID:sample\tSM:normal\n@PG\tID:prep\tPN:prep\tCL:original arguments\n'
    prefix = b'BAM\1' + struct.pack('<i', len(header)) + header + struct.pack('<ii', 1, 5) + b'chr1\0' + struct.pack('<i', 100)
    blocks = []
    offsets = []
    for index, position in enumerate((10, 20)):
        values = {'name': f'read{index + 1}', 'position': position, 'bin': 4681, 'mapq': 50, 'flag': (99, 147)[index],
                  'cigar': [(4, 0)], 'sequence': b'\x12\x48', 'quality': bytes([30, 31, 32, 33]),
                  'aux': b'R G'.replace(b' ', b'') + b'Zsample\0' + b'NMC\x01' + b'ZFf' + struct.pack('<f', 1.25)
                         + b'XBBC' + struct.pack('<i', 4) + b'\x01\x02\x03\x04'}
        if with_indel:
            values['aux'] += b'BIZ!!!!\0BDZ####\0'
        if mutation:
            mutation(values, index)
        name = values['name'].encode() + b'\0'
        core = struct.pack('<iiIIiiii', 0, values['position'], values['bin'] << 16 | values['mapq'] << 8 | len(name),
                           values['flag'] << 16 | len(values['cigar']), 4, 0, (20, 10)[index], (14, -14)[index])
        cigar = b''.join(struct.pack('<I', length << 4 | opcode) for length, opcode in values['cigar'])
        block = core + name + cigar + values['sequence'] + values['quality'] + values['aux']
        offsets.append(len(prefix) + sum(len(previous) for previous in blocks))
        blocks.append(struct.pack('<i', len(block)) + block)
    raw = prefix + b''.join(blocks)
    return bgzf_block(raw) + bgzf_block(b''), {'first': offsets[0], 'second': offsets[1], 'end': len(raw)}


def bai_fixture(offsets, start=None, end=None, linear=None, bin_id=4681):
    start = offsets['first'] if start is None else start
    end = offsets['end'] if end is None else end
    linear = start if linear is None else linear
    return b'BAI\1' + struct.pack('<iiIiQQiQQ', 1, 1, bin_id, 1, start, end, 1, linear, 0)


class SomaticVCFTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def parse(self, text=VCF, spec=None, name='out.vcf.gz', context=None):
        path = self.root / name
        path.write_bytes(text if isinstance(text, bytes) else gzip.compress(text.encode()) if name.endswith('.gz') else text.encode())
        return compare.parse_output(path, context or CONTEXT, SPEC if spec is None else spec, ('/work',))

    def test_browser_program_name_uses_only_declared_alias_and_keeps_arguments(self):
        plan = json.loads((ROOT / 'validation/somatic/cases.json').read_text())
        context = {**CONTEXT, 'executableAliases': plan['tools']['lofreq']['executableAliases']}
        native, _ = self.parse(context=context)
        browser = VCF.replace('##source=lofreq ', '##source=./this.program ')
        self.assertEqual(native, self.parse(browser, context=context)[0])
        self.assertNotEqual(native, self.parse(browser.replace('--call-indels', '--only-indels'), context=context)[0])
        self.assertNotEqual(native, self.parse(browser.replace('./this.program', './different-program'), context=context)[0])

    def test_positive_fixture_and_only_declared_metadata_normalisation(self):
        expected, counts = self.parse()
        changed = VCF.replace('20261011', '20261012').replace('##source=lofreq ', '##source=/runtime/worker.mjs ').replace('reference.fa', '/work/reference.fa').replace('\n', '\r\n')
        actual, other = self.parse(gzip.compress(changed.encode(), mtime=123))
        self.assertEqual(expected, actual)
        self.assertEqual(counts, other)
        self.assertEqual((counts['snvRecords'], counts['indelRecords'], counts['somaticRecords']), (1, 1, 2))

    def test_every_biological_value_and_variant_multiplicity_remains_exact(self):
        original = self.parse()[0]
        mutants = [VCF.replace('42.125', '42.1250000000000001'), VCF.replace('AF=0.25', 'AF=0.250001'),
                   VCF.replace('DP=20', 'DP=21'), VCF.replace('SB=0', 'SB=1'),
                   VCF.replace('0/1:0/1:20:2:42', '0|1:0/1:20:2:42'),
                   VCF.replace('0/1:0/1:20:2:42', '0/1:0/1:20:2:43'),
                   VCF.replace('\tPASS\t', '\tq10\t', 1), VCF + VCF_ROWS[0] + '\n']
        for mutated in mutants:
            with self.subTest(mutated=mutated[-160:]):
                self.assertNotEqual(original, self.parse(mutated)[0])

    def test_sample_order_and_sample_values_are_not_interchangeable(self):
        with self.assertRaises(ValueError):
            self.parse(VCF.replace('\tNORMAL\tTUMOR', '\tTUMOR\tNORMAL'))
        mutated = VCF.replace('0/0:0/0:10:0:0\t0/1:0/1:20:2:42', '0/1:0/1:20:2:42\t0/0:0/0:10:0:0')
        self.assertNotEqual(self.parse()[0], self.parse(mutated)[0])

    def test_record_and_unordered_info_order_do_not_change_identity(self):
        mutated = VCF_HEADER + '\n'.join(reversed(VCF_ROWS)) + '\n'
        mutated = mutated.replace('DP=20;AF=0.25;SB=0;SOMATIC', 'SOMATIC;SB=0;AF=0.25;DP=20')
        self.assertEqual(self.parse()[0], self.parse(mutated)[0])

    def test_header_presence_definition_and_source_arguments_are_preserved(self):
        original = self.parse()[0]
        for mutated in [VCF.replace('##fileDate=20261011\n', ''), VCF.replace(' --call-indels', ''),
                        VCF.replace('Description="Frequency"', 'Description="Changed meaning"'),
                        VCF.replace('##source=lofreq ', '##source=different-tool ')]:
            with self.subTest(mutated=mutated[:160]):
                self.assertNotEqual(original, self.parse(mutated)[0])
        for mutated in [VCF.replace('20261011', '20260230'), VCF.replace('##fileDate=20261011', '##fileDate=20261011\n##fileDate=20261011')]:
            with self.assertRaises(ValueError):
                self.parse(mutated)

    def test_reference_genotype_and_declared_header_invariants_reject_invalid_calls(self):
        for mutated in [VCF.replace('chr1\t2\t.\tC', 'chr1\t2\t.\tA'), VCF.replace('chr1\t5', 'chr1\t12'),
                        VCF.replace('0/1:0/1:20:2:42', '0/2:0/1:20:2:42'), VCF.replace('AF=0.25', 'AF=1.01'),
                        VCF.replace('DP=20;', 'MISSING=20;'), VCF.replace('length=12', 'length=13'),
                        VCF.replace('42.125', 'NaN')]:
            with self.subTest(mutated=mutated[-160:]), self.assertRaises(ValueError):
                self.parse(mutated)

    def test_gzip_crc_truncation_and_control_separators_are_rejected(self):
        valid = gzip.compress(VCF.encode())
        damaged = bytearray(valid); damaged[-8] ^= 1
        for mutated in [valid[:-4], bytes(damaged), b'plain VCF with gzip extension', gzip.compress(VCF.replace('\nchr1\t2', '\vchr1\t2').encode())]:
            with self.subTest(length=len(mutated)), self.assertRaises((ValueError, OSError, EOFError)):
                self.parse(mutated)

    def test_empty_success_and_missing_intermediate_outputs_cannot_pass(self):
        with self.assertRaises(ValueError):
            self.parse(VCF_HEADER)
        left, right = self.root / 'native', self.root / 'wasm'
        left.mkdir(); right.mkdir()
        for directory in (left, right):
            (directory / 'final.vcf').write_text(VCF)
            (directory / 'intermediate.vcf').write_text(VCF)
        context = {**CONTEXT, 'vcfs': [{**SPEC, 'path': 'final.vcf'}, {**SPEC, 'path': 'intermediate.vcf'}]}
        self.assertTrue(compare.compare_directories(left, right, context)['equal'])
        (right / 'intermediate.vcf').write_text(VCF.replace('42.125', '43'))
        self.assertFalse(compare.compare_directories(left, right, context)['equal'])
        (right / 'intermediate.vcf').unlink()
        with self.assertRaises(OSError):
            compare.compare_directories(left, right, context)


class SomaticBAMTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def parse(self, value, name='test.bam'):
        path = self.root / name
        path.write_bytes(value)
        return bam.parse_bam(path)

    def index(self, value, decoded):
        path = self.root / 'test.bam.bai'
        path.write_bytes(value)
        return bam.parse_bai(path, decoded)

    def test_indelqual_preserves_reads_and_adds_valid_quality_tags(self):
        original = self.parse(bam_fixture()[0], 'original.bam')
        result = self.parse(bam_fixture(with_indel=True)[0])
        self.assertEqual(bam.indelqual_invariants(result, original), {'records': 2, 'eligibleIndelQualityRecords': 2})

    def test_indelqual_cannot_mutate_alignment_sequence_quality_or_existing_tags(self):
        original = self.parse(bam_fixture()[0], 'original.bam')
        mutations = [lambda v, i: v.update(position=v['position'] + 1), lambda v, i: v.update(sequence=b'\x22\x48'),
                     lambda v, i: v.update(quality=bytes([31, 31, 32, 33])), lambda v, i: v.update(mapq=49),
                     lambda v, i: v.update(aux=v['aux'].replace(b'NMC\x01', b'NMC\x02'))]
        for mutation in mutations:
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                bam.indelqual_invariants(self.parse(bam_fixture(with_indel=True, mutation=mutation)[0]), original)

    def test_indelqual_rejects_missing_short_or_non_phred_quality_tags(self):
        for mutation in [lambda v, i: v.update(aux=v['aux'].replace(b'BIZ!!!!\0', b'')),
                         lambda v, i: v.update(aux=v['aux'].replace(b'BIZ!!!!\0', b'BIZ!!!\0')),
                         lambda v, i: v.update(aux=v['aux'].replace(b'BIZ!!!!\0', b'BIZ !!!\0'))]:
            with self.assertRaises(ValueError):
                bam.indelqual_invariants(self.parse(bam_fixture(with_indel=True, mutation=mutation)[0]))

    def test_aux_numeric_storage_type_array_values_and_record_order_are_retained(self):
        original = self.parse(bam_fixture()[0])
        for mutation in [lambda v, i: v.update(aux=v['aux'].replace(b'NMC\x01', b'NMI\x01\0\0\0')),
                         lambda v, i: v.update(aux=v['aux'].replace(b'\x01\x02\x03\x04', b'\x01\x02\x03\x05')),
                         lambda v, i: v.update(cigar=[(2, 0), (1, 1), (1, 0)])]:
            self.assertNotEqual(original['records'], self.parse(bam_fixture(mutation=mutation)[0])['records'])
        reversed_records = copy.deepcopy(original)
        reversed_records['records'].reverse()
        self.assertNotEqual(original, reversed_records)

    def test_header_command_line_is_not_dropped_by_generic_sam_normalisation(self):
        raw, _ = bam_fixture(with_indel=True)
        changed = gzip.decompress(raw).replace(b'original arguments', b'modified arguments')
        first = self.parse(raw)
        second = self.parse(bgzf_block(changed) + bgzf_block(b''))
        self.assertNotEqual(first['header'], second['header'])

    def test_corrupt_bam_cigar_crc_aux_duplicates_and_nonfinite_values_fail(self):
        valid, _ = bam_fixture()
        damaged = bytearray(valid); damaged[-36] ^= 1
        mutants = [valid[:-4], bytes(damaged),
                   bam_fixture(mutation=lambda v, i: v.update(cigar=[(3, 0)]))[0],
                   bam_fixture(mutation=lambda v, i: v.update(aux=v['aux'] + b'NMC\x01'))[0],
                   bam_fixture(mutation=lambda v, i: v.update(aux=v['aux'].replace(struct.pack('<f', 1.25), struct.pack('<f', float('inf')))))[0]]
        for mutated in mutants:
            with self.subTest(length=len(mutated)), self.assertRaises((ValueError, OSError, EOFError, zlib.error)):
                self.parse(mutated)

    def test_bai_valid_structure_and_invalid_reference_count_truncation_or_reversed_chunks(self):
        raw, offsets = bam_fixture()
        decoded = self.parse(raw)
        valid = bai_fixture(offsets)
        self.assertEqual(self.index(valid, decoded)['chunks'], 1)
        for mutated in [valid[:-1], valid[:4] + struct.pack('<i', 2) + valid[8:], bai_fixture(offsets, start=offsets['end'], end=offsets['first'])]:
            with self.assertRaises(ValueError):
                self.index(mutated, decoded)

    def test_bam_bin_corruption_is_rejected_or_retained_for_comparison(self):
        original = self.parse(bam_fixture()[0])
        try:
            changed = self.parse(bam_fixture(mutation=lambda v, i: v.update(bin=4682))[0])
        except ValueError:
            return
        self.assertNotEqual(original, changed, 'The alignment bin was silently discarded')

    def test_bai_virtual_offsets_outside_its_bam_cannot_pass_counts_only_validation(self):
        raw, offsets = bam_fixture()
        decoded = self.parse(raw)
        with self.assertRaises(ValueError):
            self.index(bai_fixture(offsets, start=1 << 60, end=(1 << 60) + 100, linear=1 << 60), decoded)


class SomaticNumericsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / 'numerics.json'

    def validate(self, value):
        self.path.write_text(json.dumps(value))
        return numerics.validate(self.path)

    def test_actual_native_probe_satisfies_independent_decimal_oracle(self):
        signature, evidence = self.validate(NATIVE_NUMERICS)
        self.assertEqual(len(signature), 11)
        self.assertEqual(evidence['cases'], 11)
        self.assertEqual(evidence['mantissaBits'], 64)
        self.assertEqual(signature[2]['phred'], 4342)

    def test_double_range_substitution_and_lost_subnormal_are_rejected(self):
        for index in (2, 3, 4, 5):
            value = copy.deepcopy(NATIVE_NUMERICS)
            value['cases'][index]['rawProbability'] = '0'
            with self.subTest(case=value['cases'][index]['x']), self.assertRaises(ValueError):
                self.validate(value)
        value = copy.deepcopy(NATIVE_NUMERICS)
        value['cases'][2]['probability'] = '2.2250738585072014e-308'
        value['cases'][2]['log10'] = '-307.6526555685888'
        value['cases'][2]['phred'] = 3076
        with self.assertRaises(ValueError):
            self.validate(value)

    def test_incorrect_range_flags_integer_phred_or_logarithm_fail(self):
        for key, replacement in [('rangeError', False), ('positive', 1), ('phred', 49315), ('phred', True), ('log10', '-4931.4')]:
            value = copy.deepcopy(NATIVE_NUMERICS)
            value['cases'][4][key] = replacement
            with self.subTest(field=key, value=replacement), self.assertRaises(ValueError):
                self.validate(value)
        value = copy.deepcopy(NATIVE_NUMERICS)
        value['tailCases'][1]['rangeError'] = False
        with self.assertRaises(ValueError):
            self.validate(value)

    def test_boundary_case_omissions_and_false_precision_claims_fail(self):
        for mutation in [lambda x: x['cases'].pop(), lambda x: x['tailCases'].pop(),
                         lambda x: x['cases'].reverse(), lambda x: x.update(mantissaBits=113)]:
            value = copy.deepcopy(NATIVE_NUMERICS)
            mutation(value)
            with self.assertRaises(ValueError):
                self.validate(value)


if __name__ == '__main__':
    unittest.main()
