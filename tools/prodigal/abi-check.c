/* The upstream training file is a raw struct. Keep its ordinary little-endian
 * x86-64 layout on wasm32 so native and browser training files are exchangeable.
 * These assertions add no executable code or changes to the gene finder. */
#include <stddef.h>
#include "training.h"

_Static_assert(sizeof(int) == 4, "Prodigal training files require 32-bit int");
_Static_assert(sizeof(double) == 8, "Prodigal training files require binary64");
_Static_assert(sizeof(struct _training) == 558392, "Unexpected training layout");
_Static_assert(offsetof(struct _training, st_wt) == 16, "Unexpected start weight offset");
_Static_assert(offsetof(struct _training, rbs_wt) == 80, "Unexpected RBS weight offset");
_Static_assert(offsetof(struct _training, mot_wt) == 1328, "Unexpected motif weight offset");
_Static_assert(offsetof(struct _training, gene_dc) == 525624, "Unexpected coding weight offset");
#if defined(__BYTE_ORDER__) && __BYTE_ORDER__ != __ORDER_LITTLE_ENDIAN__
#error "Prodigal training-file exchange requires a little-endian target"
#endif
