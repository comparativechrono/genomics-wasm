"""Adversarial checks for scientific identity and non-vacuous Primer3 results."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
SPEC=importlib.util.spec_from_file_location('primer3_compare',Path(__file__).resolve().parents[1]/'validation/primer3/compare.py')
compare=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(compare)
OUTPUT='''SEQUENCE_ID=test
SEQUENCE_TEMPLATE=AACCGGTT
PRIMER_LEFT_NUM_RETURNED=1
PRIMER_RIGHT_NUM_RETURNED=1
PRIMER_INTERNAL_NUM_RETURNED=0
PRIMER_PAIR_NUM_RETURNED=1
PRIMER_PAIR_0_PENALTY=0.010000
PRIMER_LEFT_0_SEQUENCE=AACC
PRIMER_RIGHT_0_SEQUENCE=AACC
PRIMER_LEFT_0=0,4
PRIMER_RIGHT_0=7,4
PRIMER_LEFT_0_TM=60.000
PRIMER_RIGHT_0_TM=60.000
PRIMER_PAIR_0_PRODUCT_SIZE=8
=
'''
ART={'path':'result.txt','kind':'boulder','records':1,'errors':0,'minPairs':1,'minPrimers':2}
class Primer3CompareTests(unittest.TestCase):
 def pair(self,a,b,art=ART):
  with tempfile.TemporaryDirectory() as t:
   x=Path(t)/'x';y=Path(t)/'y';x.mkdir();y.mkdir();(x/'result.txt').write_bytes(a.encode());(y/'result.txt').write_bytes(b.encode())
   return compare.compare(x,y,{'artifacts':[art]})
 def test_identity(self):
  r=self.pair(OUTPUT,OUTPUT);self.assertTrue(r['equal']);self.assertEqual(r['numericTolerance'],0);self.assertEqual(r['invariants']['result.txt']['pairs'],1)
 def test_sequence_change(self):self.assertFalse(self.pair(OUTPUT,OUTPUT.replace('PRIMER_LEFT_0_SEQUENCE=AACC','PRIMER_LEFT_0_SEQUENCE=AACT'))['equal'])
 def test_position_change(self):self.assertFalse(self.pair(OUTPUT,OUTPUT.replace('PRIMER_LEFT_0=0,4','PRIMER_LEFT_0=1,4'))['equal'])
 def test_numeric_last_digit_change(self):self.assertFalse(self.pair(OUTPUT,OUTPUT.replace('0.010000','0.010001'))['equal'])
 def test_product_size_change(self):self.assertFalse(self.pair(OUTPUT,OUTPUT.replace('PRODUCT_SIZE=8','PRODUCT_SIZE=9'))['equal'])
 def test_record_order_change(self):
  a=OUTPUT+OUTPUT.replace('SEQUENCE_ID=test','SEQUENCE_ID=second');b=OUTPUT.replace('SEQUENCE_ID=test','SEQUENCE_ID=second')+OUTPUT
  self.assertFalse(self.pair(a,b,dict(ART,records=2))['equal'])
 def test_missing_scientific_field(self):self.assertFalse(self.pair(OUTPUT,OUTPUT.replace('PRIMER_LEFT_0_TM=60.000\n',''))['equal'])
 def test_duplicate_scientific_field(self):
  with self.assertRaises(ValueError):self.pair(OUTPUT,OUTPUT.replace('PRIMER_LEFT_0_TM=60.000','PRIMER_LEFT_0_TM=60.000\nPRIMER_LEFT_0_TM=60.000'))
 def test_missing_final_separator(self):
  with self.assertRaises(ValueError):self.pair(OUTPUT,OUTPUT[:-2])
 def test_rank_mutation(self):
  with self.assertRaises(ValueError):self.pair(OUTPUT,OUTPUT.replace('PRIMER_PAIR_0_PENALTY','PRIMER_PAIR_1_PENALTY'))
 def test_inconsistent_count(self):
  with self.assertRaises(ValueError):self.pair(OUTPUT,OUTPUT.replace('LEFT_NUM_RETURNED=1','LEFT_NUM_RETURNED=2'))
 def test_nonfinite(self):
  with self.assertRaises(ValueError):self.pair(OUTPUT,OUTPUT.replace('PRIMER_LEFT_0_TM=60.000','PRIMER_LEFT_0_TM=nan'))
 def test_errors_not_allowed_as_success(self):
  with self.assertRaises(ValueError):self.pair(OUTPUT,'SEQUENCE_ID=test\nPRIMER_ERROR=bad\n=\n')
 def test_input_changes_alone_not_biological_change(self):
  r=self.pair(OUTPUT,OUTPUT.replace('SEQUENCE_ID=test','SEQUENCE_ID=different'));self.assertFalse(r['equal']);self.assertEqual(r['expectedBiologicalDigest'],r['biologicalDigest'])
 def test_crlf_only_normalized(self):self.assertTrue(self.pair(OUTPUT,OUTPUT.replace('\n','\r\n'))['equal'])
 def test_empty_output_fails(self):
  with self.assertRaises(ValueError):self.pair(OUTPUT,'')
 def test_path_escape_fails(self):
  with tempfile.TemporaryDirectory() as t:
   with self.assertRaises(ValueError):compare.safe_file(t,'../outside')
 def test_cli_alignment_is_exact(self):self.assertFalse(self.pair('score=14.00 |2,0|','score=14.00 |3,0|',{'path':'result.txt','kind':'text'})['equal'])
if __name__=='__main__':unittest.main()
