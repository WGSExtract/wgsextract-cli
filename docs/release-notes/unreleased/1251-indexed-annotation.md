## Fixes

- Reference-normalized annotation no longer writes a full text VCF copy of
  ClinVar, gnomAD, CADD indel, or other whole-resource inputs to scratch.
  The first run streams normalization and still matches against the compressed,
  indexed resource.
