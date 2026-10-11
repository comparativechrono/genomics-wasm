"""Adversarial counterexamples for RNA-analysis biological comparisons."""
import gzip
import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('rna_compare', Path(__file__).parents[1] / 'validation/rna-analysis/compare.py')
compare = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(compare)


class RNACompareTests(unittest.TestCase):
    def check(self, tool, files, changed=None, **extra):
        with tempfile.TemporaryDirectory() as temporary:
            a, b = Path(temporary) / 'a', Path(temporary) / 'b'
            a.mkdir(); b.mkdir()
            for directory, contents in [(a, files), (b, changed or files)]:
                for name, text in contents.items():
                    path = directory / name
                    if isinstance(text, bytes): path.write_bytes(text)
                    else: path.write_text(text)
            context = {'refs': {'chr1': 100}, 'outputs': list(files), 'require_nonzero': True, **extra}
            return compare.compare_directories(a, b, tool, context)

    def counts(self):
        return {'counts.tsv': '# Program:featureCounts v2.1.1; Command:"/native/featureCounts"\nGeneid\tChr\tStart\tEnd\tStrand\tLength\tinput.bam\ng1\tchr1\t1\t10\t+\t10\t2\ng2\tchr1\t20\t30\t-\t11\t1\n',
                'counts.tsv.summary': 'Status\tinput.bam\nAssigned\t3\nUnassigned_Unmapped\t1\n'}

    def gtf(self, reference=False, coverage=True):
        gene, transcript = ('ENSG001', 'ENST001') if reference else ('STRG.1', 'STRG.1.1')
        attrs = f'gene_id "{gene}"; transcript_id "{transcript}";'
        text = '# /native/stringtie -o transcripts.gtf input.bam\n# StringTie version 3.0.3\n'
        text += 'chr1\tStringTie\ttranscript\t1\t20\t1000\t+\t.\t' + attrs + (' cov "2.5"; FPKM "3.0"; TPM "1000000.0";' if coverage else '') + '\n'
        text += 'chr1\tStringTie\texon\t1\t10\t1000\t+\t.\t' + attrs + ' exon_number "1";\n'
        text += 'chr1\tStringTie\texon\t15\t20\t1000\t+\t.\t' + attrs + ' exon_number "2";\n'
        return {'transcripts.gtf': text}

    def depth(self):
        return {'coverage.mosdepth.summary.txt': 'chrom\tlength\tbases\tmean\tmin\tmax\nchr1\t100\t50\t0.50\t0\t1\ntotal\t100\t50\t0.50\t0\t1\n',
                'coverage.mosdepth.global.dist.txt': 'chr1\t0\t1.00\nchr1\t1\t0.50\ntotal\t0\t1.00\ntotal\t1\t0.50\n',
                'coverage.per-base.bed.gz': gzip.compress(b'chr1\t0\t50\t1\nchr1\t50\t100\t0\n')}

    def test_feature_counts_positive_exact_and_metadata_paths(self):
        files = self.counts(); changed = dict(files)
        changed['counts.tsv'] = files['counts.tsv'].replace('/native/featureCounts', '/work/featurecounts')
        self.assertTrue(self.check('featurecounts', files, changed, annotation_ids={'g1', 'g2'})['equal'])

    def test_feature_count_and_assignment_mutations_fail(self):
        files = self.counts()
        for name, before, after in [('counts.tsv', '\t2\n', '\t2.000001\n'), ('counts.tsv.summary', 'Assigned\t3', 'Assigned\t4')]:
            changed = {**files, name: files[name].replace(before, after)}
            self.assertFalse(self.check('featurecounts', files, changed)['equal'])

    def test_counts_reject_nonfinite_missing_genes_and_duplicate_features(self):
        files = self.counts()
        for change in [files['counts.tsv'].replace('\t2\n', '\tNaN\n'), files['counts.tsv'].replace('g2', 'unknown'), files['counts.tsv'] + files['counts.tsv'].splitlines()[2] + '\n']:
            with self.assertRaises(ValueError):
                self.check('featurecounts', files, {**files, 'counts.tsv': change}, annotation_ids={'g1', 'g2'})

    def test_counts_all_zero_rejected_unless_explicit_filter_case(self):
        files = self.counts(); files['counts.tsv'] = files['counts.tsv'].replace('\t2\n', '\t0\n').replace('\t1\n', '\t0\n')
        with self.assertRaises(ValueError): self.check('featurecounts', files)
        self.assertTrue(self.check('featurecounts', files, require_nonzero=False)['equal'])

    def test_gtf_preserves_reference_and_generated_gene_transcript_identities(self):
        for ref in [False, True]:
            files = self.gtf(ref)
            source = files['transcripts.gtf']
            for old, new in ([('ENSG001', 'ENSG002'), ('ENST001', 'ENST002')] if ref else [('STRG.1.1', 'STRG.1.2'), ('STRG.1"', 'STRG.2"')]):
                self.assertFalse(self.check('stringtie', files, {'transcripts.gtf': source.replace(old, new)})['equal'])

    def test_gtf_row_and_attribute_order_are_irrelevant_but_coverage_is_exact(self):
        files = self.gtf(); source = files['transcripts.gtf']; lines = source.splitlines()
        changed = '\n'.join(lines[:2] + list(reversed(lines[2:]))) + '\n'
        self.assertTrue(self.check('stringtie', files, {'transcripts.gtf': changed})['equal'])
        self.assertFalse(self.check('stringtie', files, {'transcripts.gtf': source.replace('2.5', '2.500001')})['equal'])

    def test_gtf_rejects_wrong_exon_parent_and_invalid_coordinates(self):
        files = self.gtf(); source = files['transcripts.gtf']
        changed = source.rsplit('gene_id "STRG.1"', 1)
        for bad in ['gene_id "wrong"'.join(changed), source.replace('\texon\t15\t20', '\texon\t15\t101')]:
            with self.assertRaises(ValueError): self.check('stringtie', files, {'transcripts.gtf': bad})

    def test_empty_merge_is_rejected_without_requiring_coverage_attribute(self):
        merge = self.gtf(coverage=False)
        self.assertTrue(self.check('stringtie', merge, require_nonzero=False)['equal'])
        with self.assertRaises(ValueError):
            self.check('stringtie', {'transcripts.gtf': '# StringTie version 3.0.3\n'}, require_nonzero=False)

    def test_gtf_wrong_version_and_reference_identity_changes_fail(self):
        files = self.gtf()
        self.assertFalse(self.check('stringtie', files, {'transcripts.gtf': files['transcripts.gtf'].replace('3.0.3', '2.0.0')})['equal'])

    def test_coverage_gzip_wrapper_is_ignored_but_depth_values_are_exact(self):
        files = self.depth(); changed = dict(files)
        changed['coverage.per-base.bed.gz'] = gzip.compress(gzip.decompress(files['coverage.per-base.bed.gz']), mtime=123)
        self.assertTrue(self.check('mosdepth', files, changed)['equal'])
        changed['coverage.per-base.bed.gz'] = gzip.compress(b'chr1\t0\t50\t2\nchr1\t50\t100\t0\n')
        with self.assertRaises(ValueError): self.check('mosdepth', files, changed)

    def test_coverage_gaps_and_summary_mismatch_rejected_even_when_both_sides_agree(self):
        for data in [b'chr1\t0\t49\t1\nchr1\t50\t100\t0\n', b'chr1\t0\t50\t2\nchr1\t50\t100\t0\n']:
            files = self.depth(); files['coverage.per-base.bed.gz'] = gzip.compress(data)
            with self.assertRaises(ValueError): self.check('mosdepth', files)

    def test_csi_requires_complete_structure_and_gzip_crc(self):
        valid = b'CSI\x01' + struct.pack('<iiii', 14, 5, 0, 1) + struct.pack('<i', 0) + struct.pack('<Q', 0)
        compare.check_csi(valid, {'chr1': 100})
        for data in [b'CSI\x01' + b'\x00'*12, valid[:-1], valid + b'garbage', gzip.compress(valid)[:-4]]:
            with self.assertRaises((ValueError, EOFError)): compare.check_csi(data, {'chr1': 100})

    def test_missing_output_fails_instead_of_comparing_partial_results(self):
        files = self.counts()
        with self.assertRaises(ValueError): self.check('featurecounts', files, {'counts.tsv': files['counts.tsv']})


if __name__ == '__main__': unittest.main()
