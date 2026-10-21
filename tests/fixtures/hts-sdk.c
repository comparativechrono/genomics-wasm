/* Public-API consumer: compile against the released HTSlib SDK, not tool objects. */
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/types.h>
#include <htslib/hts.h>
#include <htslib/sam.h>
#include <htslib/vcf.h>

_Static_assert(sizeof(off_t) == 8, "SDK must expose 64-bit file offsets");
_Static_assert(sizeof(hts_pos_t) == 8, "SDK must expose 64-bit genomic positions");

#define CHECK(condition) do { if (!(condition)) { \
    fprintf(stderr, "HTSlib SDK check failed at line %d: %s\n", __LINE__, #condition); \
    exit(2); } } while (0)

static void variants(const char *input, const char *output)
{
    htsFile *in = bcf_open(input, "r");
    CHECK(in);
    bcf_hdr_t *header = bcf_hdr_read(in);
    CHECK(header && bcf_hdr_nsamples(header) == 2);
    CHECK(strcmp(header->samples[0], "S1") == 0);
    CHECK(strcmp(header->samples[1], "S2") == 0);
    htsFile *out = bcf_open(output, "wb");
    CHECK(out && bcf_hdr_write(out, header) == 0);
    bcf1_t *record = bcf_init();
    CHECK(record);
    int count = 0, called = 0, alternate = 0, status;
    int32_t *genotypes = NULL;
    int capacity = 0;
    int64_t position_sum = 0;
    while ((status = bcf_read(in, header, record)) == 0) {
        int n = bcf_get_genotypes(header, record, &genotypes, &capacity);
        CHECK(n == 4);
        for (int i = 0; i < n; i++) {
            CHECK(genotypes[i] != bcf_int32_vector_end);
            if (!bcf_gt_is_missing(genotypes[i])) {
                called++;
                alternate += bcf_gt_allele(genotypes[i]);
            }
        }
        count++;
        position_sum += record->pos + 1;
        CHECK(bcf_write(out, header, record) == 0);
    }
    CHECK(status == -1 && count == 3 && called == 10 && alternate == 4 && position_sum == 600);
    free(genotypes);
    bcf_destroy(record);
    bcf_hdr_destroy(header);
    CHECK(bcf_close(in) == 0 && bcf_close(out) == 0);
    CHECK(bcf_index_build(output, 14) == 0);

    in = bcf_open(output, "rb");
    CHECK(in);
    header = bcf_hdr_read(in);
    CHECK(header);
    hts_idx_t *index = bcf_index_load(output);
    CHECK(index);
    hts_itr_t *iterator = bcf_itr_querys(index, header, "chr1:150-250");
    CHECK(iterator);
    record = bcf_init();
    CHECK(record);
    int queried = 0;
    genotypes = NULL;
    capacity = 0;
    while ((status = bcf_itr_next(in, iterator, record)) >= 0) {
        CHECK(record->pos == 199);
        CHECK(bcf_unpack(record, BCF_UN_STR) == 0);
        CHECK(record->n_allele == 2 && strcmp(record->d.allele[0], "G") == 0 && strcmp(record->d.allele[1], "T") == 0);
        CHECK(bcf_get_genotypes(header, record, &genotypes, &capacity) == 4);
        CHECK(genotypes[0] == bcf_gt_unphased(0) && genotypes[1] == bcf_gt_unphased(0));
        CHECK(bcf_gt_is_missing(genotypes[2]) && bcf_gt_is_missing(genotypes[3]));
        queried++;
    }
    CHECK(status == -1 && queried == 1);
    free(genotypes);
    bcf_destroy(record);
    hts_itr_destroy(iterator);
    hts_idx_destroy(index);
    bcf_hdr_destroy(header);
    CHECK(bcf_close(in) == 0);
    printf("\"variants\":{\"records\":%d,\"calledAlleles\":%d,\"alternateAlleles\":%d,"
           "\"positionSum\":%" PRId64 ",\"indexedRecords\":%d},", count, called, alternate, position_sum, queried);
}

static void alignments(const char *input, const char *output)
{
    samFile *in = sam_open(input, "r");
    CHECK(in);
    sam_hdr_t *header = sam_hdr_read(in);
    CHECK(header && sam_hdr_nref(header) == 2);
    samFile *out = sam_open(output, "wb");
    CHECK(out && sam_hdr_write(out, header) == 0);
    bam1_t *record = bam_init1();
    CHECK(record);
    int count = 0, mapped = 0, mapq_sum = 0, reference_bases = 0, status;
    while ((status = sam_read1(in, header, record)) >= 0) {
        count++;
        if (!(record->core.flag & BAM_FUNMAP)) {
            mapped++;
            mapq_sum += record->core.qual;
            reference_bases += bam_cigar2rlen(record->core.n_cigar, bam_get_cigar(record));
        }
        CHECK(sam_write1(out, header, record) >= 0);
    }
    CHECK(status == -1 && count == 4 && mapped == 3 && mapq_sum == 130 && reference_bases == 45);
    bam_destroy1(record);
    sam_hdr_destroy(header);
    CHECK(sam_close(in) == 0 && sam_close(out) == 0);
    CHECK(sam_index_build(output, 0) == 0);

    in = sam_open(output, "rb");
    CHECK(in);
    header = sam_hdr_read(in);
    CHECK(header);
    hts_idx_t *index = sam_index_load(in, output);
    CHECK(index);
    hts_itr_t *iterator = sam_itr_querys(index, header, "chr1:190-220");
    CHECK(iterator);
    record = bam_init1();
    CHECK(record);
    int queried = 0;
    while ((status = sam_itr_next(in, iterator, record)) >= 0) {
        CHECK(record->core.pos == 200 && strcmp(bam_get_qname(record), "r2") == 0);
        CHECK(record->core.qual == 40 && record->core.l_qseq == 10);
        CHECK(record->core.n_cigar == 1 && bam_cigar_oplen(bam_get_cigar(record)[0]) == 10);
        for (int i = 0; i < record->core.l_qseq; i++)
            CHECK(seq_nt16_str[bam_seqi(bam_get_seq(record), i)] == 'C' && bam_get_qual(record)[i] == 40);
        queried++;
    }
    CHECK(status == -1 && queried == 1);
    bam_destroy1(record);
    hts_itr_destroy(iterator);
    hts_idx_destroy(index);
    sam_hdr_destroy(header);
    CHECK(sam_close(in) == 0);
    printf("\"alignments\":{\"records\":%d,\"mapped\":%d,\"mappingQualitySum\":%d,"
           "\"referenceBases\":%d,\"indexedRecords\":%d}", count, mapped, mapq_sum, reference_bases, queried);
}

int main(int argc, char **argv)
{
    CHECK(argc == 5);
    printf("{\"htslib\":\"%s\",\"offsetBytes\":%zu,\"positionBytes\":%zu,", hts_version(), sizeof(off_t), sizeof(hts_pos_t));
    variants(argv[1], argv[3]);
    alignments(argv[2], argv[4]);
    puts("}");
    return 0;
}
