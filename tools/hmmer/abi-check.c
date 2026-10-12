/* Assert the on-disk primitive widths used by upstream HMMER's SSE profile IO.
 * No upstream structure or serialization algorithm is replaced here. */
#include <sys/types.h>
#include <stdint.h>
#include <limits.h>
#include <emmintrin.h>

_Static_assert(CHAR_BIT == 8, "HMMER profile interchange requires 8-bit bytes");
_Static_assert(sizeof(int) == 4, "HMMER profile interchange requires 32-bit int");
_Static_assert(sizeof(float) == 4, "HMMER profile interchange requires 32-bit float");
_Static_assert(sizeof(double) == 8, "HMMER profile interchange requires 64-bit double");
_Static_assert(sizeof(off_t) == 8, "HMMER profile interchange requires 64-bit offsets");
_Static_assert(sizeof(__m128) == 16 && sizeof(__m128i) == 16,
               "HMMER profile interchange requires 128-bit SIMD vectors");
#if __BYTE_ORDER__ != __ORDER_LITTLE_ENDIAN__
#error "Native/WebAssembly pressed-profile interchange requires little endian."
#endif
#if defined(__EMSCRIPTEN__) && !defined(__wasm_simd128__)
#error "HMMER WebAssembly requires SIMD128."
#endif
