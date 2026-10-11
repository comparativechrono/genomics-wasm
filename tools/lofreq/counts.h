/* Portable parsing for upstream LoFreq multiple-testing counts. */
#ifndef LOFREQ_PORT_COUNTS_H
#define LOFREQ_PORT_COUNTS_H
#include <errno.h>
#include <limits.h>
#include <stdlib.h>

_Static_assert(sizeof(long long) * CHAR_BIT == 64, "LoFreq test counts require a 64-bit long long");

static int lofreq_parse_test_count(const char *text, long long *result)
{
    const char *p = text;
    char *end;
    long long value;
    /* Preserve the upstream nonnegative CLI domain, while rejecting partial
       conversions and overflow instead of silently changing the test count. */
    if (!*p) return 0;
    for (; *p; ++p)
        if (*p < '0' || *p > '9') return 0;
    errno = 0;
    value = strtoll(text, &end, 10);
    if (errno == ERANGE || *end || value < 0) return 0;
    *result = value;
    return 1;
}
#endif
