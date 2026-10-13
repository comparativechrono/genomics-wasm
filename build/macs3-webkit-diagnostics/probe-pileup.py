"""Scratch-only deterministic PileupV2 localization; not production validation."""
import hashlib
import json
import os
import sys
import time

print("PILEUP_PROBE import start", flush=True)
import numpy as np
from MACS3.IO.Parser import BEDParser
from MACS3.Signal.PileupV2 import naive_quick_pileup, pileup_from_PN_shifted
print("PILEUP_PROBE import done", flush=True)

args = sys.argv[1:]
if args and args[0] == "--probe":
    args.pop(0)
selected = args[0] if args else "all"
limit = int(args[1]) if len(args) > 1 else 0
chip = os.environ.get("MACS3_PROBE_CHIP", "chip.bed.gz")


def describe(array):
    return {"dtype": str(array.dtype), "shape": list(array.shape),
            "sha256": hashlib.sha256(array.tobytes()).hexdigest(),
            "minimum": float(array.min()) if array.size else None,
            "maximum": float(array.max()) if array.size else None}


print("PILEUP_PROBE parse start", flush=True)
track = BEDParser(chip).build_fwtrack()
track.finalize()
raw = [a.copy() for a in track.get_locations_by_chr(b"chr22")]
print("PILEUP_PROBE parse done", track.total, json.dumps([describe(a) for a in raw]), flush=True)
track.filter_dup(1)
dedup = [a.copy() for a in track.get_locations_by_chr(b"chr22")]
print("PILEUP_PROBE dedup done", track.total, json.dumps([describe(a) for a in dedup]), flush=True)
if limit:
    raw = [a[:limit].copy() for a in raw]
    dedup = [a[:limit].copy() for a in dedup]

jobs = [
    ("naive-raw-plus", lambda: naive_quick_pileup(raw[0], 300)),
    ("naive-raw-minus", lambda: naive_quick_pileup(raw[1], 300)),
    ("naive-dedup-plus", lambda: naive_quick_pileup(dedup[0], 300)),
    ("naive-dedup-minus", lambda: naive_quick_pileup(dedup[1], 300)),
    ("shift-zero", lambda: pileup_from_PN_shifted(dedup[0], dedup[1], 0, 100, 2147483647)),
    ("shift-negative", lambda: pileup_from_PN_shifted(dedup[0], dedup[1], 50, 50, 2147483647)),
]
if selected != "all" and selected not in [name for name, _ in jobs]:
    raise ValueError("Unknown probe: " + selected)
for name, operation in jobs:
    if selected != "all" and selected != name:
        continue
    print("PILEUP_PROBE start", name, flush=True)
    started = time.monotonic()
    result = operation()
    print("PILEUP_PROBE done", name, "seconds", time.monotonic() - started,
          json.dumps([describe(a) for a in result]), flush=True)
print("PILEUP_PROBE all requested operations done", flush=True)
