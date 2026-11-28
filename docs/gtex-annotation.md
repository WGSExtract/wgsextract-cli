# Offline GTEx eQTL annotation

Install the opt-in GRCh38 track, then annotate a VCF or BCF:

```sh
wgsextract ref gtex --download
wgsextract vcf gtex --input sample.vcf.gz --outdir annotations
```

`vcf gtex` writes `gtex_eqtl.tsv` and leaves the input unchanged. To match
the final variants from a chain, include `gtex` by name, for example
`vcf chain-annotate --input sample.vcf.gz --annotations regulatory,gtex`.
GTEx is not in the default chain. The chain's `chain_gtex_eqtl.tsv` reflects
any region selection and other annotation steps. A rerun without `gtex`
leaves the old sidecar untouched; it may no longer describe the final VCF.
A GTEx rebuild replaces the TSV only after it has been written successfully,
and the chain rejects a sidecar path that aliases a configured input.
The chain does not add GTEx INFO fields
to its VCF or GTEx columns to `--to-table` output. The step journal tracks the
VCF and sidecar together; use `--force-step gtex` to regenerate the pair.

The VCF header must identify GRCh38 through a build declaration, known
primary-contig lengths or versioned accessions. Conflicting build evidence
is rejected. Matching uses position, REF and each literal ALT, with
case-insensitive bases and known primary chromosome aliases. It does not
normalize variants, lift coordinates, reverse-complement alleles or swap
REF and ALT. Symbolic alleles, breakends and spanning deletions do not
match. A reference FASTA, VCF index and sample genotypes are not needed
for the standalone command.

## What the sidecar contains

Each row pairs an input ALT with one published gene, tissue and credible
set association. `chrom` uses the input's spelling. `pos` and `alt_index`
are one-based; the latter is the position in the original ALT list.
`gene_id` is the release's versioned Ensembl phenotype identifier,
`gene_name` is the published label and `tissue` is the GTEx tissue ID.
`credible_set` is the published `cs_id`, scoped to the gene and tissue.

`pip` is SuSiE's posterior inclusion probability for the variant.
`afc` is the published signed allelic fold-change estimate for the matched
ALT, in GTEx's reported scale. It is not flipped or inferred, and is blank
when not reported. A no-match result has only the column header. The
manifest records match counts, source URL, archive and database SHA-256
checksums, release and attribution.

These are research annotations from a fine-mapped summary. They are not a
list of all significant cis-eQTL pairs and do not establish an effect in
the person whose VCF was annotated, causality or ACMG evidence.

## Significant variant–gene pairs

Select one GTEx tissue ID with `--pairs TISSUE` instead of using SuSiE:

```sh
wgsextract ref gtex --pairs Whole_Blood --download
wgsextract vcf gtex --input sample.vcf.gz --pairs Whole_Blood --outdir annotations
wgsextract vcf chain-annotate --input sample.vcf.gz --annotations gtex --pairs Whole_Blood
```

The standalone command writes `gtex_eqtl_pairs.tsv`; the chain writes
`chain_gtex_eqtl_pairs.tsv` for the final variants. The same GRCh38 and
exact-ALT rules apply. Omit `--pairs` to use the original SuSiE track. Use
different output directories to retain results for multiple tissues. The
chain does not update a sidecar for a mode that was not selected.

Each row gives a versioned `gene_id`, `gene_name`, `tissue`, nominal
`pval_nominal`, signed regression `slope` for the matched ALT, and
`slope_se`. `pval_nominal_threshold` comes from the same gene and tissue in
the release's eGenes table. `passes_threshold` is `1` when the nominal p
is at or below that threshold, and `0` otherwise. This threshold is derived
from permutations for that gene, not a universal nominal p-value cutoff.
Only published significant-pair records are queried. No hit does not
establish that a variant has no regulatory effect.

`--download` for a missing tissue fetches the full 2.56 GB public
[GTEx v10 eQTL archive](https://storage.googleapis.com/adult-gtex/bulk-qtl/v10/single-tissue-cis-qtl/GTEx_Analysis_v10_eQTL.tar)
from Google Cloud Storage, accessed 2026-11-28 and pinned by SHA-256.
GTEx provides the significant-pair files in a single archive, not as
separate tissue downloads. The installer uses only the requested tissue's
Parquet and gzipped eGenes files and keeps only that tissue's SQLite track
and receipt. Allow at least 4 GB of free disk space for installation. Run
`wgsextract ref gtex --pairs Whole_Blood` to verify the installed tissue
without downloading. These v10 data are not the current GTEx release.

## Installation, privacy and terms

`--download` requests the public
[GTEx v10 SuSiE eQTL archive](https://storage.googleapis.com/adult-gtex/bulk-qtl/v10/susie-qtl/GTEx_v10_SuSiE_eQTL.tar)
from Google Cloud Storage (174 MB, 50 tissue tables). The archive is pinned
to SHA-256 `294891d94fa04556963fb67434ca33bf5f92a236d4f05ea208c3d9b4b13f211f`.
There is no transfer of sample data and no controlled-access GTEx data
are used. Without `--download`, `ref gtex` verifies the installed database
against its JSON checksum receipt. Annotation verifies that checksum too.
`ref status` reports whether the SuSiE track is present, and `ref needs`
shows how to install the selected GTEx track. Annotation never fetches
a missing or damaged track.

Installation reads Parquet with the optional `pyarrow` dependency
(`wgsextract-cli[parquet]` or `pixi run -e parquet`). The installer does not
install dependencies. Allow about 2 GB of free disk space in the reference
library for the SuSiE download and SQLite database. Annotation uses
`bcftools` and the local database, not `pyarrow`.

Credit the [Genotype-Tissue Expression (GTEx) Project](https://www.gtexportal.org/)
and cite the GTEx Consortium, *The GTEx Consortium atlas of genetic
regulatory effects across human tissues* ([Science, 2020](https://doi.org/10.1126/science.aaz1776)).
See the [GTEx Portal data release policy](https://www.gtexportal.org/home/license)
for attribution and redistribution conditions, including disclosure when
redistributing an older release. This track is pinned to v10; it is not the latest
GTEx release. WGS Extract does not bundle or mirror the archive.
