## New

- Research-only `vcf acmg` uses RepeatMasker for BP3 and gnomAD region flags to
  withhold population evidence at flagged sites under fastVEP's default rule.
  This can shift benign calls to VUS: 8,669 alleles (17.0%) did so on HG002
  chr22, with no new pathogenic or likely pathogenic calls.
  The VCF marks calls at flagged positions and the run report identifies
  evidence that couldn't be evaluated
  ([#1458, ACMG region tracks](https://github.com/WGSExtract/wgsextract-cli/issues/1458)).

## Changed

- `ref acmg-data` stays offline unless you pass `--download`. An offline
  `--dry-run` still lists the sources and their terms.
