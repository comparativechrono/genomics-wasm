"""Adversarial parser tests; biological equivalence uses provenance-locked data."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('plink2_compare', Path(__file__).resolve().parents[1]/'validation/plink2/compare.py')
compare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compare)


class Plink2ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.a, self.b = Path(self.temp.name)/'a', Path(self.temp.name)/'b'
        self.a.mkdir(); self.b.mkdir()
        self.artifact = {'path':'result.tsv','kind':'table','floatColumns':['BETA','P'], 'minimumRecords':2}
        self.original = '#CHROM POS ID REF ALT OBS_CT BETA P ERRCODE\n1 10 v1 A G 603 0.123456 0.0000123456 .\n1 20 v2 C T 597 -0.765432 0.876543 .\n'
        for directory in [self.a,self.b]: (directory/'result.tsv').write_text(self.original)

    def check(self):
        return compare.compare(self.a,self.b,{'artifacts':[self.artifact]})

    def change(self, old, new):
        (self.b/'result.tsv').write_text(self.original.replace(old,new))

    def test_identical_binds_all_bytes(self):
        result=self.check()
        self.assertTrue(result['equal'])
        self.assertEqual(result['actualFiles'],result['expectedFiles'])
        self.assertEqual(result['numericComparisons']['result.tsv']['comparedValues'],4)
        self.assertEqual(result['invariants']['result.tsv']['numericValues'],4)
        self.assertEqual(result['invariants']['result.tsv']['exactCells'],14)

    def test_adjacent_printed_rounding_accepted(self):
        self.change('0.123456','0.123457')
        result=self.check();self.assertTrue(result['equal'])
        self.assertNotEqual(result['actualFiles'],result['expectedFiles'])
        self.assertEqual(result['numericComparisons']['result.tsv']['outsideTolerance'],0)

    def test_larger_numerical_shift_rejected(self):
        self.change('0.123456','0.123459')
        result=self.check();self.assertFalse(result['equal'])
        self.assertEqual(result['numericComparisons']['result.tsv']['outsideTolerance'],1)

    def test_scientific_exponent_precision(self):
        self.assertTrue(compare.numeric_equal('1.23456e-20','1.23457e-20')[0])
        self.assertFalse(compare.numeric_equal('1.23456e-20','1.23458e-20')[0])

    def test_integer_float_token_has_no_rounding_allowance(self):
        self.assertFalse(compare.numeric_equal('0','0.1')[0])
        self.assertFalse(compare.numeric_equal('1','1.00001')[0])
        self.assertTrue(compare.numeric_equal('1','1.00000')[0])

    def test_discrete_biology_mutations_rejected(self):
        for old,new in [('603','604'),('v1','v3'),('1 10','1 11'),('A G','A T'),(' .\n',' VIF_INFINITE\n')]:
            with self.subTest(old=old):
                self.change(old,new);self.assertFalse(self.check()['equal'])

    def test_row_order_exact(self):
        lines=self.original.splitlines();(self.b/'result.tsv').write_text('\n'.join([lines[0],lines[2],lines[1]])+'\n')
        self.assertFalse(self.check()['equal'])

    def test_missing_value_marker_exact(self):
        self.change('0.123456','NA');self.assertFalse(self.check()['equal'])
        self.change('0.123456','.');self.assertFalse(self.check()['equal'])

    def test_nonfinite_values_rejected(self):
        for value in ['nan','NaN','inf','-Infinity']:
            with self.subTest(value=value):
                self.change('0.123456',value)
                with self.assertRaisesRegex(ValueError,'nonfinite'):self.check()

    def test_ragged_or_truncated_table_rejected(self):
        self.change('603 0.123456','603')
        with self.assertRaisesRegex(ValueError,'ragged'):self.check()

    def test_unknown_float_column_rejected(self):
        self.artifact['floatColumns'].append('NOT_PRESENT')
        with self.assertRaisesRegex(ValueError,'column'):self.check()

    def test_required_nonmissing_results(self):
        self.artifact.update(nonmissingColumns=['P'],minimum={'nonmissing:P':2})
        self.change('0.0000123456','NA')
        with self.assertRaisesRegex(ValueError,'invariant'):self.check()

    def test_complete_row_counts(self):
        self.artifact['expected']={'records':2};self.change('\n1 20 v2 C T 597 -0.765432 0.876543 .','')
        with self.assertRaises(ValueError):self.check()

    def test_duplicate_plan_and_path_escape(self):
        with self.assertRaisesRegex(ValueError,'duplicate'):
            compare.compare(self.a,self.b,{'artifacts':[self.artifact,self.artifact]})
        self.artifact['path']='../result.tsv'
        with self.assertRaisesRegex(ValueError,'unsafe'):self.check()

    def test_exact_binary_byte_change_rejected(self):
        self.artifact={'path':'data.pgen','kind':'exact','magicHex':'6c1b'}
        (self.a/'data.pgen').write_bytes(b'\x6c\x1b\x10\x01')
        (self.b/'data.pgen').write_bytes(b'\x6c\x1b\x10\x02')
        result=self.check();self.assertFalse(result['equal']);self.assertEqual(result['numericTolerance'],0)

    def test_vcf_preserves_phasing_and_dosage(self):
        self.artifact={'path':'data.vcf','kind':'vcf','expected':{'records':1,'samples':2}}
        value='##fileformat=VCFv4.2\n##fileDate=20261013\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\ts1\ts2\n1\t10\tv1\tA\tG,T\t.\t.\t.\tGT:DS\t0|1:1.01\t2|0:0.20\n'
        (self.a/'data.vcf').write_text(value)
        for old,new,accepted in [('20261013','20261014',True),('0|1:1.01','1|0:1.01',False),('0|1:1.01','0/1:1.01',False),('1.01','1.02',False),('G,T','T,G',False),('s1\ts2','s2\ts1',False)]:
            with self.subTest(old=old):
                (self.b/'data.vcf').write_text(value.replace(old,new));self.assertEqual(self.check()['equal'],accepted)

    def test_zero_float_columns_policy_exact(self):
        self.artifact['floatColumns']=[]
        result=self.check();self.assertEqual(result['numericTolerance'],0);self.assertEqual(result['numericComparisons'],{})


class OddSampleTruthTests(unittest.TestCase):
    """Hand-enumerated three-sample BED, not output from either C++ program."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.a, self.b, self.fixtures = root/'a', root/'b', root/'fixtures'
        for path in (self.a, self.b, self.fixtures): path.mkdir()
        # Genotypes by sample: [0,1,1,2], [0,1,2,2], [2,1,1,0].
        (self.fixtures/'cohort.bed').write_bytes(b'\x6c\x1b\x01\x30\x2a\x2e\x0f')
        (self.fixtures/'cohort.bim').write_text(''.join(f'1 v{i} 0 {i} A G\n' for i in range(1,5)))
        (self.fixtures/'cohort.fam').write_text('F dad 0 0 1 -9\nF child 0 0 2 -9\nF other 0 0 1 -9\n')
        self.ids = '#FID IID\nF dad\nF child\nF other\n'
        (self.fixtures/'keep.txt').write_text(self.ids)
        self.config = {'bed':'cohort.bed','bim':'cohort.bim','fam':'cohort.fam','keep':'keep.txt','mode':'king-table'}
        self.table = '#FID1 IID1 FID2 IID2 NSNP HETHET IBS0 KINSHIP\nF child F dad 4 1 0 0.25\nF other F dad 4 2 2 -0.5\nF other F child 4 1 2 -1.75\n'
        self.spec = {'oddSampleTruth': self.config, 'artifacts': [
            {'path':'king.kin0','kind':'table','floatColumns':['KINSHIP'],'expected':{'records':3}}]}
        self.write_both('king.kin0', self.table)

    def write_both(self, filename, value):
        for path in (self.a, self.b): (path/filename).write_text(value)

    def check(self):
        return compare.compare(self.a, self.b, self.spec, self.fixtures)

    def matrix(self, mode):
        self.config['mode'] = mode
        filename = 'king.king' if mode == 'king-matrix' else 'distance.mibs'
        self.spec['artifacts'] = [
            {'path':filename,'kind':'table','columns':['S0','S1','S2'],
             'floatColumns':['S0','S1','S2'],'expected':{'records':3}},
            {'path':filename+'.id','kind':'text'}]
        value = '0.5 0.25 -0.5\n0.25 0.5 -1.75\n-0.5 -1.75 0.5\n' if mode == 'king-matrix' else '1 0.875 0.5\n0.875 1 0.375\n0.5 0.375 1\n'
        self.write_both(filename,value)
        self.write_both(filename+'.id',self.ids)
        return filename,value

    def test_hand_counted_table_and_input_hashes(self):
        result = self.check()
        self.assertTrue(result['equal'])
        expected = result['independentTruth']['expected']
        self.assertEqual(expected,result['independentTruth']['actual'])
        self.assertEqual(expected['samples'],3)
        self.assertEqual(expected['variants'],4)
        self.assertEqual(expected['firstSamplePairs'],2)
        self.assertEqual(expected['checkedValues'],3)
        self.assertEqual(set(expected['inputs']),{'cohort.bed','cohort.bim','cohort.fam','keep.txt'})

    def test_matching_first_sample_corruption_is_rejected(self):
        # Equality between targets must not make a shared wrong answer pass.
        for old,new in [('0.25','0.18'),('4 1 0','4 1 1'),('4 2 2','4 1 2')]:
            with self.subTest(mutation=new):
                self.write_both('king.kin0',self.table.replace(old,new))
                with self.assertRaisesRegex(ValueError,'independent truth'): self.check()

    def test_corrupt_native_reference_alone_is_rejected(self):
        (self.a/'king.kin0').write_text(self.table.replace('0.25','0.18'))
        with self.assertRaisesRegex(ValueError,'independent truth'): self.check()

    def test_duplicate_pair_cannot_replace_missing_pair(self):
        lines = self.table.splitlines()
        self.write_both('king.kin0','\n'.join([*lines[:3],lines[1]])+'\n')
        with self.assertRaisesRegex(ValueError,'duplicate'): self.check()

    def test_both_square_matrices_checked_against_genotypes(self):
        for mode in ('king-matrix','distance-ibs'):
            with self.subTest(mode=mode):
                filename,value = self.matrix(mode)
                result=self.check();self.assertTrue(result['equal'])
                self.assertEqual(result['independentTruth']['actual']['checkedValues'],9)
                self.write_both(filename,value.replace('0.25','0.18') if mode=='king-matrix' else value.replace('0.875','0.815'))
                with self.assertRaisesRegex(ValueError,'independent truth'): self.check()

    def test_matching_matrix_id_permutation_rejected(self):
        filename,_=self.matrix('king-matrix')
        self.write_both(filename+'.id',self.ids.replace('F dad\nF child','F child\nF dad'))
        with self.assertRaisesRegex(ValueError,'dimensions or order'): self.check()

    def test_coarse_rounding_cannot_expand_truth_tolerance(self):
        with self.assertRaisesRegex(ValueError,'independent truth'):
            compare.truth_number('0.3',compare.Decimal('0.25'),'coarse token')
        with self.assertRaisesRegex(ValueError,'independent truth'):
            compare.truth_number('0.4',compare.Decimal('0.5'),'exact endpoint')
        compare.truth_number('0.333333',compare.Decimal(1)/3,'six digits')

    def test_cutoff_replayed_from_independent_pair_graph(self):
        self.config.update(mode='king-cutoff',threshold=0.2)
        self.spec['artifacts']=[{'path':'cutoff.king.cutoff.in.id','kind':'text'},
                                {'path':'cutoff.king.cutoff.out.id','kind':'text'}]
        self.write_both('cutoff.king.cutoff.in.id','#FID IID\nF dad\nF other\n')
        self.write_both('cutoff.king.cutoff.out.id','#FID IID\nF child\n')
        self.assertTrue(self.check()['equal'])
        self.write_both('cutoff.king.cutoff.in.id',self.ids)
        self.write_both('cutoff.king.cutoff.out.id','#FID IID\n')
        with self.assertRaisesRegex(ValueError,'cutoff selection'): self.check()

    def test_subset_requires_every_first_sample_pair(self):
        self.config.update(mode='king-subset',subset='pairs.tsv')
        (self.fixtures/'pairs.tsv').write_text('#FID1 IID1 FID2 IID2\nF child F dad\nF other F dad\n')
        self.spec['artifacts'][0]['expected']['records']=2
        self.write_both('king.kin0','\n'.join(self.table.splitlines()[:3])+'\n')
        self.assertTrue(self.check()['equal'])
        (self.fixtures/'pairs.tsv').write_text('#FID1 IID1 FID2 IID2\nF child F dad\n')
        with self.assertRaisesRegex(ValueError,'first-sample pair coverage'): self.check()

    def test_malformed_fixture_cannot_define_an_oracle(self):
        (self.fixtures/'cohort.bed').write_bytes(b'\x6c\x1b\x01\x30')
        with self.assertRaisesRegex(ValueError,'BED magic or size'): self.check()

    def test_missing_fixture_context_fails_closed(self):
        with self.assertRaisesRegex(ValueError,'fixture directory required'):
            compare.compare(self.a,self.b,self.spec)


if __name__=='__main__':unittest.main()
