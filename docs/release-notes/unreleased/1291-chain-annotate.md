## New

- `vcf chain-annotate` accepts `--threads`, targets a region or BED panel,
  and resumes after a failed annotation step. Add `--to-table` to write
  a TSV (or Parquet) and SQLite database in the same run
  ([#1291, targeted and resumable annotation chains](https://github.com/WGSExtract/wgsextract-cli/issues/1291)).
