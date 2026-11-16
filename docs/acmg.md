# Automated ACMG classification (research only)

**Research use only; not a diagnosis.** `vcf acmg` adds automated ACMG/AMP
classes (P, LP, VUS, LB, B) to a VCF annotated by `vep run`. They come from
[fastVEP](https://github.com/Huang-lab/fastVEP)'s classifier and public
databases only. It has no clinical, family or functional evidence (PS3/BS3,
PS4, PP1, PP4), BP3 needs an optional RepeatMasker track, and a VUS can mean
that nothing was checked. Don't act on a class without a clinical genetics professional.

The command is off unless you ask for it: it refuses to run without
`--research-acmg` (`research_acmg` in the API and server), and nothing else
runs it for you.

```bash
wgsextract ref acmg-data --dry-run   # the plan, terms and sizes
wgsextract ref acmg-data             # download and build, once
wgsextract vep run --input sample.vcf.gz --engine vep-rs
wgsextract vcf acmg --vcf-input sample_vep.vcf.gz --research-acmg
```

## What it writes

- `acmg_annotated.vcf.gz` (+ `.tbi`): the input VCF, vep-rs consequences
  unchanged, with three INFO fields per ALT allele: `ACMG_CLASS`,
  `ACMG_CRITERIA` (the criteria met, joined by `&`) and `ACMG_TRANSCRIPT`.
  The header carries the disclaimer in `##WGSE_ACMG`. Classifying an
  `acmg_annotated.vcf.gz` again replaces its earlier classes.
- `acmg_summary.json`: the disclaimer, the fastVEP version, the databases
  (files, sources, assemblies, sizes, dates), the optional sources not loaded,
  the criteria never evaluated, and the counts of
  classes, criteria and transcript choices.
- The result manifest lists both (roles `annotated_vcf` and `acmg_summary`),
  the class counts as metrics (`acmg_p`, `acmg_lp`, ...), and the warning
  `acmg_research_only`.

## How it works

vep-rs stays the consequence engine. fastVEP reads the same records with its
own transcript models (the Ensembl GFF3), the reference FASTA and the
databases, with `--acmg --pick-allele --pick-order
mane_select,canonical,biotype,rank`. Each allele's class is the one fastVEP
gives on the transcript [`vcf table`](variant-table.md) picks; when fastVEP
has no row for that transcript, its own pick is used and
`transcript_choice.other_transcript` counts it. fastVEP's own consequences
and annotations are discarded.

fastVEP doesn't fail when a database is missing: the criteria it drives
don't fire and the variant reads as VUS. `vcf acmg` refuses instead, before
reading any record, when the databases lack one of the eight sources below,
when a database isn't built for GRCh38, when fastVEP skips a database file,
or when the gnomAD database holds nothing of an autosome, X or Y the input
has records on. Records on other contigs gnomAD holds nothing of (chrM, which
gnomAD v4.1 lacks, and unplaced, alt or decoy contigs) are left unclassified,
with the warning `acmg_contigs_not_classified`, and listed in the summary's
`contigs_not_classified`.

## Requirements

- GRCh38 only, with its reference FASTA (`--ref` or the reference library).
- fastVEP 0.5: `pixi install -e fastvep` on x86-64 Linux and Intel macOS. On
  Linux ARM64 and Apple Silicon, put the
  [fastVEP 0.5 release](https://github.com/Huang-lab/fastVEP/releases) for
  your system on PATH. There is no Windows build; the command refuses there.
- An Ensembl GRCh38 GFF3 of the same release as the vep-rs cache, such as
  `Homo_sapiens.GRCh38.116.gff3.gz` from
  <https://ftp.ensembl.org/pub/release-116/gff3/homo_sapiens/>. fastVEP
  writes a transcript cache beside it on first use.
- The folder of fastVEP databases and the GFF3 that `wgsextract ref
  acmg-data` builds (below). `vcf acmg` reads them from the reference library
  unless `--sa-dir` and `--gff3` (or `acmg_sa_dir` and `acmg_gff3` in
  config.toml) name others. A folder built with `fastvep sa-build` yourself
  (see fastVEP's `docs/ACMG_SETUP.md`) works too, one database per source, all
  with `--assembly GRCh38`. A RepeatMasker track (`--source custom_bed --name
  repeatmasker`, an `.osi`) is optional and lets fastVEP evaluate BP3.

## Building the databases: `ref acmg-data`

`wgsextract ref acmg-data` downloads the eight sources and the Ensembl GFF3
from their publishers, checks them, shows their terms and builds the
databases with `fastvep sa-build`:

- The GFF3 goes in `ref/acmg/` in the library, the downloads in
  `ref/acmg/sources/` and the databases in `ref/acmg/sa/`, or in `--sa-dir`
  (`acmg_sa_dir`). `--ensembl-release` (default 116) should match your vep-rs
  cache.
- Each download is checked against the checksum its publisher gives: the
  `.md5` files of NCBI, UCSC's `md5sum.txt`, the MD5 Google Cloud Storage gives
  for gnomAD and Ensembl's `CHECKSUMS`. ClinGen and REVEL publish none. A file
  that fails is removed, and the run says so. `acmg_data.json` beside the
  databases records each file's URL, release, size, SHA-256 and check (the
  SpliceAI VCF's too, and where it came from: Ensembl, `--spliceai-file` or a
  file already in the library), and each database's record count and
  SHA-256.
- gnomAD's joint sites (877 GB) are streamed, one chromosome at a time, never
  stored: bcftools keeps the INFO fields fastVEP reads (AC, AN, AF and
  nhomalt, faf95, the population AFs and each callset's filters), filtered
  sites included. A dropped connection resumes at the byte it stopped at.
- SpliceAI is the library's `spliceai_hg38.vcf.gz` (`ref spliceai`, from
  Ensembl), downloaded when missing, or `--spliceai-file`: Illumina's own
  files need a BaseSpace login and licence acceptance, so download them
  yourself and pass the file. It must be GRCh38 and tabix-indexed.
- `--chromosomes` (such as `chr22`, or `chr1-22,chrX`; default all) limits
  gnomAD, SpliceAI, REVEL and phyloP; the others are genome-wide. `vcf acmg`
  refuses inputs with records on a chromosome gnomAD wasn't built for.
- It's resumable: a rerun builds only what is missing, resuming interrupted
  downloads (a failed run keeps them), and a source that failed doesn't stop
  the others. Ctrl-C stops the builds running and starts no more. To take a new
  ClinVar or ClinGen release, delete its database (`clinvar.osa2`,
  `clinvar_protein.oga`, `omim.oga`) and its download, and rerun.
- `--dry-run` prints the plan, the terms and the sizes, and downloads nothing;
  the build refuses to start without the free space it needs on each drive
  it writes (the library's, and `--sa-dir`'s when that is elsewhere).
  `--threads` sets how many chromosomes build at a time (default 4); REVEL is
  cut into per-chromosome files only as fast as they are built.

| Source | Download | Database (chr22; whole genome, estimated) | Terms |
|---|---|---|---|
| Ensembl GRCh38 GFF3 (release 116) | 108 MB | (fastVEP caches 151 MB beside it) | no restrictions |
| ClinVar VCF | 202 MB | `clinvar.osa2`, 43 MB | NCBI, no restrictions |
| ClinVar `variant_summary.txt.gz` | 458 MB | `clinvar_protein.oga`, 20 MB | NCBI, no restrictions |
| gnomAD v4.1 joint sites | streams 877 GB (chr22: 15.6 GB) | `gnomad_chr22.osa2`, 211 MB; about 12 GB | CC0 |
| gnomAD v4.1 constraint metrics | 96 MB | `gnomad_genes.oga`, 1.5 MB | CC0 |
| ClinGen gene-disease validity | 1.1 MB | `omim.oga`, 0.5 MB | CC0; credit ClinGen and give the access date |
| REVEL v1.3 | 667 MB | `revel_chr22.osa2`, 4 MB; about 0.4 GB | free for non-commercial use; others contact the authors |
| SpliceAI (Illumina, MANE SNVs via Ensembl) | 28.9 GB | `spliceai_chr22.osa2`, 228 MB; about 15 GB | academic and not-for-profit use only |
| UCSC phyloP100way | 5.5 GB (chr22: 69 MB) | `phylop_chr22.osa2`, 75 MB; about 6 GB | freely available for public use |

You download these yourself and their terms apply to you; WGS Extract never
mirrors them or the databases built from them, and you shouldn't share the
databases either. The run shows each source's terms and warns
`acmg_data_terms` about REVEL's and SpliceAI's.

Measured with `--chromosomes chr22` on a 6-core Linux machine, two at a
time: 89 minutes and 2.6 GB of memory at most. Most of it was the 28.9 GB
SpliceAI download (54 minutes at 9 MB/s) and gnomAD's chromosome 22 (31
minutes): bcftools reads the joint release at about 8.5 MB/s per stream,
held back by parsing its 11 KB records, and more bcftools threads don't help.
A whole genome streams 877 GB, about 29 hours of one core, or 7 at 4
chromosomes at a time where the network keeps up; `--dry-run` says it needs
about 80 GB (75 GiB) free: 47 GiB for the databases and their staging, 27 GiB
for the SpliceAI download in the library's `ref/` and 1.4 GiB for the other
downloads.

## Validation

ClinVar 2026-11-08, chromosome 22 variants with 2+ review stars, with these
eight sources (gnomAD, SpliceAI, REVEL and phyloP built for chromosome 22):

| ClinVar | Same direction | VUS | Opposite direction |
|---|---|---|---|
| P/LP (1,960) | 75.4% P/LP | 24.5% | 2 |
| B/LB (8,231) | 77.2% B/LB | 22.8% | 1 |

The same check with the databases `ref acmg-data` built for chromosome 22
(REVEL v1.3 rather than ANNOVAR's REVEL, and the whole of gnomAD's chromosome
22 rather than the test's sites) gives 75.4% and 77.2%, with 1 opposite call
each way, and the same classes for HG002.

ClinVar itself feeds some criteria (the PM2 backstop, PM5, PM1, PS1), so these
numbers are an optimistic upper bound. GIAB HG002's chromosome 22 (50,284
records) gets no P/LP call; 0.7% of its genic alleles are classified on
fastVEP's own transcript. A chromosome 22 run takes about a minute on two
cores, most of it loading the whole-genome GFF3 and FASTA, and peaks at about
7 GB of memory.
