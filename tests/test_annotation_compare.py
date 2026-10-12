"""Mutation tests for complete-output and evidence comparison contracts."""
import importlib.util
import tempfile
import unittest
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / 'validation/annotation/compare.py'
spec = importlib.util.spec_from_file_location('annotation_compare', MODULE)
compare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compare)

FASTA = '>gene1 # 1 # 9 # 1 # ID=1_1;partial=00\nMA*\n>gene2\nMQQ*\n'
GFF = '##gff-version  3\nseq\tProdigal_v2.6.3\tCDS\t1\t9\t5.0\t+\t0\tID=1_1;partial=00\n'
TABLE = '# target query table\ntarget - model - 1e-12 40.1 0.0 2e-12 39.5 0.0 1.0 1 0 0 1 1 1 1 description\n# Option settings: /native/hmmsearch --seed 42 model.hmm seq.faa \n# Current dir: /native/path\n# Date: Mon Oct 12 21:03:00 2026\n# [ok]\n'
DOMAIN = 'target - 100 model - 80 1e-12 40.1 0.0 1 1 1e-12 1e-12 39.5 0.0 1 80 3 82 2 84 0.99 description\n'
DNA = 'chr1 - MADE1 - 1 80 100 179 99 180 500 + 1e-12 40.1 0.0 description\n'
STOCKHOLM = '# STOCKHOLM 1.0\none AC-D\ntwo ACED\n#=GC RF xx.x\n//\n'
HMM = 'HMMER3/f [3.4 | Aug 2023]\nNAME  family\nLENG  1\nALPH  amino\nDATE  Mon Oct 12 21:03:00 2026\nSTATS LOCAL MSV -9.8664 0.70955\nHMM A C\n  COMPO 1.1 2.2\n  1 1.2 2.3 1 A - - -\n//\n'


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.left, self.right = self.root/'native', self.root/'wasm'
        self.left.mkdir();self.right.mkdir()

    def compare(self, native, actual=None, kind='text', **kwargs):
        for directory, content in ((self.left, native), (self.right, native if actual is None else actual)):
            (directory/'out').write_bytes(content if isinstance(content, bytes) else content.encode())
        return compare.compare(self.left, self.right, {'artifacts': [{'path': 'out', 'kind': kind, **kwargs}]})

    def test_exact_fasta_preserves_sequences_headers_and_record_order(self):
        baseline = self.compare(FASTA, kind='fasta')
        self.assertTrue(baseline['equal'])
        self.assertEqual(baseline['invariants']['out']['records'], 2)
        for replacement in (FASTA.replace('MA*', 'MK*'), FASTA.replace('partial=00', 'partial=10'),
                            FASTA.replace('gene1', 'different'), '>gene2\nMQQ*\n>gene1 # 1 # 9 # 1 # ID=1_1;partial=00\nMA*\n'):
            with self.subTest(replacement=replacement):
                result = self.compare(FASTA, replacement, 'fasta')
                self.assertFalse(result['equal'])
                self.assertNotEqual(baseline['biologicalDigest'], result['biologicalDigest'])

    def test_duplicate_and_empty_fasta_records_rejected(self):
        for bad in ('', '>id\n', '>id\nACGT\n>id\nACGT\n', 'ACGT\n', '>id\nAC1T\n'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.compare(bad, kind='fasta')

    def test_gff_keeps_every_biological_field(self):
        self.assertTrue(self.compare(GFF, kind='gff')['equal'])
        for before, after in [('5.0','5.1'), ('1\t9','2\t9'), ('\t+\t','\t-\t'), ('partial=00','partial=10')]:
            self.assertFalse(self.compare(GFF, GFF.replace(before, after), 'gff')['equal'])

    def test_gff_rejects_inverted_coordinates_bad_strand_and_missing_id(self):
        for bad in (GFF.replace('\t1\t9\t','\t10\t9\t'), GFF.replace('\t+\t','\t?\t'), GFF.replace('ID=1_1','name=1_1')):
            with self.assertRaises(ValueError):self.compare(bad, kind='gff')

    def test_hmmer_tables_normalise_only_declared_metadata(self):
        changed=TABLE.replace('/native/hmmsearch','/wasm/hmmsearch').replace('/native/path','/work').replace('21:03:00','23:45:00')
        result=self.compare(TABLE,changed,'hmmer-table')
        self.assertTrue(result['equal'])
        self.assertNotEqual(result['actualFiles'],result['expectedFiles'])
        self.assertEqual(result['semanticDigest'],result['expectedSemanticDigest'])
        self.assertEqual(result['numericTolerance'],0)
        self.assertEqual(result['invariants']['out'],{'records':1,'queries':1,'targets':1})

    def test_any_reported_score_probability_or_count_change_fails(self):
        for before,after in [('1e-12','1.1e-12'),('40.1','40.2'),('1.0 1 0','1.1 1 0'),('target - model','other - model')]:
            self.assertFalse(self.compare(TABLE,TABLE.replace(before,after),'hmmer-table')['equal'])

    def test_threshold_effect_digest_ignores_metadata_but_not_rows(self):
        baseline=self.compare(TABLE,kind='hmmer-table')
        changed=TABLE.replace('--seed 42','--seed 42 -T 100')
        result=self.compare(TABLE,changed,'hmmer-table')
        self.assertFalse(result['equal'])
        self.assertEqual(result['biologicalDigest'],baseline['biologicalDigest'])
        result=self.compare(TABLE,TABLE.replace('40.1','40.2'),'hmmer-table')
        self.assertNotEqual(result['biologicalDigest'],baseline['biologicalDigest'])

    def test_table_columns_nan_truncation_and_unknown_table_type_rejected(self):
        for bad in (TABLE.replace('1e-12','nan'),TABLE.replace('40.1','inf'),'target - model - 1e-12\n'):
            with self.assertRaises(ValueError):self.compare(bad,kind='hmmer-table')
        with self.assertRaises(KeyError):self.compare(TABLE,kind='hmmer-table',table='unknown')

    def test_domain_table_checks_ordered_coordinates_and_probability(self):
        self.assertTrue(self.compare(DOMAIN,kind='hmmer-table',table='domain')['equal'])
        for bad in (DOMAIN.replace('1 80 3 82','81 80 3 82'),DOMAIN.replace('0.99','1.01')):
            with self.assertRaises(ValueError):self.compare(bad,kind='hmmer-table',table='domain')

    def test_nucleotide_table_preserves_negative_strand_coordinates(self):
        reverse=DNA.replace('100 179','179 100').replace('99 180','180 99').replace('500 +','500 -')
        self.assertTrue(self.compare(reverse,kind='hmmer-table',table='dna')['equal'])
        self.assertFalse(self.compare(DNA,reverse,'hmmer-table',table='dna')['equal'])

    def test_stockholm_keeps_gaps_and_posterior_annotations(self):
        baseline=self.compare(STOCKHOLM,kind='stockholm')
        self.assertEqual(baseline['invariants']['out'],{'records':2,'columns':4})
        self.assertFalse(self.compare(STOCKHOLM,STOCKHOLM.replace('AC-D','ACD-'),'stockholm')['equal'])
        self.assertFalse(self.compare(STOCKHOLM,STOCKHOLM.replace('xx.x','x.xx'),'stockholm')['equal'])
        with self.assertRaises(ValueError):self.compare(STOCKHOLM.replace('ACED','ACE'),kind='stockholm')

    def test_hmm_normalises_date_but_preserves_probabilities_names_and_stats(self):
        self.assertTrue(self.compare(HMM,HMM.replace('21:03:00','23:45:00'),'hmm')['equal'])
        for before,after in [('family','other'),('-9.8664','-9.8665'),('1.2 2.3','1.2 2.4')]:
            self.assertFalse(self.compare(HMM,HMM.replace(before,after),'hmm')['equal'])
        with self.assertRaises(ValueError):self.compare(HMM.replace('//\n',''),kind='hmm')

    def test_timings_are_normalised_without_dropping_other_statistics(self):
        text='# CPU time: 1.00u 0.10s 00:00:01.10 Elapsed: 00:00:02.10\n# Mc/sec: 1.00\nTarget sequences: 45\n'
        other=text.replace('1.00u','5.00u').replace('# Mc/sec: 1.00','# Mc/sec: 9.50')
        self.assertTrue(self.compare(text,other,'hmmer-text')['equal'])
        self.assertFalse(self.compare(text,text.replace('45','46'),'hmmer-text')['equal'])
        self.assertFalse(self.compare(text,text.replace('1.00u','unknown'),'hmmer-text')['equal'])

    def test_binary_artifacts_are_exact_with_bound_raw_hashes(self):
        result=self.compare(b'\x00\x01\x02',kind='exact')
        self.assertTrue(result['equal'])
        self.assertEqual(result['actualFileBytes'],{'out':3})
        self.assertFalse(self.compare(b'\x00\x01\x02',b'\x00\x01\x03','exact')['equal'])
        with self.assertRaises(ValueError):self.compare(b'',kind='exact')

    def test_required_biological_coverage_is_enforced_on_both_sides(self):
        for kind,value,kwargs in [('fasta',FASTA,{'minimumRecords':3}),('gff',GFF,{'minimum':{'reverse':1}}),('hmmer-table',TABLE,{'minimumRecords':2})]:
            with self.subTest(kind=kind),self.assertRaises(ValueError):self.compare(value,kind=kind,**kwargs)

    def test_crlf_only_line_ending_normalisation_and_invalid_controls(self):
        self.assertTrue(self.compare(FASTA,FASTA.replace('\n','\r\n'),'fasta')['equal'])
        for control in ('\x00','\r','\v','\x7f','\u2028','\ufeff'):
            with self.subTest(control=repr(control)),self.assertRaises(ValueError):self.compare(FASTA.replace('gene1','gene1'+control),kind='fasta')
        with self.assertRaises(UnicodeDecodeError):self.compare(b'bad\xff',kind='text')

    def test_empty_duplicate_unknown_artifact_and_invalid_biological_selection(self):
        (self.left/'out').write_text('some text\n');(self.right/'out').write_text('some text\n')
        artifact={'path':'out','kind':'text'}
        for spec in ({'artifacts':[]},{'artifacts':[artifact,artifact]},{'artifacts':[{'path':'out','kind':'unknown'}]},
                     {'artifacts':[artifact],'biologicalArtifacts':[]},{'artifacts':[artifact],'biologicalArtifacts':['missing']}):
            with self.assertRaises(ValueError):compare.compare(self.left,self.right,spec)

    def test_unsafe_paths_and_escaping_symlinks_rejected(self):
        for path in ('../escape','/absolute','x/../out','./out',''):
            with self.assertRaises(ValueError):compare.safe_file(self.left,path)
        (self.root/'outside').write_text('outside')
        (self.left/'link').symlink_to(self.root/'outside')
        with self.assertRaises(ValueError):compare.safe_file(self.left,'link')


if __name__=='__main__':unittest.main()
