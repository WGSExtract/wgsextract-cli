## New

- `vcf compare --atomize` compares primitive alleles against a local
  reference, so an MNP can match separate SNVs. For two-callset concordance,
  `--emit-partition-vcfs` writes indexed shared and one-sided allele VCFs.
  Neither option changes the default comparison
  ([#1582, VCF atomization and partitions](https://github.com/WGSExtract/wgsextract-cli/issues/1582)).
