# A port-level integer-width check using the unchanged upstream statistics type.
# These are arithmetic bounds, not simulated sequencing observations.
import depthstat

static:
  doAssert sizeof(int) == 8
  doAssert sizeof(pointer) == 8

let a = depth_stat(cum_length: 2_147_483_000, cum_depth: 4_294_967_000'u64,
                   min_depth: 0, max_depth: 17)
let b = depth_stat(cum_length: 2_147_483_000, cum_depth: 4_294_967_000'u64,
                   min_depth: 1, max_depth: 23)
let combined = a + b
doAssert combined.cum_length == 4_294_966_000
doAssert combined.cum_depth == 8_589_934_000'u64
doAssert combined.min_depth == 0 and combined.max_depth == 23
echo "MOSDEPTH_WIDTH_OK 4294966000 8589934000"
