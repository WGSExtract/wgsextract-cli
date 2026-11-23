## New

- `vcf filter` and `vcf table` can keep variants from multiple genes, a gene
  list, a preset, or a BED panel, all offline. Combine them in one run and
  optionally narrow the panel with `--region`. The result manifest records the
  intervals used and the BED's SHA-256
  ([#1290, VCF panel slicing](https://github.com/WGSExtract/wgsextract-cli/issues/1290)).
