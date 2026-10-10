/* SPDX-License-Identifier: MIT
 * Local-file/stdin implementation of the BWA kopen interface.
 * Browser callers must supply files, not URLs or shell process expressions.
 */
#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

void *kopen(const char *name, int *fd)
{
    const char *p = name;
    void *handle;
    *fd = -1;
    while (isspace((unsigned char)*p)) ++p;
    if (*p == '<' || strstr(name, "://") != NULL) {
        fputs("[bwa] URLs and shell process inputs are unavailable in this build; supply a local file.\n", stderr);
        errno = ENOTSUP;
        return NULL;
    }
    *fd = strcmp(name, "-") == 0 ? STDIN_FILENO : open(name, O_RDONLY);
    if (*fd < 0) return NULL;
    handle = malloc(1);
    if (handle == NULL) {
        if (*fd != STDIN_FILENO) close(*fd);
        *fd = -1;
    }
    return handle;
}

int kclose(void *handle)
{
    /* BWA closes the gzip stream, which owns the opened descriptor. */
    free(handle);
    return 0;
}
