## New

- `vcf chain-annotate --dry-run` prints the plan and records
  `details.steps` and `details.dry_run` in a separate plan manifest without
  changing the annotation outputs or resume journal.

## Fixes

- `vcf chain-annotate` rejects repeated annotators and unknown or
  out-of-bounds `--region` values. It clips BED intervals to known
  contig lengths and warns with `bed_contigs_not_in_vcf` for omitted
  contigs and `no_variants` for a header-only result. The manifest reports
  `region_intervals` and `region_bases` for the merged scope in `metrics`
  and `provenance.params` (`region_bases` is null for open-ended regions
  on contigs of unknown length). For BED selection, `provenance.params` records
  `bed` and `bed_sha256`; the digest is also in `details.scope`.
  ([#1475, chain annotation validation and reporting](https://github.com/WGSExtract/wgsextract-cli/issues/1475))
