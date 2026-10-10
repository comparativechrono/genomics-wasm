// Command dispatcher for the standalone HISAT2 WebAssembly port.
// SPDX-License-Identifier: GPL-3.0-or-later
#include <cstdio>
#include <cstring>
extern "C" int hisat2(int, const char **);
extern "C" int hisat2_build(int, const char **);
int hisat2_inspect_main(int, char **);

int main(int argc, char **argv) {
    if (argc > 1 && std::strcmp(argv[1], "build") == 0)
        return hisat2_build(argc - 1, const_cast<const char **>(argv + 1));
    if (argc > 1 && std::strcmp(argv[1], "inspect") == 0)
        return hisat2_inspect_main(argc - 1, argv + 1);
    if (argc > 1 && std::strcmp(argv[1], "align") == 0)
        return hisat2(argc - 1, const_cast<const char **>(argv + 1));
    if (argc > 1 && argv[1][0] == '-')
        return hisat2(argc, const_cast<const char **>(argv));
    std::fprintf(stderr, "Usage: hisat2 build|align|inspect [arguments]\n");
    return 1;
}
