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


if __name__=='__main__':unittest.main()
