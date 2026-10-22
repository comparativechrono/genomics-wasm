"""Small, deterministic fixtures for native -> wasm32 portability regressions.

The BGZF stream represents 4 GiB + 64 KiB of zeros followed by TAILMARKER\n,
but occupies less than 7 MiB. Its GZI index permits a four-byte seek without
inflating or allocating the zero prefix. For either HTSlib version:

    bgzip -b 4295032834 -s 4 logical-offset.bgz

must emit exactly b'ILMA'. A 32-bit atol/long would seek into the zero prefix.

The SAM contains two single-end duplicate groups. Both have x coordinates
above INT32_MAX; the first pair is 252 units apart and the second 50 units.
For either SAMtools version:

    samtools markdup --no-PG -s -d 100 -O SAM optical-width.sam marked.sam

must report DUPLICATE SINGLE=2, DUPLICATE SINGLE OPTICAL=1. The record with
x=2147483900 must receive flag 1024 and dt:Z:LB; x=2147483698 receives flag
1024 and dt:Z:SQ. The other two records remain unmarked. Qualities select a
unique representative, so this truth does not depend on sorting ties.

sort-disordered.sam places the unplaced read first and interleaves two contigs
and decreasing positions. Two paired unmapped reads retain their reference
coordinates, as ExpansionHunter BAMlets do. Sorting must reorder every record
by reference then position, retaining those coordinates and placing the fully
unplaced record last. All positions are unique, so there is no tie-order
assumption. A stubbed pthread callback must not pass this regression simply by
rewriting the header to SO:coordinate.
"""
from __future__ import annotations

import struct
import zlib


ZERO_BLOCK_SIZE = 1 << 16
ZERO_BLOCK_COUNT = (1 << 16) + 1
LOGICAL_SEEK_OFFSET = ZERO_BLOCK_SIZE * ZERO_BLOCK_COUNT + 2
LOGICAL_SEEK_OUTPUT = b"ILMA"

# Fixed raw DEFLATE bytes for 65536 zero bytes, independent of the generator's
# installed compressor/version. zlib is used only for CRC32 and a local check.
_ZERO_DEFLATE = bytes.fromhex(
    "edc101010000008090feafee080a000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000000"
    "000000000000000000000000006a"
)


def _bgzf(payload: bytes, raw_deflate: bytes) -> bytes:
    """Wrap one raw DEFLATE block in the specified BGZF gzip envelope."""
    total = 18 + len(raw_deflate) + 8
    assert total <= 65536
    header = bytes.fromhex("1f8b08040000000000ff060042430200")
    return (header + struct.pack("<H", total - 1) + raw_deflate
            + struct.pack("<II", zlib.crc32(payload), len(payload)))


def synthetic() -> dict[str, bytes]:
    zeros = bytes(ZERO_BLOCK_SIZE)
    assert zlib.decompress(_ZERO_DEFLATE, -15) == zeros
    block = _bgzf(zeros, _ZERO_DEFLATE)
    tail = b"TAILMARKER\n"
    # A final uncompressed DEFLATE block is completely deterministic.
    tail_deflate = b"\x01" + struct.pack("<HH", len(tail), 65535 - len(tail)) + tail
    eof = bytes.fromhex("1f8b08040000000000ff0600424302001b0003000000000000000000")
    bgzf = block * ZERO_BLOCK_COUNT + _bgzf(tail, tail_deflate) + eof
    # GZI omits the implicit (0, 0); include every later data-block start,
    # including the marker block. Both coordinates are uint64 little-endian.
    gzi = struct.pack("<Q", ZERO_BLOCK_COUNT) + b"".join(
        struct.pack("<QQ", i * len(block), i * ZERO_BLOCK_SIZE)
        for i in range(1, ZERO_BLOCK_COUNT + 1)
    )
    lines = ["@HD\tVN:1.6\tSO:coordinate", "@SQ\tSN:chr1\tLN:1000"]
    for pos, x, quality in [(101, 2147483648, "J"), (101, 2147483900, "I"),
                            (201, 2147483648, "J"), (201, 2147483698, "I")]:
        # Add a different tile for the second group to keep qnames unique.
        tile = 1101 if pos == 101 else 1102
        name = f"fixture:1:FLOW:1:{tile}:{x}:100"
        fields = [name, "0", "chr1", str(pos), "60", "10M", "*", "0", "0",
                  "ACGTACGTAC", quality * 10]
        lines.append("\t".join(fields))
    disordered = ["@HD\tVN:1.6\tSO:unsorted", "@SQ\tSN:chr1\tLN:1000",
                  "@SQ\tSN:chr2\tLN:1000"]
    for name, flag, chrom, pos in [
            ("unplaced", 4, "*", 0),
            ("chr1-unmapped-high", 77, "chr1", 401),
            ("chr2-mapped", 0, "chr2", 11),
            ("chr1-reverse", 16, "chr1", 301),
            ("chr1-unmapped-low", 189, "chr1", 201),
            ("chr1-forward", 0, "chr1", 101),
            ("chr2-unmapped", 4, "chr2", 21)]:
        cigar, mapq = ("*", "0") if flag & 4 else ("10M", "60")
        disordered.append("\t".join([name, str(flag), chrom, str(pos), mapq,
                                       cigar, "*", "0", "0", "ACGTACGTAC", "I" * 10]))
    return {"logical-offset.bgz": bgzf, "logical-offset.bgz.gzi": gzi,
            "optical-width.sam": ("\n".join(lines) + "\n").encode(),
            "sort-disordered.sam": ("\n".join(disordered) + "\n").encode()}
