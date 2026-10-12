#include <cstddef>
#include <cstdint>
#include <climits>
#include <type_traits>
#include "BUSData.h"
#include "bustools_compress.h"

static_assert(CHAR_BIT == 8, "BUS files require octets");
static_assert(sizeof(uint32_t) == 4 && sizeof(uint64_t) == 8,
              "BUS headers require fixed-width integer fields");
static_assert(std::is_standard_layout<BUSData>::value, "BUSData must have a stable layout");
static_assert(sizeof(BUSData) == 32, "Native BUS records occupy exactly 32 bytes");
static_assert(offsetof(BUSData, barcode) == 0 && offsetof(BUSData, UMI) == 8 &&
              offsetof(BUSData, ec) == 16 && offsetof(BUSData, count) == 20 &&
              offsetof(BUSData, flags) == 24 && offsetof(BUSData, pad) == 28,
              "Native and WebAssembly BUS field offsets must match");
static_assert(sizeof(((BUSZIndex*)0)->n_blocks) == 8 &&
              sizeof(((BUSZIndex*)0)->block_size) == 8 &&
              sizeof(((BUSZIndex*)0)->last_block) == 8,
              "BUSZ indexes use 64-bit counts and offsets");
#if __BYTE_ORDER__ != __ORDER_LITTLE_ENDIAN__
#error "This build recipe supports little-endian BUS files only"
#endif
