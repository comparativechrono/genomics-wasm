/* SPDX-License-Identifier: MIT
 * The upstream junction tables encode counts in void*. Keep the portable
 * build within the signed 32-bit pointer-difference range on every platform.
 */
#ifndef GENOMICS_WASM_FEATURECOUNTS_COUNT_GUARD_H
#define GENOMICS_WASM_FEATURECOUNTS_COUNT_GUARD_H
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

static inline void *fc_checked_junction_count_add(void *value, uintptr_t increment)
{
    uintptr_t current = (uintptr_t)value;
    if (current > INT32_MAX || increment > (uintptr_t)INT32_MAX - current) {
        fputs("featureCounts: junction/splice-site support exceeds this build's limit of 2147483647; use the native upstream program for this input.\n", stderr);
        exit(2);
    }
    return (void *)(current + increment);
}
#endif
