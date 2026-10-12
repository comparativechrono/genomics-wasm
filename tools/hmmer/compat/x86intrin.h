/* HMMER's selected implementation needs only SSE/SSE2. Emscripten provides
 * these headers, while Clang's catch-all x86intrin.h also imports unsupported
 * x86-only instructions. No intrinsic implementation is changed here. */
#ifndef GENOMICS_HMMER_X86INTRIN_H
#define GENOMICS_HMMER_X86INTRIN_H
#include <emmintrin.h>
#endif
