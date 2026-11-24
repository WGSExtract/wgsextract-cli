## Fixes

- gnomAD 4.1 frequency tables now retain the maximum ancestry-group frequency
  and homozygote count for each alternate allele. Rebuild older tables with
  `ref gnomad --release 4.1 --download` to get one row per passing alternate
  allele, even if the old table already has all the columns
  ([#1419, gnomAD joint frequencies](https://github.com/WGSExtract/wgsextract-cli/issues/1419)).
- `ref gnomad` needs `--download` before fetching either release.
