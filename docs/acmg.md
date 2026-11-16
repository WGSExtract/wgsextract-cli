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
wgsextract vep run --input sample.vcf.gz --engine vep-rs
wgsextract vcf acmg --vcf-input sample_vep.vcf.gz --research-acmg \
  --sa-dir ~/acmg/sa --gff3 ~/acmg/Homo_sapiens.GRCh38.116.gff3.gz
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
- A folder of fastVEP databases built with `fastvep sa-build` (see fastVEP's
  `docs/ACMG_SETUP.md`), one per source, all with `--assembly GRCh38`. A
RepeatMasker track (`--source custom_bed --name repeatmasker`, an `.osi`)
is optional and lets fastVEP evaluate BP3. Set `acmg_sa_dir` and `acmg_gff3` in
  config.toml to avoid passing them each time.

| Source | `sa-build --source` | Terms |
|---|---|---|
| ClinVar VCF | `clinvar` | NCBI, no restrictions |
| ClinVar `variant_summary.txt.gz` | `clinvar_protein` | NCBI, no restrictions |
| gnomAD v4.1 joint sites (AC, AN, nhomalt, faf95 and filters) | `gnomad` | CC0 |
| gnomAD v4.1 constraint metrics | `gnomad_genes` | CC0 |
| ClinGen gene-disease validity (converted with fastVEP's `clingen_gdv_to_oga.py`) | `omim` | CC0; attribution requested |
| REVEL | `revel` | free for non-commercial use; others contact the authors |
| SpliceAI scores (Illumina, via Ensembl) | `spliceai` | academic and not-for-profit use only |
| UCSC phyloP100way | `phylop` | freely available for public use |

You download these yourself and their terms apply to you; WGS Extract
doesn't mirror them. `wgsextract ref` doesn't build these databases yet.

## Validation

ClinVar 2026-11-08, chromosome 22 variants with 2+ review stars, with these
eight sources (gnomAD, SpliceAI, REVEL and phyloP built for chromosome 22):

| ClinVar | Same direction | VUS | Opposite direction |
|---|---|---|---|
| P/LP (1,960) | 75.4% P/LP | 24.5% | 2 |
| B/LB (8,231) | 77.2% B/LB | 22.8% | 1 |

ClinVar itself feeds some criteria (the PM2 backstop, PM5, PM1, PS1), so these
numbers are an optimistic upper bound. GIAB HG002's chromosome 22 (50,284
records) gets no P/LP call; 0.7% of its genic alleles are classified on
fastVEP's own transcript. A chromosome 22 run takes about a minute on two
cores, most of it loading the whole-genome GFF3 and FASTA, and peaks at about
7 GB of memory.
