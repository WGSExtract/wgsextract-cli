## New

- Research-only `vcf acmg` uses RepeatMasker for BP3 and gnomAD region flags to
  withhold unreliable population evidence. The VCF and run report identify
  evidence that couldn't be evaluated
  ([#1458, ACMG region tracks](https://github.com/WGSExtract/wgsextract-cli/issues/1458)).

## Changed

- `ref acmg-data` stays offline unless you pass `--download`. An offline
  `--dry-run` still lists the sources and their terms.
