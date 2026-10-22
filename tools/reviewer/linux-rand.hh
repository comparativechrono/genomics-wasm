// REViewer's only rand() call chooses between equal-scoring fragment origins.
// Preserve the default GNU libc Linux sequence, scoped to that call site.
// This is an independent implementation of the degree-31 additive generator;
// it does not replace libc rand() or alter any other program's random state.
// SPDX-License-Identifier: GPL-3.0-or-later
#ifndef REVIEWER_LINUX_RAND_HH
#define REVIEWER_LINUX_RAND_HH
#include <cstdint>
namespace reviewer_portability {
class LinuxDefaultRandom {
    std::uint32_t state_[31];
    unsigned front_ = 3, rear_ = 0;
public:
    LinuxDefaultRandom() {
        state_[0] = 1;
        for (unsigned i = 1; i < 31; ++i)
            state_[i] = (std::uint64_t(16807) * state_[i - 1]) % 2147483647;
        for (unsigned i = 0; i < 310; ++i) next();
    }
    int next() {
        state_[front_] += state_[rear_]; // Defined modulo-2^32 addition.
        const int result = static_cast<int>(state_[front_] >> 1);
        front_ = (front_ + 1) % 31;
        rear_ = (rear_ + 1) % 31;
        return result;
    }
};
inline int linuxDefaultRand() {
    static LinuxDefaultRandom generator;
    return generator.next();
}
}
#endif
