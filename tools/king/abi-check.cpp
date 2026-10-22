// Original portability checks for the local KING build; no upstream code.
#include <climits>
#include <cstdint>
static_assert(CHAR_BIT == 8, "KING requires 8-bit bytes");
static_assert(sizeof(unsigned short) == 2, "KING short genotype words require 16 bits");
static_assert(sizeof(unsigned int) == 4, "KING integer counters require 32 bits");
static_assert(sizeof(unsigned long long) == 8, "KING genotype words require 64 bits");
static_assert(sizeof(double) == 8, "KING matrix code requires 64-bit double");
#ifdef __EMSCRIPTEN__
static_assert(sizeof(void*) == 4, "This profile targets wasm32");
#else
static_assert(sizeof(void*) == 8 && sizeof(unsigned long) == 8,
              "The pristine native reference requires an LP64 host");
#endif
