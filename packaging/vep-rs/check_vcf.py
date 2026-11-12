"""Fail unless vep-rs annotated (nearly) every record of its golden set."""

import sys

with open(sys.argv[1]) as f:
    annotated = sum(1 for line in f if not line.startswith("#") and "CSQ=" in line)
print(annotated, "annotated records")
sys.exit(0 if annotated >= 990 else 1)
