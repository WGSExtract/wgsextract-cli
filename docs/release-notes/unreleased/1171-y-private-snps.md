## New

- `lineage y-private` lists candidate private (novel) Y SNPs from a BAM, CRAM
  or single-sample VCF on GRCh38 or T2T: called chrY SNVs inside the bundled
  Poz callable mask whose alleles are in no installed marker set (the YBrowse
  snapshot on GRCh38, YFull YTree positions on both), passing strict filters
  (ALT fraction ≥ 0.9, ≥ 10 ALT reads, QUAL ≥ 30). The report and result
  manifest state the marker sets, mask and filters, and warn when there are
  unexpectedly many candidates. They are candidates to confirm, not proven
  private SNPs
  ([#1171, private Y SNP report](https://github.com/WGSExtract/wgsextract-cli/issues/1171)).
