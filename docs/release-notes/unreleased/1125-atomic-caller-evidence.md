## Fixes

- When `vcf clair3`, `vcf gatk`, or `vcf deepvariant --gpu` requests a gVCF,
  the callset and its indexes are published together. A failed gVCF step
  leaves any previous callset intact
  ([#1125, streaming caller intermediates](https://github.com/WGSExtract/wgsextract-cli/issues/1125)).
