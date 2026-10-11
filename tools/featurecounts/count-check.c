/* SPDX-License-Identifier: MIT */
#include <string.h>
#include "count-guard.h"

int main(int argc, char **argv)
{
    if (argc == 2 && strcmp(argv[1], "overflow") == 0) {
        fc_checked_junction_count_add((void *)(uintptr_t)INT32_MAX, 1);
        return 9; /* The overflow branch must terminate before this point. */
    }
    if (argc == 2 && strcmp(argv[1], "merge-overflow") == 0) {
        fc_checked_junction_count_add((void *)(uintptr_t)(INT32_MAX - 10), 11);
        return 9;
    }
    if ((uintptr_t)fc_checked_junction_count_add(NULL, 0) != 0) return 1;
    if ((uintptr_t)fc_checked_junction_count_add((void *)(uintptr_t)(INT32_MAX - 1), 1) != INT32_MAX) return 1;
    if ((uintptr_t)fc_checked_junction_count_add((void *)(uintptr_t)100, 23) != 123) return 1;
    puts("FEATURECOUNTS_COUNT_GUARD_OK");
    return 0;
}
