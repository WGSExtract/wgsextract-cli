## New

- `typing str --loci HTT,FMR1,AR` sizes only the named catalog loci.
  `--format parquet` writes a typed repeat table, and the result JSON
  includes numeric allele sizes, confidence intervals and read support.
  The personal report now summarizes flagged, unconfirmed and normal
  repeat calls ([#1286, structured repeat sizing](https://github.com/WGSExtract/wgsextract-cli/issues/1286)).
