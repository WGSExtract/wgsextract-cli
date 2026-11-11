# Structural-variant population frequencies (`vcf sv-freq`)

`vcf sv` and `vcf cnv` list hundreds to tens of thousands of structural
variants (SVs). Most of them are common in the population and unlikely to
matter for a rare condition. `vcf sv-freq` looks up each call in gnomAD's SV
catalogues, adds its population frequency, and flags the common ones, so the
variant table and the personal report can put the rare SVs first.

```sh
wgsextract ref gnomad-sv --install              # once: gnomAD-SV (GRCh38 1.7 GB, GRCh37 141 MB downloads)
wgsextract ref gnomad-sv --install --source lr  # optional, GRCh38: gnomAD-LR long-read SVs (53 GB, one chromosome at a time)
wgsextract vcf sv --input me.cram               # or vcf cnv, or any VCF of SVs
wgsextract vcf sv-freq --vcf-input sv.vcf.gz
wgsextract vcf table --vcf-input sv_freq_annotated.vcf.gz
```

## Catalogues

| Source | Build | Samples | Licence | Installed table |
| --- | --- | --- | --- | --- |
| gnomAD-SV v4.1 (`--source sr`) | GRCh38 | 63,046 short-read genomes | no use restriction (CC0) | `gnomad-sv/gnomad_sv_v4.1_hg38.tsv.gz` |
| gnomAD-SV v2.1 (`--source sr`) | GRCh37 | 10,847 short-read genomes | no use restriction (CC0) | `gnomad-sv/gnomad_sv_v2.1_hg19.tsv.gz` |
| gnomAD-LR 0.1 beta (`--source lr`) | GRCh38 | 1,027 All of Us long-read (PacBio HiFi) genomes | "publicly released without use restriction" ([announcement](https://gnomad.broadinstitute.org/news/2026-10-long-read-genome-data-in-gnomad/), 2026-10-22) | `gnomad-sv/gnomad_lr_0.1_hg38.tsv.gz` |

Each source VCF is downloaded from gnomAD's public bucket, checked against a
pinned SHA-256, reduced to a small table (CHROM, POS, END, SVTYPE, SVLEN, AF,
POPMAX_AF, ID; bgzip and tabix) and deleted. A marker beside the table records
its digest, which is checked before every use, so a changed table is refused
until it is installed again. One source VCF is on disk at a time: the
command refuses to start without room for the largest one (4.3 GB for
gnomAD-LR) plus the table.

Only PASS sites with an allele frequency are kept, plus gnomAD-SV's
multi-allelic CNVs, whose frequency is the non-reference carrier frequency
(`CN_NONREF_FREQ`). Breakends, translocations and complex SVs are left out.
gnomAD-LR contributes its SV alleles of 50 bp or more (deletions,
insertions and tandem duplications), not its tandem-repeat alleles.

**GRCh37.** gnomAD-SV v2.1 is native GRCh37, so no liftover is needed.
gnomAD-LR is GRCh38 only; a GRCh37 VCF is annotated with gnomAD-SV v2.1
alone. T2T-CHM13 is not supported (`--gnomad-sv-file` takes your own table).
A table whose `##build=` header names a different build from the VCF's is
refused.

**gnomAD-LR caveats.** It is a beta. Its All of Us participants
self-identified as Black or African American, so its frequencies describe
that population; `SV_POPMAX_AF` is the highest within it. It finds many SVs
in repeats and segmental duplications that short reads miss.

## Matching

SV calls rarely share exact breakpoints with a catalogue SV, so a call
matches a catalogue SV by the rules truvari and SVAFotate use:

- **Deletions, duplications, inversions and CNVs**: compatible types (a CNV
  matches a deletion, duplication or CNV) and a reciprocal overlap of at
  least `--min-overlap` (default 0.5): the shared span divided by the longer
  of the two.
- **Insertions**: starts at most `--max-distance` bp apart (default 500,
  truvari's default) and sizes at least `--min-size-similarity` alike
  (shorter / longer; default 0.5) when both are known. Insertions and
  duplications are compared this way too, since long-read callers report many
  tandem duplications as insertions.
- Calls are recognised from `SVTYPE` and `END`/`SVLEN` (Delly, Sniffles,
  pbsv, `vcf cnv`'s `<CN0>`... alleles), or, for sequence-resolved VCFs
  without `SVTYPE`, from the REF and ALT lengths (50 bp or more).

Of all the matching SVs, in every catalogue, the one with the highest
allele frequency is reported, as SVAFotate does, with its match quality. So
an SV that is common in any matching record is flagged common. This matters:
gnomAD-SV lists some SVs twice at identical breakpoints, once rare and once
common (e.g. two 6,041 bp deletions at chr1:184,845,609, AF 0.00002 and
0.58).

Why not run truvari, SVAFotate or svdb? None installs on Windows (all need
pysam or cyvcf2), truvari has no mode that annotates calls from a catalogue,
SVAFotate ships its own older catalogue, and svdb has no size-similarity
rule for insertions. The rules above are short and use only bcftools,
bgzip and tabix.

## Outputs

`sv_freq_annotated.vcf.gz` (+ `.tbi`) adds these INFO fields to every SV it
could look up; other records pass through unchanged:

| Field | Meaning |
| --- | --- |
| `SV_AF` | Allele frequency of the matching gnomAD SV. |
| `SV_POPMAX_AF` | Its highest genetic-ancestry-group frequency. |
| `SV_AF_ID`, `SV_AF_SOURCE` | Its ID and catalogue (`gnomAD-SV_v4.1`, `gnomAD-SV_v2.1`, `gnomAD-LR_0.1`). |
| `SV_AF_MATCH` | Match quality, 0-1: the reciprocal overlap, or for insertions the mean of breakpoint closeness and size similarity. |
| `SV_FREQ` | `common` (AF ≥ `--common-af`, default 0.01), `low`, `rare` (AF < `--rare-af`, default 0.001) or `novel` (no match). |
| `SV_COMMON` | Flag: a common SV. |

`sv_freq.tsv` has one row per SV with the same values. The result manifest's
metrics count the SVs, the matched fraction and each class. `vcf table`
fills its `sv_af` and `sv_freq` columns from the VCF, and `report` lists
each carried PASS SV with `SV_FREQ=rare` in its "Rare structural variants"
section, labelled "rare (AF < 0.001)".

"Novel" means not in these catalogues, which is not the same as rare: many
novel calls are caller artefacts or SVs in repeats that short reads cannot
resolve.

## Validation on HG002

On 2026-11-10, against the GIAB HG002 v5.0q SV benchmark (SVs of 50 bp or
more from `HG002_GRCh38_v5.0q_stvar.vcf.gz`, 46,504 calls; GRCh37 36,051)
and `vcf sv` Sniffles 2.8.1 calls on the #431 whole-genome ONT CRAM (24,587
SVs):

| VCF | Catalogues | Matched | Common | Low | Rare | Not in gnomAD |
| --- | --- | --- | --- | --- | --- | --- |
| v5.0q GRCh38 | gnomAD-SV v4.1 | 35.5% | 11,172 | 1,873 | 3,442 | 30,017 |
| v5.0q GRCh38 | + gnomAD-LR | 73.0% | 30,380 | 2,632 | 922 | 12,570 |
| v5.0q GRCh37 | gnomAD-SV v2.1 | 18.0% | 5,021 | 291 | 1,161 | 29,578 |
| Sniffles, ONT | gnomAD-SV v4.1 | 45.9% | 8,212 | 1,061 | 2,004 | 13,310 |
| Sniffles, ONT | + gnomAD-LR | 91.5% | 21,134 | 996 | 367 | 2,090 |

Short-read gnomAD-SV misses most of the SVs in tandem repeats and segmental
duplications that long reads resolve (most benchmark SVs are 50-1,000 bp
insertions and deletions in repeats); gnomAD-LR finds them. The median
allele frequency of the matched Sniffles calls is 0.26 with gnomAD-LR (0.21
without): one person's SVs are mostly common, as expected.

**Common SVs stay common through caller noise.** Of the Sniffles calls that
match a benchmark SV inside the benchmark regions which gnomAD calls common,
99.6% (18,282 of 18,364) get SV_AF > 1% themselves with gnomAD-LR, and 96.9%
(7,607 of 7,849) with gnomAD-SV alone (the acceptance bar is 90%).

**Known common deletions** in the benchmark are all matched and common:
GSTM1 (18 kb, AF 0.64), LCE3B/LCE3C (32 kb, AF 0.41), UGT2B17 (111 kb,
AF 0.35) and GSTT1 (37 kb, AF 0.27), all from gnomAD-SV v4.1.

Annotating 46,504 SVs against both catalogues takes about 7 s and 0.4 GB.
