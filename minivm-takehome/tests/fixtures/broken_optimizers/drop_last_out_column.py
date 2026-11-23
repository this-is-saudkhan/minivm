#!/usr/bin/env python3
"""A deliberately broken 'optimizer': silently drops the last OUT operand.
Used to prove evaluate actually catches an ncols-changing defect."""
import sys

with open(sys.argv[1]) as f:
    text = f.read()
lines = text.splitlines()
for i, line in enumerate(lines):
    if line.strip().upper().startswith("OUT"):
        parts = line.split(",")
        if len(parts) > 1:
            lines[i] = ",".join(parts[:-1])
print("\n".join(lines))
