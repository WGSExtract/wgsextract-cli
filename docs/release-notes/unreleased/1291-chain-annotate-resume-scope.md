## New

- `vcf chain-annotate` takes `--threads`, passed to every annotation step;
  `--region` or `--bed` to annotate only the calls there (contigs may be
  named with or without `chr`); and `--format tsv|parquet` to also write the
  `vcf table` of the result. Completed steps are recorded in
  `chain_annotated.vcf.gz.steps.json`, so a rerun after a failed step resumes
  there, and `--force`, `--force-step` and `--dry-run` work as for
  `analyze comprehensive` ([#1291, chain-annotate threads, regions and resume](https://github.com/WGSExtract/wgsextract-cli/issues/1291)).
