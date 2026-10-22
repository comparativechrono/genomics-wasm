#include <cstdint>
#include <sys/types.h>
#include "htslib/hts.h"
static_assert(sizeof(off_t) == 8, "HTS file offsets require 64 bits");
static_assert(sizeof(hts_pos_t) == 8, "HTS genomic coordinates require 64 bits");
static_assert(sizeof(std::uint32_t) == 4, "The scoped RNG requires uint32_t");
#ifdef __EMSCRIPTEN__
static_assert(sizeof(void*) == 4, "This build targets wasm32");
#endif
