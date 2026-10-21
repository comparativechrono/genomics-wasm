"""Adversarial checks for the HTS semantic comparison and independent truths."""
import importlib.util,json,struct,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def module(path,name):
    spec=importlib.util.spec_from_file_location(name,ROOT/path);value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value
C=module('validation/hts/compare.py','hts_compare')
P=module('validation/hts/portability_fixtures.py','hts_portability')
class HTSCompare(unittest.TestCase):
    def setUp(self):self.vcf=(ROOT/'datasets/hts/common/trio-csq.vcf').read_bytes()
    def test_exact_genotypes_annotations_and_order(self):
        reference=C.canonical(self.vcf,'vcf')
        for old,new in [(b'0/1:20:10,10',b'1/0:20:10,10'),(b'ENST000001',b'ENST999999'),(b'0.001',b'0.002'),(b'1/2:20',b'1/1:20'),(b'./1:10',b'0/1:10')]:
            with self.subTest(old=old):
                altered=self.vcf.replace(old,new,1);self.assertNotEqual(altered,self.vcf);self.assertNotEqual(reference,C.canonical(altered,'vcf'))
    def test_provenance_exception_is_narrow(self):
        raw=self.vcf.replace(b'#CHROM',b'##bcftools_pluginCommand=+fill-tags test; Date=unfixed\n#CHROM')
        self.assertEqual(C.canonical(raw,'vcf'),C.canonical(self.vcf,'vcf'))
        scientific=self.vcf.replace(b'##source=',b'##source=changed-')
        self.assertNotEqual(C.canonical(scientific,'vcf'),C.canonical(self.vcf,'vcf'))
        a=b'COMMAND: /a/b/samtools markdup -s x y\nDUPLICATE SINGLE: 2\n';b=b'COMMAND: hts markdup -s x y\nDUPLICATE SINGLE: 2\n'
        self.assertEqual(C.canonical(a,'text'),C.canonical(b,'text'))
        self.assertNotEqual(C.canonical(a,'text'),C.canonical(b.replace(b'SINGLE: 2',b'SINGLE: 3'),'text'))
    def test_malformed_vcf_rejected(self):
        for raw in [self.vcf.replace(b'AF=0.1;',b'AF=0.1;AF=0.2;',1),self.vcf.replace(b'chr1\t101\t',b'chr1\t0\t',1),self.vcf.split(b'#CHROM')[0]]:
            with self.assertRaises((AssertionError,ValueError)):C.parse_vcf(raw)
    def test_independent_allele_count_contract(self):
        data=C.parse_vcf(self.vcf);lines=self.vcf.decode().splitlines();updated=[]
        import re
        for line in lines:
            if line.startswith('#'):updated.append(line);continue
            cols=line.split('\t');ac=[0]*len(cols[4].split(','));an=ns=0
            for sample in cols[9:]:
                alleles=[int(x) for x in re.split(r'[/|]',sample.split(':')[0]) if x!='.'];an+=len(alleles);ns+=bool(alleles)
                for a in alleles:
                    if a:ac[a-1]+=1
            cols[7]=';'.join(['AN='+str(an),'NS='+str(ns),'AC='+','.join(map(str,ac)),'AF='+','.join(format(a/an,'.6g') for a in ac)])
            updated.append('\t'.join(cols))
        output=('\n'.join(updated)+'\n').encode()
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);(folder/'source.vcf').write_bytes(self.vcf)
            truth=C.contract(output,'vcf',{'alleleCountsFrom':'source.vcf'},folder)
            self.assertEqual(len(truth['alleleCountsFrom']),12)
            for before,after in [(b'AN=6',b'AN=7'),(b'NS=3',b'NS=4'),(b'AF=0.333333',b'AF=0.333334')]:
                with self.assertRaises(AssertionError):C.contract(output.replace(before,after,1),'vcf',{'alleleCountsFrom':'source.vcf'},folder)
    def test_trio_truth_rejects_wrong_mendelian_count(self):
        good=b'FLT0\tchild\tfather\tmother\t5\t4\t1\t1\t1\t1\t4\t0\tinf\t0\t0\n'
        C.contract(good,'text',{'trioTruth':True},ROOT)
        with self.assertRaises(AssertionError):C.contract(good.replace(b'5\t4\t1',b'5\t4\t0'),'text',{'trioTruth':True},ROOT)
    def test_large_logical_index_is_really_64bit_without_inflation(self):
        files=P.synthetic();index=files['logical-offset.bgz.gzi'];count=struct.unpack_from('<Q',index)[0]
        compressed,uncompressed=struct.unpack_from('<QQ',index,8+16*(count-1));self.assertGreater(uncompressed,2**32);self.assertGreater(compressed,65535)
        block=files['logical-offset.bgz'][compressed:];decoded,offsets=C.bgzf(block);self.assertEqual(decoded[2:6],b'ILMA');self.assertEqual(P.LOGICAL_SEEK_OFFSET,uncompressed+2)
    def test_bgzf_rejects_corrupt_integrity(self):
        import gzip
        files=P.synthetic();raw=files['logical-offset.bgz'];size=struct.unpack_from('<H',raw,16)[0]+1;block=raw[:size]
        broken=bytearray(block);broken[-8]^=1
        with self.assertRaises((OSError,AssertionError)):C.bgzf(bytes(broken))
    def test_optical_duplicate_contract_rejects_saturation(self):
        raw=P.synthetic()['optical-width.sam'].decode();lines=[]
        for line in raw.splitlines():
            if ':2147483900:' in line:line=line.replace('\t0\tchr1','\t1024\tchr1')+'\tdt:Z:LB'
            if ':2147483698:' in line:line=line.replace('\t0\tchr1','\t1024\tchr1')+'\tdt:Z:SQ'
            lines.append(line)
        good=('\n'.join(lines)+'\n').encode();C.contract(good,'sam',{'opticalDuplicates':True},ROOT)
        with self.assertRaises(AssertionError):C.contract(good.replace(b'dt:Z:LB',b'dt:Z:SQ'),'sam',{'opticalDuplicates':True},ROOT)
    def test_plan_covers_stock_plugins_both_versions(self):
        plan=json.loads((ROOT/'validation/hts/cases.json').read_text());lock=json.loads((ROOT/'tools/hts/versions.lock.json').read_text())
        for version in ['1.16','1.21']:
            cases=[x for x in plan['cases'] if version in x.get('versions',['1.16','1.21'])];names={n for x in cases for n in x['plugins']}
            self.assertEqual(names,set(lock['versions'][version]['plugins']));self.assertEqual(len(names),39)
            self.assertTrue(all(x['artifacts'] for x in cases if x['plugins']))
            self.assertTrue(all(x['inputs'] and not any('--help' in s['args'] or s['args'][-1:] == ['-h'] for s in x['steps']) for x in cases if x['plugins']))
    def test_subsampling_truth_preserves_order_and_mates(self):
        raw=(ROOT/'datasets/hts/common/alignments.sam').read_bytes()
        names=[row[0] for row in C.parse_sam(raw)['records']]
        C.contract(raw,'sam',{'readNames':names},ROOT)
        with self.assertRaises(AssertionError):C.contract(raw.replace(b'pair\t147',b'other\t147'),'sam',{'readNames':names},ROOT)
if __name__=='__main__':unittest.main()
