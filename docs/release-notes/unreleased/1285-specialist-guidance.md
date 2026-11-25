## Changed

- `typing target-cnv` and `vcf callability` now share specialist caller
  recommendations for known short-read limitations. For example, masked
  PMS2 regions point to long-read `typing paraphase` without being
  reported as callable ([#1285, paralog typing and target copy number](https://github.com/WGSExtract/wgsextract-cli/issues/1285)).
