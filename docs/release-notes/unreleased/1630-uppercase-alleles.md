## Fixes

- `vcf call` writes uppercase REF and ALT sequences even on soft-masked references.
  Panel watches, ACMG annotations, variant tables and polygenic scores now match
  lowercase sequence alleles in input VCFs, and `qc relate` accepts lowercase
  alleles in custom sites files
  ([#1630, lowercase VCF alleles](https://github.com/WGSExtract/wgsextract-cli/issues/1630)).
