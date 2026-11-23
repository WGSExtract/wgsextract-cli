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
wgsextract ref acmg-data --dry-run    # plan and terms, offline
wgsextract ref acmg-data --download   # download and build, once
wgsextract vep run --input sample.vcf.gz --engine vep-rs
wgsextract vcf acmg --vcf-input sample_vep.vcf.gz --research-acmg
```

## What it writes

- `acmg_annotated.vcf.gz` (+ `.tbi` or `.csi`): the input VCF, vep-rs consequences
  unchanged, with three INFO fields per ALT allele: `ACMG_CLASS`,
  `ACMG_CRITERIA` (the criteria met, joined by `&`) and `ACMG_TRANSCRIPT`.
  The header carries the disclaimer in `##WGSE_ACMG`. Classifying an
  `acmg_annotated.vcf.gz` again replaces its earlier classes. The new VCF and
  index are published only after indexing succeeds; a failed index build
  leaves any previous VCF and its index unchanged.
- `acmg_summary.json`: the disclaimer, the fastVEP version, the databases
  (files, sources, assemblies, sizes, dates), the optional sources not loaded,
  the criteria never evaluated, and the counts of
  classes, criteria and transcript choices.
- The result manifest lists both (roles `annotated_vcf` and `acmg_summary`),
  the class counts as metrics (`acmg_p`, `acmg_lp`, ...), and the warning
  `acmg_research_only`.
- When RepeatMasker is missing, the header, summary and manifest say BP3
  was not evaluated (`acmg_bp3_not_evaluated`). When the gnomAD region flags
  are missing, they list affected flags and contigs
  (`acmg_gnomad_region_flags_not_evaluated`). The population criteria still
  run, but their low-complexity/segmental duplication veto is incomplete.
  The region veto is only evaluated for alleles with a matching gnomAD
  record; in particular, fastVEP can still assign PM2_Supporting to an
  unmatched allele in one of these regions.

