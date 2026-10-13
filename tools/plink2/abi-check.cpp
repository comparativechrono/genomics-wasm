#include <climits>
#include <cstdint>
#include <sys/types.h>
#include "plink2_base.h"

static_assert(CHAR_BIT == 8, "PLINK2 file formats require 8-bit bytes");
static_assert(sizeof(uint32_t) == 4 && sizeof(uint64_t) == 8,
              "PLINK2 genotype integer widths must be preserved");
static_assert(sizeof(float) == 4 && sizeof(double) == 8,
              "PLINK2 dosage and statistical numeric widths must be preserved");
static_assert(sizeof(off_t) == 8, "PLINK2 file offsets must remain 64-bit");
static_assert(__BYTE_ORDER__ == __ORDER_LITTLE_ENDIAN__,
              "This build requires little-endian PGEN representation");
#ifdef __EMSCRIPTEN__
static_assert(sizeof(uintptr_t) == 4, "This port is validated for wasm32");
#endif
