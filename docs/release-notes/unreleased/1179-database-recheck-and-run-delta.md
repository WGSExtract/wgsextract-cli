## New

- Result manifests now record the public resources a run used and their exact
  releases (`resources`): ClinVar's release from its own header, CADD's SNV
  and indel tables, the report's gene list, each with its source terms; an
  unrecorded release stays unknown. `ref recheck` compares the recorded
  releases with the newest, from an answers file or, only with `--online`,
  NCBI's public ClinVar listing (no sample data is sent; nothing is downloaded
  or rerun). `report-delta` compares two reports (or any two manifests of one
  command and sample) by meaning: findings added, removed, reclassified or
  with changed review, conflicts, evidence, genotype, phase, confidence or
  ambiguity; changed gene interpretations; sections and runs that failed or
  didn't run; and changed releases, ignoring timestamps and ordering
  ([#1179, database recheck and run delta](https://github.com/WGSExtract/wgsextract-cli/issues/1179)).
