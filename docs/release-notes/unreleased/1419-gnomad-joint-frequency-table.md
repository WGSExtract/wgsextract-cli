## Fixes

- gnomAD 4.1 frequency tables now retain the maximum ancestry-group frequency
  and homozygote count for each alternate allele. Old tables still provide
  overall allele frequency; rebuild with `ref gnomad --release 4.1 --download`
  to add the new fields ([#1419, gnomAD joint frequencies](https://github.com/WGSExtract/wgsextract-cli/issues/1419)).
- `ref gnomad` needs `--download` before fetching either release.
