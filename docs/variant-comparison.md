# Compare local VCFs

Use `vcf compare` to find small alleles shared by callers, platforms, or
samples. Agreement is not a truth benchmark, and sharing does not prove
transmission or that two samples have the same haplotype. Nothing is uploaded
or downloaded.

```sh
wgsextract vcf compare --callset dv=deepvariant.vcf.gz --callset bc=bcftools.vcf.gz \
  --ref GRCh38.fa --outdir comparison
```

Each label is unique. If a VCF contains multiple samples, select one with
`--sample dv=SAMPLE`. The command does not infer whether two samples are the
same person. Labels and their samples are recorded in the manifest.

The command compares exact `CHROM, POS, REF, ALT` keys, not just positions.
It reconciles `chr` aliases, splits multiallelic records, and uses an indexed
local `--ref` for left alignment and REF checking. Without `--ref`, indels
that differ in representation may not match. Inputs need a common build
identified by header metadata or matching sequence digests. A known build,
contig length, sequence digest, or REF mismatch is an error even with
`--allow-unverified-build`. That switch allows only an unverifiable build
and records a warning. Without a FASTA, known REF bases must agree where
records start at the same position; `N` is unknown, and longer REF padding
is allowed. No file is read from the VCF's `##reference` URL.

`--bed panel.bed` accepts zero-based, half-open BED intervals; `--region
chr22:100-200` accepts a one-based inclusive interval or a whole contig.
The scope overlaps the **normalized** REF span, including the anchor base
of an insertion. Blocks starting outside the panel can still provide
reference evidence inside it. MNPs and other equal-length complex alleles
are not atomized; symbolic variants, breakends, `*`, and non-reference
placeholders are not counted as comparison alleles.

## Select alleles across samples

```sh
wgsextract vcf compare --mode shared --callset sib1=sib1.vcf.gz \
  --callset sib2=sib2.vcf.gz --callset parent=parent.g.vcf.gz \
  --include sib1 --include sib2 --exclude parent --reference-evidence gvcf \
  --ref GRCh38.fa --bed panel.bed --emit-vcf --outdir comparison
```

`shared` requires two or more `--include` labels and permits exclusions.
`unique` requires at least one include and one exclusion. Every included
sample must carry the exact ALT. With default `--reference-evidence none`,
missing records and explicit non-carriage are treated as assumed absence
for exclusions, not evidence of homozygous reference. Filtered or no-call
records and another carried ALT at that position do not satisfy exclusion.

With `--reference-evidence gvcf`, a primary gVCF supplies its own evidence;
use `--gvcf LABEL=PATH` to supply a separate gVCF for the same selected
sample. The comparison does not search for external companion files.
Only `callable_reference` across the whole REF span qualifies for a strict
exclusion; insertions also require the adjacent reference base where
defined. The [reference confidence rules](variant-confidence.md) apply
(minimum block depth and caller-specific GQ policy). A plain VCF's `0/0`
does not qualify. `not_assessed` (no applicable evidence), `uncovered`,
`low_quality_reference`, `filtered`, `no_call`, `excluded_by_policy`,
`other_alt`, and `variant_evidence` are not callable-reference results.
Primary ALT calls are kept if a companion disagrees; a callable reference
conflict is warned about. gVCF callability is not proof a caller could
detect a particular indel.

## Read the results

- `compare.summary.tsv` and `compare.summary.json` show all unordered
  pairs for `snv`, `indel`, `other_small` and `all`. A shared allele is
  counted even if dosage differs. `gt_compared` requires complete original
  calls of the same ploidy; phase is ignored, and ploidy mismatches are counted
  separately. `gt_concordance` divides equal dosages by compared genotypes.
  Ti/Tv counts apply only to SNVs, and ratios with zero denominators are
  blank in tables and null in JSON.
- `compare.support.tsv` counts alleles by how many labels carried them.
  Identical duplicate observations count once; conflicting duplicates are
  flagged and do not contribute support. A partial genotype that names an
  ALT supports its presence, but is not used for dosage concordance.
- `compare.variants.tsv` (family modes or `--emit-variants`) describes
  each candidate allele's decision and the labels' states and observed
  dosages, including inconclusive rows. Use `--format parquet` to write
  all requested tables as Parquet.
- `--emit-vcf` writes `compare.anchor.vcf.gz` and its CSI index. The first
  callset (first include in family modes) supplies the one sample and its
  allele annotations, for use with `vcf table`. The VCF contains alleles the
  anchor carries (only retained alleles in family modes); calls exclusive
  to other labels are in the tables. `WGSE_COMPARE_STATE` uses label order
  from its INFO header, and `WGSE_COMPARE_DECISION` describes the outcome.
  The anchor GT is a biallelic projection, not a joint genotype.
- `compare.manifest.json` records the input fingerprints and policies,
  counts, outputs, and warnings.
