## Changed

- `vcf impute` releases a chromosome's GLIMPSE2 chunk files after successful
  ligation instead of keeping them until the full run ends, reducing peak
  temporary disk use ([#1125, disposable genome handoffs](https://github.com/WGSExtract/wgsextract-cli/issues/1125)).
