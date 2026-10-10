/* SPDX-License-Identifier: MIT */
#ifndef BWA_MEM2_PORT_H
#define BWA_MEM2_PORT_H
#include <stdint.h>
#include <time.h>
/* Profiling only: native TSC cycles are replaced by monotonic nanoseconds. */
static inline uint64_t mem2_ticks(void) {
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    return (uint64_t)now.tv_sec * UINT64_C(1000000000) + (uint64_t)now.tv_nsec;
}
#endif
