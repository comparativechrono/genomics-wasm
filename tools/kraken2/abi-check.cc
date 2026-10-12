#include "kraken2_data.h"
#include "taxonomy.h"
#include <cstddef>
static_assert(sizeof(size_t) == 8 && sizeof(long) == 8, "Kraken2 databases require the native 64-bit ABI");
static_assert(sizeof(kraken2::IndexOptions) == 64, "Unexpected options record size");
static_assert(offsetof(kraken2::IndexOptions, dna_db) == 32, "Unexpected options layout");
static_assert(offsetof(kraken2::IndexOptions, minimum_acceptable_hash_value) == 40, "Unexpected options layout");
static_assert(sizeof(kraken2::TaxonomyNode) == 56, "Unexpected taxonomy node size");
