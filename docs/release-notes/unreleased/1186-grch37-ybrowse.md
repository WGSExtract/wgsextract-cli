## New

- `extract ydna-vcf --ybrowse` (and `extract region --target y --ybrowse`)
  now works on GRCh37 alignments, with the same yBrowse IDs and HG/ISOGG/AA
  annotations as on GRCh38. Run `ref ybrowse --install --grch37 --download`
  once: it lifts the installed GRCh38 yBrowse snapshot to GRCh37 with
  Ensembl's pinned GRCh38->GRCh37 chain (fetched only with `--download`; or
  give one with `--chain`) and checks every allele against your GRCh37 FASTA.
  Sites the chain can't place exactly once, or whose alleles don't match
  GRCh37, are dropped and counted in the VCF header. YBrowse's own GRCh37
  file isn't used: its positions are GRCh38's. T2T stays unsupported
  ([#1186, GRCh37 targeted Y VCF](https://github.com/WGSExtract/wgsextract-cli/issues/1186)).
