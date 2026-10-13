/* Compile-time assumptions used by Primer3 and its binary k-mer reader. */
#include <limits.h>
#include <stdint.h>
#include <sys/types.h>
_Static_assert(CHAR_BIT == 8, "Primer3 requires 8-bit bytes");
_Static_assert(sizeof(unsigned int) == 4, "K-mer counts require 32-bit unsigned int");
_Static_assert(sizeof(unsigned long long) == 8, "K-mer words require 64-bit integers");
_Static_assert(sizeof(double) == 8, "Primer3 thermodynamics requires binary64 doubles");
#ifdef __EMSCRIPTEN__
_Static_assert(sizeof(void *) == 4, "This build targets wasm32");
_Static_assert(sizeof(off_t) == 8, "File sizes must not truncate to 32 bits");
#endif
