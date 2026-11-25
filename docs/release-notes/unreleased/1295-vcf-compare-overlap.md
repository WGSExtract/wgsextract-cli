## Fixes

- `vcf compare` no longer treats a companion gVCF reference block as proof of
  absence when a primary ALT, filtered record, or no-call overlaps the assessed
  span ([#1295, VCF comparison](https://github.com/WGSExtract/wgsextract-cli/issues/1295)).
