#!/usr/bin/env python3
"""A deliberately broken 'optimizer': if asked to optimize q0001 (the
"o_amt above 750.00" program), lowers that threshold to 740.00 -- a filter
that only disagrees with the original on specific data (any order with
o_amt in (740, 750]). Used to prove evaluate's fuzzer, not just its fixed
regression check, can find a data-dependent defect; a naive checker that
only replays examples/data.json would miss this entirely, since none of
that fixed data happens to fall in the affected range."""
import sys

with open(sys.argv[1]) as f:
    lines = f.readlines()
for i, l in enumerate(lines):
    if l.strip().upper().startswith("GT") and "750.00" in l:
        lines[i] = l.replace("750.00", "740.00")
sys.stdout.write("".join(lines))
