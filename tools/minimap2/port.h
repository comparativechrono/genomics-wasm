/* SPDX-License-Identifier: MIT
 * Port-specific checks, used by patched native and WebAssembly builds alike.
 */
#ifndef GENOMICS_WASM_MINIMAP2_PORT_H
#define GENOMICS_WASM_MINIMAP2_PORT_H
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static inline int minimap2_port_threads(const char *value)
{
    if (strcmp(value, "1") != 0) {
        fputs("[minimap2] This build supports one thread. Use -t 1.\n", stderr);
        exit(2);
    }
    return 1;
}
#endif
