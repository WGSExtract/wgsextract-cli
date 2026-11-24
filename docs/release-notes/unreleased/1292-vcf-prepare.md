## New

- `vcf prepare` repairs VCFs from sequencing services that other tools
  reject: it recompresses plain gzip, text and BCF to BGZF, sorts, declares
  missing contigs and IDs, renames and sizes contigs to match `--ref`,
  rebuilds the index and, with `--normalize`, splits multiallelic records
  and left-aligns indels. Every repair is reported with its counts, and no
  record is dropped ([#1292, VCF preparation](https://github.com/WGSExtract/wgsextract-cli/issues/1292)).
