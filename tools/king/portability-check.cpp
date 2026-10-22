// Original regression check linking the downloaded KING String class.
#include "StringBasics.h"
#include <climits>
#include <cstdio>
#include <cstring>

int main()
{
    const unsigned values[] = {0U, 9U, 10U, 999999999U, 1000000000U, 2147483648U, UINT_MAX};
    for (unsigned value : values) {
        String rendered;
        rendered = value;
        char expected[32];
        std::snprintf(expected, sizeof expected, "%u", value);
        if (std::strcmp(static_cast<const char*>(rendered), expected)) {
            std::fprintf(stderr, "Unsigned decimal conversion failed for %u\n", value);
            return 1;
        }
        std::puts(static_cast<const char*>(rendered));
    }
    return 0;
}
