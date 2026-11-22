# Runs of homozygosity (`qc roh`)

A run of homozygosity (ROH) is a stretch of a chromosome where both copies
are identical. Short runs are in everyone's genome. Long runs on many
chromosomes usually mean the parents share ancestry. A single long run over
an imprinted region can mean both copies came from one parent
(uniparental isodisomy), which for a few regions causes a known imprinting
disorder. `qc roh` finds the runs in a VCF and flags that last case for
review, using population allele frequencies you have on disk.

```sh
wgsextract qc roh --input sample.vcf.gz \
  --af-file gnomad.genomes.v4.1.sites.chr15.vcf.bgz --af-tag AF --regions chr15
wgsextract qc roh --input sample.vcf.gz --af-file 1kgp_af.tab.gz --af-build hg38
```

It needs bcftools, bgzip and tabix, and runs offline: nothing is downloaded,
and no sample data leaves the computer.

## Population allele frequencies

`bcftools roh` scores each site with its population allele frequency (AF),
so the frequencies change the answer. On HG00096's chromosome 20 a constant
AF of 0.4 (OwnGenome's choice) found 4.5 times as many runs as real
1000 Genomes frequencies at the same sites, including two of 1-4 Mb
([comparison](#validation)). `qc roh` therefore needs real frequencies:
`--af-file` is required, and there is no constant fallback.

`--af-file` takes either:

- a sites VCF/BCF with an AF field in INFO (`--af-tag`, default `AF`), such
  as gnomAD's or a 1000 Genomes release; a population-specific field
  (gnomAD's `AF_nfe`, say) is closer for a sample of that ancestry;
- or a `bcftools roh` table: tab-separated `CHROM`, `POS`, `REF,ALT`, `AF`,
  which has no header to tell its build, so `--af-build` is required.

Its build must be the sample's. A VCF's build is read from its contig lengths
(else give `--af-build`), and a file on another build is refused
(`roh_af_build_mismatch`). Contig names (`chr15` or `15`) are matched to the
sample. Record the release with `--af-release "gnomAD v4.1 genomes"`; the
file's size and digest are recorded in any case.

gnomAD's per-chromosome VCFs are large. To keep only what `qc roh` reads,
stream one through bcftools once:

```sh
bcftools view -G -m2 -M2 -v snps \
  https://storage.googleapis.com/gcp-public-data--gnomad/release/4.1/vcf/genomes/gnomad.genomes.v4.1.sites.chr15.vcf.bgz \
  | bcftools annotate -x '^INFO/AF,INFO/AF_nfe' -Oz -o gnomad.v4.1.chr15.af.vcf.gz
tabix -p vcf gnomad.v4.1.chr15.af.vcf.gz
```

Use `bcftools concat` to join chromosomes into one file for a whole-genome run.

**Reuse.** The first run makes the `bcftools roh` table from the file
(biallelic SNVs with 0 < AF < 1, on the chromosomes in scope) and, when
`reference_library` is set, keeps it in `<library>/roh/<key>/` with the
file's identity. Later runs reuse it (`"prepared": "reused"` in the report)
until the file changes, for example a new release saved under the same name,
which rebuilds it and says why (`prepared_reason`). A run holds a lock on
the table while it uses it, so a run started on the new release waits
rather than replace the table under it. Without a reference
library the table is made for each run.

**Terms.** gnomAD's primary data are CC0, with attribution requested; its
SpliceAI annotations are CC BY-NC, and `qc roh` doesn't use them. 1000
Genomes data are available without embargo, cited as the project asks.
Snapshots of both terms are in `src/wgsextract_cli/assets/resource_terms/`.
You download the file yourself; WGS Extract doesn't redistribute it.

bcftools' own code is MIT/Expat, but the Bioconda build that pixi installs
(1.24) links the GNU Scientific Library, which makes that binary
GPL-3.0-or-later (its package metadata says so). WGS Extract runs it as a
separate program, and is GPL-3.0-or-later itself, so the terms are
compatible either way. The `qc roh` code is new, not adapted from
OwnGenome.

## Markers and model

The markers are the sample's biallelic SNV calls with `PASS` (or no)
`FILTER` at sites of the AF table. A call without a genotype, or with GQ
below `--min-gq` (20), is left out and counted (`missing_genotype`,
`weak_genotype`), so a weak heterozygous call doesn't split a run. A VCF
without GQ (a genotype export, say) is refused (`roh_no_gq`) unless you pass
`--min-gq 0` to accept its genotypes without a quality floor. Genotypes
are used, not likelihoods (`bcftools roh --GTs-only 30`), so callers'
differently calibrated PLs don't change the result. The HMM's transition
probabilities are bcftools' defaults, passed explicitly.

A run ends where two consecutive markers are more than 1 Mb apart, as in
PLINK's `--homozyg-gap`: a centromere or an uncalled stretch carries no
evidence. Without that, HG002's chromosome 9 had one 21.8 Mb "run" across
its centromere. A run also ends at the edge of each `--regions` range, so
bases left out between two ranges count neither in a run nor in F_ROH.

A VCF of variants only (most callers' output) has no hom-ref calls, so runs
are found from heterozygous and hom-alt calls; their ends are less precise
(`roh_variants_only` warning). A VCF with hom-ref calls at the AF sites (a
multi-sample or all-sites VCF) gives more markers. A gVCF is refused; give
the VCF of its calls.

## Scope and F_ROH

`--regions` declares what is assessed: autosomes (`chr20`) or ranges
(`chr15:20000000-30000000`). By default it is every autosome in the VCF
header. Sex chromosomes are left out: a man's X is all "homozygous".

F_ROH is the length of runs of at least 1.5 Mb divided by the bases the
markers cover: on each assessed chromosome, from its first to its last
marker, less gaps over 1 Mb. That denominator is the assessed scope's, not
the genome's. `scope.whole_genome` is true only when all 22 autosomes are in
scope whole and have markers; otherwise the report says that F_ROH covers the scope only.
A chromosome in scope without markers is reported
(`roh_contigs_without_markers`) and adds nothing to the denominator.

## Isodisomy review flag

A run is flagged "investigate possible isodisomy" only when it overlaps one
of the imprinted regions below and is long enough:

- 5 Mb (`--flag-terminal-mb`) when it reaches a chromosome end: the last
  marker, or the first except on the acrocentric chromosomes 13-15, 21 and
  22, whose first markers are at the centromere, and that marker is within
  1 Mb of the end. This is decided only when the whole chromosome is in
  scope. A run in a partial range, or one reaching a last marker that stops
  short of the end (the VCF or the AF file has no sites beyond it), counts
  as interstitial (`position: undetermined`).
- 10 Mb (`--flag-interstitial-mb`) otherwise.

It also needs 100 markers and an average bcftools quality of 30. These are
the screening lengths clinical microarray laboratories use to suspect UPD (a
telomeric run of 5 Mb, or an interstitial run of 10 Mb on chromosome 6, 7,
11, 14, 15 or 20; Kim et al., *Ann Lab Med* 2026;46:83, and Del Gaudio et
al. 2020).

A run is only labelled with a region it overlaps: a run elsewhere on
chromosome 20 never gets GNAS's conditions. The flag names the region, its
genes and the conditions associated with its UPD, and says what a run can't
tell. It is not a diagnosis:

- parents who share ancestry, or a deletion of one copy, also make runs;
- a run can't show which parent the region came from, so it can't tell
  apart the two conditions most regions have;
- heterodisomy (two different copies from one parent) leaves no run at all.

Methylation or parental testing, through a clinical genetics service, can
tell. Several long runs on different chromosomes are noted as more typical of
shared ancestry than of UPD.

| Region | Genes | Conditions (UPD) |
| --- | --- | --- |
| 6q24.2 | PLAGL1, HYMAI | 6q24-related transient neonatal diabetes (paternal UPD6) |
| 7p12.1 | GRB10 | Silver-Russell syndrome (maternal UPD7) |
| 7q32.2 | MEST | Silver-Russell syndrome (maternal UPD7) |
| 11p15.5 | H19, IGF2, KCNQ1OT1, CDKN1C | Beckwith-Wiedemann (paternal), Silver-Russell (maternal UPD11p15) |
| 14q32.2 | DLK1, MEG3, RTL1, MEG8 | Temple (maternal), Kagami-Ogata (paternal UPD14) |
| 15q11.2-q13 | MKRN3, MAGEL2, NDN, SNRPN, UBE3A | Prader-Willi (maternal), Angelman (paternal UPD15) |
| 20q13.32 | GNAS | Pseudohypoparathyroidism 1B (paternal UPD20q), growth restriction (maternal UPD20) |

The list (`src/wgsextract_cli/assets/roh/imprinted_regions.json`) is curated
from Eggermann et al., *Clin Epigenetics* 2015;7:123, Del Gaudio et al.,
*Genet Med* 2020;22:1133 and Mulchandani et al., *Genet Med* 2016;18:309;
gene and condition names are facts, not copied text. Each region spans its
genes in the bundled NCBI RefSeq gene table for the sample's build (public
domain; GRCh37, GRCh38 and T2T), so its coordinates always match the build.
`--imprinted-regions` takes your own BED (chrom, start, end, name,
`;`-separated conditions) whose `track` line declares its build (`track
name=imprinted db=hg38`); a BED for another build is refused
(`roh_regions_build_mismatch`), as is a row whose start isn't before its end
(`roh_regions_unreadable`).

## Outputs

In the output folder, named after the input (`sample.vcf.gz` gives
`sample.roh.*`):

| File | Contents |
| --- | --- |
| `<stem>.roh.tsv` | One run per row: chrom, start, end (first and last marker), length_bp, markers, quality, position, imprinted_regions, flagged |
| `<stem>.roh.json` | The `wgsextract.roh/1` document ([schema](../src/wgsextract_cli/assets/schemas/wgsextract.roh.1.schema.json)): runs, flags, marker counts, scope and denominator, summary, parameters, and the AF file's path, release, build, digest and reuse |
| `<stem>.roh.markers.tsv.gz` | Every modelled marker: chrom, pos, state (AZ or HW), bcftools' quality |
| `<stem>.roh.manifest.json` | The [result manifest](result-manifest.md); its `details` is the document |

Metrics: `roh_segments`, `roh_total_bp`, `roh_longest_bp`, `roh_froh`,
`roh_markers`, `roh_flags`, `roh_whole_genome` and `roh_af_prepared`. Each
flag is also a warning (`investigate_possible_isodisomy`).

Errors have codes: `roh_af_missing`, `roh_af_build_mismatch`,
`roh_af_build_unknown`, `roh_af_tag_missing`, `roh_af_unreadable`,
`roh_af_unsorted`, `roh_regions_build_mismatch`, `roh_regions_build_unknown`,
`roh_regions_unreadable`,
`roh_no_gq`, `roh_gvcf` and `roh_no_markers`.

## Validation

**HG00096, chromosome 20.** The OwnGenome comparison (#821) repeated with public data:
HG00096's chromosome 20 genotypes (hom-ref included) from the gnomAD v3.1.2
HGDP+1kGP callset, and AFs from its 3,123 1000 Genomes samples, at the same
1,403,289 sites in each run. `scratch/1174_compare.sh` keeps the marker
lists and parameters.

| Markers | AF | Runs | Total | Longest |
| --- | --- | --- | --- | --- |
| All genotyped sites | 1000 Genomes | 176 | 20.5 Mb | 0.70 Mb |
| All genotyped sites | constant 0.4 | 800 | 58.3 Mb | 3.91 Mb |
| Variants only (73,266) | 1000 Genomes | 98 | 9.1 Mb | 0.66 Mb |
| Variants only (73,266) | constant 0.4 | 139 | 10.4 Mb | 0.52 Mb |

Real frequencies gave 78% fewer runs (30% with variants only) and 65% less
ROH (12%) than a constant 0.4; the OwnGenome comparison reported 42% and
31% on the NYGC callset. No run reached 5 Mb either way.
`qc roh --min-gq 0` (the export has no GQ) gives the first row; its F_ROH (0)
is chromosome 20's alone.

**HG002, whole genome.** The GIAB v4.2.1 benchmark VCF (variants only,
GRCh38) with the same 1000 Genomes frequencies: 3,991 runs from 3,334,699
markers, the longest 3.3 Mb, F_ROH 0.45%, no flags. The first run took 98 s
(50 s preparing the 66.5 million-site table) and 160 MB of memory; with the
table reused, 40 s.
