## New

- `vcf pharmcat --alignment` uses a local BAM or CRAM to fill absent definitions
  only where mapped reads support a reference genotype. It overrides companion
  gVCF discovery and cannot be combined with `--absent-to-ref`.
  The new experimental `wgsextract.result/1:details.pharmcat_reference_evidence` summary
  and `role:pharmcat_reference_evidence` report record the source and thresholds,
  how many absent definitions were filled, and how many stayed missing. The
  report also lists the missing positions.
