# Reference evidence (gVCF)

A variant VCF lists the places a genome differs from the reference. It can't
say whether a base it doesn't list was read confidently as reference, read
poorly, or not read at all. Polygenic scores, PharmCAT and confidence labels
need the difference: treating every missing base as reference turns a
coverage gap into a confident genotype.

`--reference-evidence gvcf` asks a caller to also write a gVCF, which records
every base it assessed, beside its variant VCF. The variant VCF is unchanged,
except that GATK genotypes it from the gVCF (see [GATK](#gatk)).
It is opt-in (`reference_evidence = "gvcf"` in `config.toml` makes it the
default) because it costs time, memory and disk.

| Caller | gVCF | Status |
| --- | --- | --- |
| `vcf snp`, `vcf indel` (bcftools) | `bcftools.g.vcf.gz` | available |
| `vcf deepvariant` (Google's image, `wgse-deepvariant`, Parabricks) | `deepvariant.g.vcf.gz` | available |
| `vcf gatk` (HaplotypeCaller on the CPU; not `--gpu` or `--scatter`) | `gatk.g.vcf.gz` | available |
| `vcf clair3` (CPU and `--gpu`) | `clair3.g.vcf.gz` | available, about 2.5-6x slower |

`analyze` follows the `config.toml` setting: its SNP step writes the gVCF
(its indel step doesn't repeat it), and a rerun repeats the SNP step when the
gVCF or its index is missing.

## Outputs

The gVCF and its index are separate outputs in the
[result manifest](result-manifest.md): role `reference_evidence` (with the
provenance below as its `metrics`) and `reference_evidence_index`. The gVCF is
written under a temporary name and renamed into place, then its index, so a
failed run never leaves a partial gVCF or an index that describes another
file.

The gVCF's `##wgseReferenceEvidence` header (schema
`wgsextract.reference_evidence/1`) records how it was made:

| Field | Meaning |
| --- | --- |
| `Engine`, `Version` | The caller and its version. |
| `QualityModel` | `gq`: reference blocks carry a genotype quality. `depth_only`: they carry only depth (bcftools). |
| `Bands` | The depth (bcftools) or GQ bands its blocks are grouped by. |
| `MinMQ` | Reads below this mapping quality are left out of the evidence. |
| `Region` | The bases the run assessed, as `contig:start-end` items (a BED or literal `--region`, resolved); empty for the whole genome. |
| `Ploidy`, `Sex`, `PloidyPolicy` | The ploidy rules and sex used, and a digest of both. |
| `PloidyZero` | Bases with ploidy 0 for this sample, such as a woman's chrY. |

## bcftools

The gVCF comes from its own pileup of SNPs and indels together. `vcf snp`'s
SNP-only pileup can't be reused: it reports indel sites as reference (420 of
3,007 GIAB truth variants in a 2 Mb test). Its blocks are grouped by minimum
depth with bands `0,5,10,20`, the smallest of those tested, and carry no GQ,
only DP and MIN_DP. Reads below mapping quality 20 are left out, as GATK's
HaplotypeCaller does by default, so reads that map equally well elsewhere
don't make a repeat look like confident reference. Its variant records come
from that pileup too; the call set is still `snps.vcf.gz`/`indels.vcf.gz`.
Each contig with reads is called in its own process, so its records don't
depend on `--threads`.

Measured on HG002 (35x Illumina, GRCh38) against GIAB v4.2.1:

| Region | `vcf snp` | with the gVCF | gVCF + index |
| --- | --- | --- | --- |
| chr20:10-12 Mb, 1 thread | 13.2 s | 24.4 s | 0.23 MB |
| chr20, 2 threads | 240 s | 468 s | 7.8 MB (about 0.4 GB for a genome) |

The variant VCF was identical with and without it. Of the 82,588 truth
variants in chr20's confident regions, 432 sit on bases the gVCF calls
callable reference, variants bcftools missed (335 SNVs and 97 indels).

## DeepVariant

DeepVariant writes the gVCF in the same run as its VCF, which is unchanged:
Google's image or a local `run_deepvariant` and `wgse-deepvariant` with
`--output_gvcf`, Parabricks (`--gpu`) with `--gvcf`. Its blocks carry GQ, in
DeepVariant's bins of 5, and MIN_DP. It writes bases no read covers as `0/0`
blocks with GQ 0 and MIN_DP 0, which read as `uncovered`, and its `RefCall`
and `NoCall` records as `filtered`. Only the gVCF's header changes when it is
published (`bcftools reheader`), so every record stays as DeepVariant wrote
it. Bases a sex/PAR ploidy of 0 leaves out of calling (a woman's chrY) are
recorded in `PloidyZero`. Parabricks calls a man's haploid chrX and chrY
apart from the rest and its gVCF is gathered from the parts (see
[Sex and PAR](#sex-and-par)); `wgse-deepvariant` calls diploid genomes only,
as without the gVCF. `wgse-deepvariant`'s gVCF agrees
with Google's in every field the states read; before 0.3.1 its variant
records lacked the PL values of genotypes with `<*>` (#999).

The gVCF and its index are outputs of the `postprocess_variants` stage: if
that stage is interrupted, a rerun repeats it alone. Its stages' work is
deleted once the run finishes, so a gVCF deleted later reruns every stage, as
do turning `--reference-evidence` on or off, which changes what
`make_examples` writes, and a change to the gVCF's provenance alone (such as
a wider `--region` on a woman's chrY). A run that keeps no stage gets the
full preflight check.

Memory: Google's `postprocess_variants` streams each partition's reference
records into the gVCF, so it needs no more memory (chr20 as one partition:
1.14 GiB either way). `wgse-deepvariant` keeps every contig's calls until it
writes the gVCF, so its preflight allows about 20 GiB for a whole genome,
against 5 GiB without; on a smaller machine call a `--region`, or use
Google's image (see [resource_estimates.md](resource_estimates.md#deepvariant-postprocess_variants)).

Measured on HG002 (35x Illumina, GRCh38) chr20, 8 threads, against GIAB
v4.2.1:

| Engine | VCF only | with the gVCF | gVCF + index |
| --- | --- | --- | --- |
| Google's image, CPU | 859 s | 1,030 s | 20.8 MB (about 1 GB for a genome) |
| `wgse-deepvariant`, CPU | 500 s | 575 s | 19.4 MB |
| Parabricks, RTX 3090 | 78 s | 99 s | 21.0 MB |

Times are from a shared host (load 9-20 on 12 cores); on a quieter one
Google's image took 943 and 989 s. Parabricks' row is pbrun with the same
options, run directly.

The variant VCFs were identical with and without the gVCF. Of the 82,588
truth variants in chr20's confident regions, 116 sit on bases the gVCF calls
callable reference, against bcftools' 432.

## GATK

HaplotypeCaller writes the gVCF (`-ERC GVCF`), and GenotypeGVCFs genotypes
the variant VCF from it, so one HaplotypeCaller run makes both. Its blocks
carry GQ and MIN_DP, with `<NON_REF>`, banded by GQ at `10,20,30,60`: GATK's
default (one band per GQ below 60) made a genome's gVCF about 6.5 GB, these
about 1.4 GB, with the same bases at GQ 20 or more. Reads below mapping
quality 20 are left out, GATK's default. Parabricks' HaplotypeCaller
(`--gpu`) and Ultima's `--scatter` aren't supported with the gVCF; a run
asking for both stops before calling.

Measured on HG002 (35x Illumina, GRCh38) against GIAB v4.2.1, 4 threads:

| Region | VCF only | with the gVCF | gVCF + index |
| --- | --- | --- | --- |
| chr20:10-12 Mb | 25.5 s | 38.4 s | 0.76 MB |
| chr20 | 1,374 s | 1,432 s | 28.3 MB (about 1.4 GB for a genome) |

HaplotypeCaller took the same time either way; GenotypeGVCFs added 17 s and
cutting and publishing the gVCF 4 s. GenotypeGVCFs' VCF had 134,083
records against HaplotypeCaller's 134,073 without the gVCF: it genotypes
each site from the gVCF's likelihoods, so a few borderline sites differ. Of
the 82,588 truth variants in chr20's confident regions, 102 sit on bases the
gVCF calls callable reference (DeepVariant 116, bcftools 432). GATK also
writes blocks over reference N, with MIN_DP 0, so they read as `uncovered`.

## Clair3

Clair3 writes the gVCF (`--gvcf`) in the same run as its VCF, whose calls and
filters it doesn't change, on the CPU and in HKU's GPU image (`--gpu`). Its
blocks carry GQ, in Clair3's bins of 5, and MIN_DP; reads below mapping
quality 5 (Clair3's default) are left out. Its pileup step is much slower
with it, so a run that asks for it warns (`clair3_gvcf_cost`) and its
preflight scales the time it reports: about 3.8x on the CPU and 6x on the
GPU. Clair3 skips contigs the index places no reads on, and with none left
writes no gVCF; their bases read as `uncovered`. A missing gVCF when a
called contig has reads fails the run. Without a `--region` it calls chr1-22, X and Y, so the
gVCF's `Region` lists those and other contigs read as `not_assessed`.

Measured on HG002 ONT R10.4.1 chr20:10-15 Mb, 4 threads: 75 s for the VCF,
179 s with the gVCF (2.4x; 3.8x on 6 threads and 6x in the GPU image in
#863's research), 1.6 MB. The calls and filters were identical. Of 7,055
truth variants, 2 sit on bases the gVCF calls callable reference.

## Sex and PAR

GATK, Clair3 and Parabricks DeepVariant call a man's haploid chrX and chrY
apart from the diploid rest (see [caller-ploidy.md](caller-ploidy.md)), so the gVCF is written in
parts. Each part's blocks are cut at its edges (a block cut at its start
begins on its own reference base, read from the FASTA or, for a gzip one,
its decompressed copy) and the parts are gathered in reference order, so
every base has one record in the ploidy it was called with: `0` on a man's
chrX, `0/0` in its PAR (DeepVariant writes `0/0` in both). Any other record belongs to the part its POS is in;
one starting before the selection, such as a deletion whose REF reaches
into it, is kept where it covers the selected bases, once. Bases the policy gives ploidy 0, such as
a woman's chrY, aren't called and are recorded in `PloidyZero`.

`vcf gatk`, `vcf clair3` and Parabricks DeepVariant keep no step journal:
a rerun calls again, with or without the gVCF.

## Evidence states

`wgsextract_cli.core.reference_evidence` reads a gVCF (one written here, or
another caller's, whose quality model it takes from its FORMAT/GQ
declaration) and puts every base in one state. Where records overlap, the
state higher in this table wins.

| State | Meaning |
| --- | --- |
| `excluded_by_policy` | Ploidy 0 for this sample (`PloidyZero`): not expected to exist. |
| `variant` | Inside the REF span of a called non-reference allele. |
| `filtered` | A record with a failing FILTER (DeepVariant's `RefCall`, for example) or sample filter (`FORMAT/FT`). |
| `no_call` | A missing genotype (`.` or `./.`). |
| `low_quality_reference` | Reference, but with depth below `confidence_min_depth` (halved for a haploid call) or, for the `gq` model, GQ below `confidence_min_gq`. |
| `uncovered` | Assessed, but no reads: no record (bcftools, Clair3, or DeepVariant over reference N) or a block with MIN_DP 0 (DeepVariant, GATK). |
| `callable_reference` | Reference, with enough depth (and GQ). |
| `not_assessed` | Outside the region the caller assessed. |

The thresholds are the `confidence_*` settings shared with
[variant confidence labels](variant-confidence.md).

## Commands that read it

A command given a variant VCF finds its gVCF through the
[result manifest](result-manifest.md) of the run that wrote the VCF: a
manifest in the VCF's folder, or one its [sample workspace](sample-workspace.md)
recorded, that lists the VCF and a `reference_evidence` output, both
unchanged since (same recorded fingerprint; a manifest without fingerprints
isn't trusted), the gVCF indexed. Without one, the
command works as before.

- `score pgs` counts a scoring site the VCF doesn't list as homozygous
  reference only where the gVCF calls it `callable_reference` (an indexed
  `--ref` checks the alleles). Anywhere else the site stays missing, counted
  under its state's name in `match_counts`; `details.reference_evidence`
  names the gVCF and counts the scored sites in each state. A gVCF given as
  the input itself is read the same way, its explicit `0/0` records
  included. See [pgs.md](pgs.md#inputs-and-matching).
- `vcf pharmcat` gives a PharmCAT definition the VCF doesn't list a
  reference genotype only where every base of it is `callable_reference`
  with one ploidy; otherwise it is passed as a no-call (so `--absent-to-ref`
  can't make it reference) with the state as its `PGX_STATE`. See
  [variant-confidence.md](variant-confidence.md).

Measured on HG002 chr20 (35x Illumina), with PGS000018 (CAD, 1.75 million
sites, about 44,000 on chr20): `score pgs` matched 12,723 sites from the
variant VCF alone and 44,307 with bcftools' gVCF (31,584 more callable
reference sites; 10 low-quality), 44,185 with DeepVariant's (31,475 more; 56
low-quality, 31 filtered, 36 no-call, 12 uncovered). Inside GIAB v4.2.1's
confident regions, 30,771 of the 30,774 sites bcftools' gVCF made homozygous
reference agree with the truth set, and all 30,694 of DeepVariant's. Reading
the gVCF at the 1.75 million sites added 30-50 s to the run.
