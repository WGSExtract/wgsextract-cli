<!-- Notes for the next release. PRs add user-facing entries here as they
land; the release PR renames this file to vX.Y.Z.md (see docs/releasing.md). -->

## New

- `qc roh` finds runs of homozygosity in a VCF with `bcftools roh` and the
  population allele frequencies you give it (`--af-file`: a gnomAD or 1000
  Genomes sites VCF, or a bcftools AF table, on the sample's build). It
  reports runs, marker evidence, F_ROH over the declared scope only, and the
  frequency file's release and digest, and flags a long run over an
  imprinted region (15q11-q13, 11p15.5, 14q32.2, GNAS and others) for review
  as possible isodisomy, which is not a diagnosis. It never downloads
  anything; see [Runs of homozygosity](../roh.md)
  ([#1174, ROH and isodisomy flags](https://github.com/WGSExtract/wgsextract-cli/issues/1174)).
- [Migrating from WGS Extract v4](docs/migrating-from-v4.md) maps each v44.2
  button to its current desktop page and command, with changed defaults,
  features not yet equivalent and settings/reference precautions. NOTICE now
  credits WGS Extract's authors and marks the CLI's microarray export and
  liftover as modified GPL ports of v4 code. A differential corpus records
  parity and intentional differences against v44.2 on synthetic and public
  HG00096 data; its public inputs download only with `--download`
  ([#1187, v4 migration guide and corpus](https://github.com/WGSExtract/wgsextract-cli/issues/1187)).
- `extract ydna-bam --poz` and `extract region --target y --poz` now work on
  T2T-CHM13v2.0 inputs, using the Poz chrY regions lifted from GRCh38 with
  the UCSC `hg38ToHs1` chain (~11.6 Mb, bundled; no download)
  ([#1170, T2T Poz mask](https://github.com/WGSExtract/wgsextract-cli/issues/1170)).

## Fixes

- An interrupted reference download no longer resumes onto a different
  version of the file. Resumes were tied to the URL alone, so a file replaced
  at the same URL by one of the same size was installed as the old file's start
  and the new file's end. Both one-connection and segmented downloads now
  record the server's ETag or Last-Modified date, resume only that version and
  otherwise start over. A partial left by an earlier release, or from a server
  that names no version and without a pinned checksum, downloads again from
  the start
  ([#1225, resumed downloads mixing file versions](https://github.com/WGSExtract/wgsextract-cli/issues/1225)).
- `ref cadd` keeps a complete CADD score table whose index download failed or
  was cancelled: the next run checks the table against its published MD5 and
  fetches only the missing index, instead of downloading the ~85 GB SNV table
  again. A table in place that fails the check is reported, not removed.
  `ref revel` now downloads over https and checks each file against a pinned
  SHA-256
  ([#1245, CADD index retry and REVEL integrity](https://github.com/WGSExtract/wgsextract-cli/issues/1245)).
- `lineage y-str` now calls DYS448, DYS459 and DYS589 on T2T when reads
  support a call. DYS589's non-repeat boundary variation no longer appears
  as a fractional STR allele. DYS425 is left blank on all builds because
  short reads cannot reliably distinguish its assay target from paralogs
  ([#1155, missing Y-STRs](https://github.com/WGSExtract/wgsextract-cli/issues/1155)).
- `lineage y-haplogroup --tree ftdna` and `--tree isogg` correct independently
  verified ancestral/derived reversals using a pinned, separately licensed
  YBrowse polarity module. This fixes inverted backbone evidence and wrong
  haplogroup calls, including reverse-strand T2T markers. Ancient panels are
  filtered after correction. Version 2 requires compatible source branches
  and reference-lineage agreement; name-only matching in the superseded
  version 1 incorrectly flipped valid sister-branch markers. Use version 2,
  not version 1. First use requires `--download`; custom marker
  panels are unchanged ([#1168, Y marker polarity](https://github.com/WGSExtract/wgsextract-cli/issues/1168)).
- `report-issue` now reads sample names from a BAM/CRAM or VCF header when
  samtools or bcftools is installed only in the WGS Extract Pixi environment
  rather than on `PATH`, so it redacts them from the report. It previously
  could not start the tools and left those names in
  ([#1314, report sample redaction](https://github.com/WGSExtract/wgsextract-cli/issues/1314)).
- `score pgs` now finds bcftools the same way its dependency check does, so
  it scores when bcftools is installed only in the WGS Extract Pixi
  environment rather than on `PATH`. It previously failed with "No such file
  or directory: 'bcftools'"
  ([#1233, PGS bcftools probes](https://github.com/WGSExtract/wgsextract-cli/issues/1233)).
- The Python API records a command's input files under the same result
  manifest roles and params as the CLI. A VCF given as `vcf_input` (for
  `vcf` operations on a VCF and `qc vcf`) is role `vcf_input` with
  `provenance.params.vcf_input` set, not role `input`; a `vcf trio` proband
  is role `proband`; and `r1`/`r2` reads are `fastq_r1`/`fastq_r2`
  ([#1367, API input roles](https://github.com/WGSExtract/wgsextract-cli/issues/1367)).
