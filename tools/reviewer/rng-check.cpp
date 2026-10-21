// Independent native-libc oracle for REViewer's scoped default-seed shim.
// SPDX-License-Identifier: GPL-3.0-or-later
#include <cstdlib>
#include <iostream>
#include "linux-rand.hh"
int main() {
#ifndef __GLIBC__
    std::cerr << "REViewer's native RNG reference requires GNU libc\n";
    return 1;
#else
    reviewer_portability::LinuxDefaultRandom generator;
    for (unsigned i = 0; i < 100000; ++i) {
        const int expected = std::rand();
        const int actual = generator.next();
        if (expected != actual) {
            std::cerr << "RNG mismatch at draw " << i << ": " << expected << " != " << actual << '\n';
            return 1;
        }
    }
    std::cout << "100000 default-seed draws match GNU libc rand()\n";
    return 0;
#endif
}