[`vcf table`](variant-table.md) of `acmg_annotated.vcf.gz` has the columns
`acmg_class`, `acmg_criteria` and `acmg_transcript`, with the disclaimer in
its metadata. [`report`](personal-report.md) then adds a research-only
section: the classes of the alleles the sample carries, and its P/LP calls in
the gene list's genes. In the desktop app, `vcf acmg` is an action of the VCF
page's annotations that runs only once you tick that you understand it is
research use only.

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
has records on (by default those chromosomes' gnomAD is read per run
instead; see [gnomAD and SpliceAI per run](#gnomad-and-spliceai-per-run)).
Records on other contigs gnomAD holds nothing of (chrM, which gnomAD v4.1
lacks, and unplaced, alt or decoy contigs) are left unclassified, with the
warning `acmg_contigs_not_classified`, and listed in the summary's
`contigs_not_classified`.

## Requirements

- GRCh38 only, with its reference FASTA (`--ref` or the reference library).
- fastVEP 0.5.0: `pixi install -e fastvep` on Linux (x86-64 and ARM64),
  macOS (Intel and Apple Silicon), or Windows x86-64. WGS Extract builds the
  CLI-only packages from the pinned upstream release, with portable CPU
  targets and upstream/third-party licence notices, and pins each package by
  URL and SHA-256. The CLI uses the installed environment automatically; it
  never installs it or downloads databases during classification.
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

`wgsextract ref acmg-data --download` downloads the sources and the Ensembl GFF3
from their publishers, checks them, shows their terms and builds the
databases with `fastvep sa-build`:

- The GFF3 goes in `downloads/acmg/` in the library, the downloads in
  `downloads/acmg/sources/` and the databases in `downloads/acmg/sa/`, or in
  `--sa-dir` (`acmg_sa_dir`). `--ensembl-release` (default 116) should match
  your vep-rs cache.
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
- The joint release has no `lcr` or `segdup` flags. The build marks gnomAD
  records whose POS is in the two GRCh38 interval tracks published by gnomAD;
  fastVEP uses those flags to withhold unreliable frequency evidence for
  BA1, BS1, BS2 and PM2_Supporting. An allele absent from the source has no
  gnomAD record to flag. The same tracks, when installed, are applied to
  per-run site databases. `ref status` lists installed tracks and flagged
  library chromosomes.
- BP3 uses UCSC RepeatMasker `Simple_repeat` and `Low_complexity` intervals
  on the primary nuclear chromosomes. Other repeat classes are not assumed
  to be non-functional. Neither track membership nor a BP3 assignment is
  proof that an interval has no known function.
- SpliceAI is the library's `spliceai_hg38.vcf.gz` (`ref spliceai`, from
  Ensembl), downloaded when missing, or `--spliceai-file`: Illumina's own
  files need a BaseSpace login and licence acceptance, so download them
  yourself and pass the file. It must be GRCh38 and tabix-indexed.
- `--chromosomes` (such as `chr22`, or `chr1-22,chrX`; default all) limits
  gnomAD, SpliceAI, REVEL and phyloP; the others are genome-wide. `vcf acmg`
  can read gnomAD and SpliceAI per run only with explicit opt-in (below).
- `--site-data per-run` doesn't build gnomAD and SpliceAI at all (below); the
  folder remembers it, and `ref status` says so.
- It's resumable: a rerun builds only what is missing, resuming interrupted
  downloads (a failed run keeps them), and a source that failed doesn't stop
  the others. Ctrl-C stops the builds running and starts no more. To take a new
  ClinVar or ClinGen release, delete its database (`clinvar.osa2`,
  `clinvar_protein.oga`, `omim.oga`) and its download, and rerun.
- On an existing library, `--download --chromosomes chr22` adds the tracks
  and rebuilds gnomAD for chr22 only if it lacks current region flags. It
  reuses unaffected databases. Upgrading all chromosomes can re-stream the
  entire joint release; use `--site-data per-run` to prepare just the tracks
  for opted-in per-run use without rebuilding library gnomAD.
- `--dry-run` prints the plan and terms without reaching the network. With
  `--download` it queries publisher servers for sizes without downloading
  files. Without `--download`, a non-preview run names missing sources and
  stops without reaching the network.
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
| UCSC hg38 RepeatMasker | 156 MB | `repeatmasker.osi`, 14 MB | [UCSC data terms](https://genome.ucsc.edu/goldenPath/credits.html); cite UCSC and RepeatMasker |
| gnomAD GRCh38 LCRFromHengHg38 / GRCh38_segdups | 50 MB / 0.18 MB | `gnomad_lcr.bed.gz` / `gnomad_segdup.bed.gz`, 13 MB / 0.07 MB | CC0; cite gnomAD |

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
for the SpliceAI download in the library's `downloads/` and 1.4 GiB for the
other downloads.

## gnomAD and SpliceAI per run

A whole genome's gnomAD database takes an 877 GB stream to build, and
SpliceAI a 29 GB download, for about 27 GB of databases. `vcf acmg` can
instead read only the input's own sites of them, from the publishers' indexed
files: gnomAD v4.1's joint sites on Google Cloud Storage, and the library's
`spliceai_hg38.vcf.gz` or else Ensembl's copy of it. It builds them into small
databases for the run with the same `fastvep sa-build` and INFO fields as
`ref acmg-data`, so every variant finds what it would find in the full
database: fastVEP looks gnomAD and SpliceAI up by the variant's own position
and alleles, never a neighbour's (no criterion reads a nearby record of
them).

- `--site-data library` (the default) uses the library's gnomAD and SpliceAI
  databases, stays offline, and refuses missing sources or nuclear chromosomes
  with guidance to build them using `ref acmg-data --download`. `auto` is an offline
  alias. Only explicit `--site-data per-run` opts in to remote lookups, leaving
  the library's gnomAD/SpliceAI databases out. **This sends input-derived variant
  positions through byte-range requests to Google Cloud Storage (gnomAD) and
  Ensembl (SpliceAI unless configured locally), revealing access patterns even
  though the VCF is not uploaded.** The run warns `acmg_site_data_privacy`.
  `ref acmg-data --download --site-data per-run` builds the other sources only;
  classification still requires explicit `vcf acmg --site-data per-run`.
- A custom `ref acmg-data --spliceai-file PATH` is recorded and reused, including
  with `--site-data per-run`; a missing chosen source is refused, never silently
  replaced with Ensembl's. Local VCFs require a `.tbi` index. Older manifests
  recording a custom source without the choice must record it again with
  `ref acmg-data --download --site-data per-run --spliceai-file PATH`.
- A site is each record's span, one base wider on each side, both as written
  and left-aligned by `bcftools norm` against the reference, as fastVEP
  left-aligns an indel before it looks gnomAD up.
- A tabix index only places records to 16 kb windows, about 5 MB of gnomAD,
  so each site is found within its window by searching the compressed file:
  2.9 requests and about 52 KB a site on gnomAD. Every BGZF block's CRC-32 is
  checked, and a file that changes during the run (`If-Range`) stops it.
- A source or chromosome that can't be read is refused as a missing database
  is. A chromosome whose sites have no gnomAD record gets its first record, so
  the database says it holds that chromosome and its sites read as absent from
  gnomAD, as they are.
- The databases are cached in the databases folder's `sites/` (in the run's
  temporary folder when that can't be written), keyed on the sites and the
  file's strong ETag or size and modification date; remote files without a usable
  validator are refused. Local files are checked for replacement or modification
  throughout the read. The 200 most recently used databases are kept, with a
  two-day grace period for active runs. A rerun checks remote identity but reads
  no records. Concurrent builders reuse a completed winner without deleting it.
  A server job refuses a `sites/` folder, SpliceAI VCF or its `.tbi` index that
  leads outside its allowed folders, and
  holds the job lock on `sites/` while it writes there.
- `acmg_summary.json`'s `site_data` lists, per source and chromosome, the file
  read, its release, ETag and date, the sites, records, bytes and requests
  read, and whether the cache had it. SpliceAI read per run warns
  `acmg_data_terms` with its terms.

Measured on HG002's chromosome 22 (50,284 records, 49,239 sites) with 4
threads (64 connections to Google Cloud Storage, 16 to Ensembl, which refuses
more), the two sources read side by side:

| | gnomAD v4.1 joint | SpliceAI (Ensembl) | Whole genome, estimated |
|---|---|---|---|
| Read | 2.6 GB of 15.6 GB in 141,959 requests, 208 s | 355 MB of 452 MB in 12,917 requests, 136 s | gnomAD about 250 GB, 14 million requests, 5.6 hours; SpliceAI about 23 GB beside it |
| Database | 8.7 MB, 217,251 records | 1.8 MB, 231,900 records | about 1 GB together |

The run took 249 s (40 s with the library's databases, 44 s from the cache),
at most 6.8 GB of memory, fastVEP's. The whole-genome estimate scales
chromosome 22 by HG002's 97 times as many variants; reading is bound by
round trips, not bandwidth. That is a quarter of gnomAD's 877 GB stream and
leaves no 27 GB of databases on disk, but a genome takes hours, so a library
that classifies many genomes is better off building them once.

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
