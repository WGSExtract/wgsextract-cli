## New

- New gnomAD v4.1 tables retain ancestry-group maximum FAF95. `vcf gnomad`
  annotates it and `vcf table` exports it as `gnomad_faf95`. Existing tables
  still work; to add FAF95, run `wgsextract ref gnomad --release 4.1 --download`
  (about 900 GB). AF filters and ACMG defaults are unchanged
  ([#1623, gnomAD FAF95](https://github.com/WGSExtract/wgsextract-cli/issues/1623)).
