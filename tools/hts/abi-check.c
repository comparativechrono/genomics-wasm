#include <stdint.h>
#include <sys/types.h>
#include "htslib/hts.h"
_Static_assert(sizeof(off_t) == 8, "HTS file offsets require 64 bits");
_Static_assert(sizeof(hts_pos_t) == 8, "HTS genomic coordinates require 64 bits");
_Static_assert(sizeof(uint64_t) == 8, "HTS counters require 64 bits");
#ifdef __EMSCRIPTEN__
_Static_assert(sizeof(void *) == 4, "This bundle targets wasm32");
#endif
