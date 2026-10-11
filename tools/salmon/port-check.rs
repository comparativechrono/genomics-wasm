//! Regression checks for the word sizes used by the Salmon WebAssembly port.
//!
//! Compile this binary against the same patched piscem-rs and salmon-model
//! dependencies as Salmon, and run it in both native and WebAssembly builds.

use piscem_rs::index::contig_table::{
    ContigTable, ContigTableBuilder, ContigTableDirectBuilder,
};
use salmon_model::gcbias::{gc_prefix, GcRank};

const MAX_REF_LEN: u64 = 3_000_000_000;
const NUM_REFS: u64 = 200_000;
const COUNTS: [u32; 4] = [3, 0, 4, 1];
// (unitig, transcript ID, position, forward). These require 51 packed bits,
// while allocating only eight entries. Unitig 1 deliberately has no entries.
const ENTRIES: [(u32, u32, u32, bool); 8] = [
    (0, 199_999, 2_999_999_999, true),
    (0, 65_536, 2_147_483_648, false),
    (0, 1, 33, true),
    (2, 0, 0, false),
    (2, 0, 31, true),
    (2, 0, 63, false),
    (2, 131_071, 1_073_741_824, true),
    (3, 199_998, 2_999_999_998, false),
];

fn check_table(table: &ContigTable) {
    assert_eq!(table.num_contigs(), COUNTS.len());
    assert_eq!(table.num_entries(), ENTRIES.len());
    assert_eq!(table.ref_len_bits(), 32);
    assert_eq!(table.num_ref_bits(), 18);
    let encoding = table.encoding();
    assert_eq!(encoding.entry_width, 51);

    for (unitig, &count) in COUNTS.iter().enumerate() {
        let span = table.contig_entries(unitig as u64);
        let expected: Vec<_> = ENTRIES
            .iter()
            .filter(|entry| entry.0 == unitig as u32)
            .collect();
        assert_eq!(span.len(), count as usize);
        let iterated: Vec<u64> = span.iter().collect();
        assert_eq!(iterated.len(), expected.len());
        for (index, &&(_, transcript, position, forward)) in expected.iter().enumerate() {
            // Compute the expected bit pattern independently of EntryEncoding.
            let packed = ((transcript as u64) << 33)
                | ((position as u64) << 1)
                | u64::from(forward);
            assert_eq!(span.get(index), packed, "packed lookup: unitig {unitig}, entry {index}");
            assert_eq!(iterated[index], packed, "packed iterator: unitig {unitig}, entry {index}");
            assert_eq!(encoding.transcript_id(packed), transcript);
            assert_eq!(encoding.pos(packed), position);
            assert_eq!(encoding.orientation(packed), forward);
        }
    }
    assert!(table.contig_entries(0).get(0) > u32::MAX as u64);
}

fn check_roundtrip(table: &ContigTable) {
    check_table(table);
    let mut bytes = Vec::new();
    table.save(&mut bytes).expect("serialize packed contig table");
    let mut reader = bytes.as_slice();
    let loaded = ContigTable::load(&mut reader).expect("deserialize packed contig table");
    assert!(reader.is_empty(), "contig table deserialization left unread bytes");
    check_table(&loaded);
}

fn check_contig_words() {
    let mut original = ContigTableBuilder::new(COUNTS.len(), MAX_REF_LEN, NUM_REFS);
    let mut direct = ContigTableDirectBuilder::new(&COUNTS, MAX_REF_LEN, NUM_REFS);
    for (unitig, transcript, position, forward) in ENTRIES {
        original.add_occurrence(unitig, transcript, position, forward);
        direct.add_occurrence(unitig, transcript, position, forward);
    }
    check_roundtrip(&original.build());
    check_roundtrip(&direct.build());
}

fn check_gc_words() -> usize {
    // Exercise partial/full 32- and 64-bit words, 512-bit Rank9 blocks, and
    // windows that begin or end on either side of those boundaries.
    let lengths = [1, 31, 32, 33, 63, 64, 65, 127, 511, 512, 513, 1024, 1091];
    let offsets = [0, 1, 30, 31, 32, 33, 62, 63, 64, 65, 510, 511, 512, 513, 1023];
    let patterns: [&[u8]; 3] = [b"GATcNTgACGgtCaN", b"ATNatn", b"GCgc"];
    let mut windows = 0;
    for pattern in patterns {
        for len in lengths {
            let sequence: Vec<u8> = (0..len).map(|index| pattern[index % pattern.len()]).collect();
            let rank = GcRank::new(&sequence);
            for offset in offsets.into_iter().filter(|&offset| offset <= len) {
                let remaining = len - offset;
                for window_len in [0, remaining.min(33), remaining.min(65), remaining] {
                    let slice = &sequence[offset..offset + window_len];
                    let view = rank.view(offset, window_len);
                    let dense = gc_prefix(slice);
                    assert_eq!(view.ref_len(), window_len);
                    let mut expected = 0_i64;
                    for (position, &base) in slice.iter().enumerate() {
                        if matches!(base, b'G' | b'g' | b'C' | b'c') {
                            expected += 1;
                        }
                        assert_eq!(dense[position] as i64, expected);
                        assert_eq!(
                            view.cum(position as i32), expected,
                            "GC rank: total {len}, offset {offset}, window {window_len}, position {position}",
                        );
                    }
                    windows += 1;
                }
            }
        }
    }
    windows
}

fn main() {
    #[cfg(target_os = "emscripten")]
    assert_eq!(usize::BITS, 32, "this regression targets the wasm32 port");
    check_contig_words();
    let windows = check_gc_words();
    println!("SALMON_PORT_CHECK_OK pointer_bits={} contig_entries={} gc_windows={}", usize::BITS, ENTRIES.len(), windows);
}
