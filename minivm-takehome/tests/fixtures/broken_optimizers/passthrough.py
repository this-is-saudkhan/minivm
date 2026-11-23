#!/usr/bin/env python3
"""The trivial (correct, if useless) 'optimizer': emits the input unchanged.
Used as a negative control -- evaluate must accept this (it's a valid,
if not very good, optimizer) rather than report a defect."""
import sys

with open(sys.argv[1]) as f:
    sys.stdout.write(f.read())
