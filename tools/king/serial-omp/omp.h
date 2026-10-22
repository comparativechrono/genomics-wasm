// Original single-thread compatibility layer for local KING builds.
// Compile WITHOUT -fopenmp: the compiler ignores OpenMP pragmas, while
// KING's guarded initialization, output headers, and cleanup still run.
#ifndef GENOMICS_KING_SERIAL_OMP_H
#define GENOMICS_KING_SERIAL_OMP_H
static inline int omp_get_max_threads() { return 1; }
static inline int omp_get_thread_num() { return 0; }
#endif
