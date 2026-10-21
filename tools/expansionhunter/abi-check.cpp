#include <cstdint>
#include <sys/types.h>
#include <htslib/hts.h>
static_assert(sizeof(off_t) == 8, "ExpansionHunter requires 64-bit file offsets");
static_assert(sizeof(hts_pos_t) == 8, "HTSlib genomic positions must remain 64-bit");
static_assert(sizeof(std::int64_t) == 8, "Genomic coordinates require 64-bit integers");
