# Fixed-width portability patches

The release sources assume an LP64 host in several counters and offsets.
Emscripten wasm32 has 32-bit `long` but supports 64-bit integer arithmetic.
These version-specific patches preserve the upstream Linux LP64 widths and
algorithms; apply each only to its named project and version. The unmodified
native reference receives none of these patches. Patched native and WASM
receive the same patches.

| Patch prefix | Source and change | Regression |
| --- | --- | --- |
| `portability` | BCFtools `vcfstats.c`: sample depth totals and depth-histogram denominators use `uint64_t`. | Three sites with DP 2,000,000,000 retain average depth 2,000,000,000. |
| `portability` | BCFtools `prob1.c`: consensus-caller PL ordering accumulators use `int64_t`, including allocation and clearing. | Preserves the native 64-bit sum before its four-bit allele-index shift; ordinary consensus-caller comparisons also exercise the path. |
| `htslib-portability` | HTSlib `bgzip.c`: logical uncompressed offsets/sizes use `int64_t`, `atoll`, and `PRId64`. | Indexed four-byte read beyond 4 GiB returns the unique marker without inflating the prefix. |
| `samtools-portability` | SAMtools `bam_markdup.c`: optical coordinates, counters, and library-size estimates use explicit 64-bit types, matching format macros, and `strtoll` for coordinates. `sam_view.c` also parses its `uint32_t` sampling seed through `strtoll` to preserve the full native range. | Two duplicate groups above INT32_MAX remain distinguishable as library and optical duplicates. |

The parsing changes retain the existing upstream validation and behavior for
values in the native 64-bit range. `strtoll` has the same 64-bit range and
overflow behavior as LP64 `strtol`; no new accepted syntax or range policy is
introduced. The original code does not consistently reject every malformed
or out-of-range argument, and these patches do not redefine that behavior.

The plugin preparer separately fixes `GTisec`'s `uint64_t` lookup-table
allocation, using `sizeof(*args.quick)` rather than `sizeof(unsigned long)`.
This avoids allocating half the required storage under wasm32.

`validation/hts/portability_fixtures.py` supplies deterministic BGZF/GZI and
optical-duplicate fixtures with independent expected results. Native reference,
patched native, and WASM must agree; native equality alone is insufficient
when both builds could share a patch error.
