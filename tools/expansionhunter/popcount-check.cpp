// Exercise the width-dependent sparsepp compatibility branch independently.
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <limits>
#include "sparsepp/spp_utils.h"

static unsigned countBits(size_t value)
{
    unsigned result = 0;
    while (value) { result += value & 1; value >>= 1; }
    return result;
}

static bool check(size_t value)
{
    unsigned trailing = 0;
    size_t copy = value;
    if (copy == 0) trailing = sizeof(size_t) * 8;
    else while ((copy & 1) == 0) { ++trailing; copy >>= 1; }
    return spp::s_popcount(value) == countBits(value)
        && spp::count_trailing_zeroes(value) == trailing;
}

int main()
{
    bool ok = check(0) && check(std::numeric_limits<size_t>::max());
    for (unsigned i = 0; i < sizeof(size_t) * 8; ++i)
    {
        size_t value = size_t(1) << i;
        ok = ok && check(value) && check(~value) && check(value | (value >> 1));
    }
    uint64_t state = UINT64_C(0x123456789abcdef);
    for (unsigned i = 0; i < 100000; ++i)
    {
        state = state * UINT64_C(6364136223846793005) + UINT64_C(1442695040888963407);
        ok = ok && check(static_cast<size_t>(state));
    }
    if (!ok) { std::fputs("sparsepp popcount compatibility check failed\n", stderr); return 1; }
    std::printf("sparsepp popcount: 100000 patterns and %zu-bit edge cases passed\n", sizeof(size_t) * 8);
    return 0;
}
