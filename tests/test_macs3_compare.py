"""Adversarial MACS3 output checks: coordinate/call identity and bounded scores."""
import importlib.util,json,tempfile,unittest
from pathlib import Path
S=importlib.util.spec_from_file_location('macs3_compare',Path(__file__).resolve().parents[1]/'validation/macs3/compare.py');c=importlib.util.module_from_spec(S);S.loader.exec_module(c)
PEAK='chr1\t100\t200\tpeak1\t100\t.\t3.14159\t4.0\t3.0\t50\n'
MODEL={'startprob':[.5,.25,.25],'transmat':[[.5,.25,.25],[.25,.5,.25],[.25,.25,.5]],'means':[[1.,2.,3.,4.],[2.,3.,4.,5.],[3.,4.,5.,6.]],'covars':[[[1.,0.,0.,0.],[0.,1.,0.,0.],[0.,0.,1.,0.],[0.,0.,0.,1.]]]*3,'covariance_type':'full','n_features':4,'i_open_region':0,'i_background_region':1,'i_nucleosomal_region':2,'hmm_binsize':10,'hmm_type':'gaussian'}
class MACS3CompareTests(unittest.TestCase):
 def pair(self,a,b,art=None,**extra):
  art=art or {'path':'result','kind':'narrowPeak','minRows':1}
  with tempfile.TemporaryDirectory() as t:
   p=Path(t);(p/'a').mkdir();(p/'b').mkdir();(p/'a'/art['path']).write_text(a);(p/'b'/art['path']).write_text(b)
   return c.compare(p/'a',p/'b',{'artifacts':[art],**extra})
 def test_identical_peak(self):self.assertTrue(self.pair(PEAK,PEAK)['equal'])
 def test_coordinates_exact(self):self.assertFalse(self.pair(PEAK,PEAK.replace('\t100\t200','\t101\t200'))['equal'])
 def test_summit_exact(self):self.assertFalse(self.pair(PEAK,PEAK.replace('\t50\n','\t51\n'))['equal'])
 def test_score_column_exact(self):self.assertFalse(self.pair(PEAK,PEAK.replace('peak1\t100','peak1\t101'))['equal'])
 def test_signal_small_rounding(self):
  r=self.pair(PEAK,PEAK.replace('3.14159','3.141591'));self.assertTrue(r['equal']);self.assertEqual(r['numericComparisons']['result']['toleratedDifferences'],1)
 def test_signal_beyond_tolerance(self):self.assertFalse(self.pair(PEAK,PEAK.replace('3.14159','3.15'))['equal'])
 def test_peak_missing(self):
  with self.assertRaises(ValueError):self.pair(PEAK,'')
 def test_nonfinite(self):
  for bad in ['nan','inf','-inf']:
   with self.assertRaises(ValueError):self.pair(PEAK,PEAK.replace('3.14159',bad))
 def test_order_exact(self):
  p2=PEAK.replace('100\t200','200\t300').replace('peak1','peak2');self.assertFalse(self.pair(PEAK+p2,p2+PEAK)['equal'])
 def test_extra_column(self):
  with self.assertRaises(ValueError):self.pair(PEAK,PEAK.rstrip()+'\textra\n')
 def test_invalid_summit(self):
  with self.assertRaises(ValueError):self.pair(PEAK,PEAK.replace('\t50\n','\t100\n'))
 def test_truth_coordinates(self):
  with self.assertRaises(ValueError):self.pair(PEAK,PEAK,{'path':'result','kind':'narrowPeak','coordinates':[['chr1',101,200]]})
 def test_hmm_identity(self):self.assertTrue(self.pair(json.dumps(MODEL),json.dumps(MODEL),{'path':'result','kind':'hmm-model','hmmType':'gaussian'})['equal'])
 def hmm(self,mutate):
  other=json.loads(json.dumps(MODEL));mutate(other);return self.pair(json.dumps(MODEL),json.dumps(other),{'path':'result','kind':'hmm-model','hmmType':'gaussian'})
 def test_hmm_full_covariance_compared(self):self.assertFalse(self.hmm(lambda m:m['covars'][2][3].__setitem__(3,1.1))['equal'])
 def test_hmm_transition_compared(self):self.assertFalse(self.hmm(lambda m:m['transmat'].__setitem__(1,[.2,.6,.2]))['equal'])
 def test_hmm_labels_exact(self):self.assertFalse(self.hmm(lambda m:m.update(i_open_region=1,i_background_region=0))['equal'])
 def test_hmm_missing_parameter(self):
  with self.assertRaises(ValueError):self.hmm(lambda m:m.pop('covars'))
 def test_hmm_extra_parameter(self):
  with self.assertRaises(ValueError):self.hmm(lambda m:m.update(ignored=1))
 def test_hmm_invalid_shape(self):
  with self.assertRaises(ValueError):self.hmm(lambda m:m['means'].pop())
 def test_hmm_non_normalized(self):
  with self.assertRaises(ValueError):self.hmm(lambda m:m['startprob'].__setitem__(0,.6))
 def test_hmm_nonfinite(self):
  with self.assertRaises(ValueError):self.hmm(lambda m:m['means'][0].__setitem__(0,float('nan')))
 def test_hmm_indefinite_covariance(self):
  def mutation(m):m['covars'][0][0][1]=m['covars'][0][1][0]=2.0
  with self.assertRaises(ValueError):self.hmm(mutation)
 def test_hmm_singular_psd_allowed(self):
  def mutation(m):m['covars'][0][0][1]=m['covars'][0][1][0]=1.0
  self.assertFalse(self.hmm(mutation)['equal'])
 def test_hmm_numeric_tolerance(self):self.assertTrue(self.hmm(lambda m:m['means'][0].__setitem__(0,1.000001))['equal'])
 def test_pileup_no_tolerance(self):self.assertFalse(self.pair('chr1\t0\t10\t1\n','chr1\t0\t10\t1.000001\n',{'path':'result','kind':'bedgraph','exactFloats':True})['equal'])
 def test_bed_block_count(self):
  with self.assertRaises(ValueError):c.parse(b'chr1\t0\t100\tpeak\t1\t.\t0\t100\t0\t2\t100\t0\n',{'kind':'bed12'})
 def test_extracted_diagnostic_required(self):
  with self.assertRaises(ValueError):c.parse(b'{}',{'kind':'json-exact','requiredKeys':['fragmentLength']})
 def test_posterior_out_of_range(self):
  with self.assertRaises(ValueError):c.parse(b'chr1\t0\t10\t1.2\n',{'kind':'bedgraph','probability':True})
 def test_state_label_invalid(self):
  with self.assertRaises(ValueError):c.parse(b'chr1\t0\t10\tunknown\n',{'kind':'states'})
 def test_coverage_truth_independent(self):
  with self.assertRaises(ValueError):self.pair('chr1\t0\t10\t2\n','chr1\t0\t10\t2\n',{'path':'result','kind':'bedgraph','integral':30})
 def test_path_escape(self):
  with tempfile.TemporaryDirectory() as t:
   with self.assertRaises(ValueError):c.safe_file(t,'../escape')
 def test_arithmetic_truth_independent(self):
  a='chrTest\t0\t1000\t1\n'
  with self.assertRaises(ValueError):self.pair(a,a,{'path':'result.bdg','kind':'bedgraph'},truth={'operation':'multiply','parameter':2})
 def test_vcf_genotype_and_allele_exact(self):
  v='##fileformat=VCFv4.1\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSAMPLE\nchr1\t101\t.\tA\tG\t30\t.\tDPT=8;AR=0.5\tGT:DP\t0/1:8\n';a={'path':'result','kind':'vcf'}
  self.assertTrue(self.pair(v,v,a)['equal']);self.assertFalse(self.pair(v,v.replace('0/1:8','1/1:8'),a)['equal']);self.assertFalse(self.pair(v,v.replace('\tG\t','\tC\t'),a)['equal'])
 def test_vcf_depth_exact(self):
  v='##fileformat=VCFv4.1\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSAMPLE\nchr1\t101\t.\tA\tG\t30\t.\tDPT=8;AR=0.5\tGT:DP\t0/1:8\n';self.assertFalse(self.pair(v,v.replace('DPT=8','DPT=9'),{'path':'result','kind':'vcf'})['equal'])
if __name__=='__main__':unittest.main()
