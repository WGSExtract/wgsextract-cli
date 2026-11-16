# Reference evidence (gVCF)

A variant VCF lists the places a genome differs from the reference. It can't
say whether a base it doesn't list was read confidently as reference, read
poorly, or not read at all. Polygenic scores, PharmCAT and confidence labels
need the difference: treating every missing base as reference turns a
coverage gap into a confident genotype.

`--reference-evidence gvcf` asks a caller to also write a gVCF, which records
every base it assessed, beside its variant VCF. The variant VCF is unchanged.
It is opt-in (`reference_evidence = "gvcf"` in `config.toml` makes it the
default) because it costs time, memory and disk.

| Caller | gVCF | Status |
| --- | --- | --- |
| `vcf snp`, `vcf indel` (bcftools) | `bcftools.g.vcf.gz` | available |
| `vcf deepvariant` (Google's image, `wgse-deepvariant`, Parabricks) | `deepvariant.g.vcf.gz` | available |
| `vcf gatk`, `vcf clair3` | — | planned |

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
| `Region` | The `--region` the run assessed; empty for the whole genome. |
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
recorded in `PloidyZero`; Parabricks and `wgse-deepvariant` call diploid
genomes only, as without the gVCF.

The gVCF and its index are outputs of the `postprocess_variants` stage: if
that stage is interrupted, a rerun repeats it alone. Its stages' work is
deleted once the run finishes, so a gVCF deleted later reruns every stage, as
does turning `--reference-evidence` on or off, which changes what
`make_examples` writes.

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

## Evidence states

`wgsextract_cli.core.reference_evidence` reads a gVCF (one written here, or
another caller's, whose quality model it takes from its FORMAT/GQ
declaration) and puts every base in one state. Where records overlap, the
state higher in this table wins.

| State | Meaning |
| --- | --- |
| `excluded_by_policy` | Ploidy 0 for this sample (`PloidyZero`): not expected to exist. |
| `variant` | Inside the REF span of a called non-reference allele. |
| `filtered` | A record with a failing FILTER (DeepVariant's `RefCall`, for example). |
| `no_call` | A missing genotype (`.` or `./.`). |
| `low_quality_reference` | Reference, but with depth below `confidence_min_depth` (halved for a haploid call) or, for the `gq` model, GQ below `confidence_min_gq`. |
| `uncovered` | Assessed, but no reads: no record (bcftools, or DeepVariant over reference N) or a block with MIN_DP 0 (DeepVariant, GATK). |
| `callable_reference` | Reference, with enough depth (and GQ). |
| `not_assessed` | Outside the region the caller assessed. |

The thresholds are the `confidence_*` settings shared with
[variant confidence labels](variant-confidence.md). `score pgs`, `vcf pharmcat` and the
confidence labels are to move onto this classifier.
