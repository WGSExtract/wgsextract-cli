## New

- `info gene --gene SYMBOL` shows one gene from local files: its span, its
  RefSeq transcripts and exons, your VCF's carried variants in it (consequence,
  ClinVar, gnomAD, research-only ACMG, each tagged rare-and-damaging, ClinVar
  P/LP or ACMG P/LP) and BAM/CRAM depth over each exon against a callable
  threshold. Missing data is a notice naming the command that makes it;
  `--export` writes the rows as TSV ([#1166, offline gene page](https://github.com/WGSExtract/wgsextract-cli/issues/1166)).
- `ref gene-map` also installs UCSC refGene's RefSeq transcript and exon
  models as an indexed `transcripts_<build>.bed.gz`; run it again to add them
  to an existing library ([#1166](https://github.com/WGSExtract/wgsextract-cli/issues/1166)).

## Desktop app

- A **Gene page**: type a gene symbol to see its transcripts and exons, your
  variants in it with filters and TSV export, and its exon coverage, all read
  offline, then open it in the genome viewer. The viewer shows the installed
  RefSeq transcripts as a track ([#1166, offline gene page](https://github.com/WGSExtract/wgsextract-cli/issues/1166)).
