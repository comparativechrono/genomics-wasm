/* SPDX-License-Identifier: MIT
 * Browser builds load normal index files and do not use POSIX shared memory.
 */
#include <stdio.h>
#include "bwa.h"

bwaidx_t *bwa_idx_load_from_shm(const char *hint)
{
    (void)hint;
    return NULL;
}

int main_shm(int argc, char *argv[])
{
    (void)argc;
    (void)argv;
    fputs("[bwa] The shm command is unavailable in this build; load index files normally.\n", stderr);
    return 2;
}
