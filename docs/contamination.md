# DNA contamination (`qc contamination`)

A BAM or CRAM can hold reads from more than one person: a mixed-up or
contaminated sample at the lab, or two runs merged by mistake. Nothing fails,
but another person's reads turn homozygous sites heterozygous, so genotypes,
microarray exports, haplogroups and reports can be wrong without any sign.
`lineage mt-haplogroup --contamination` (haplocheck) only sees mtDNA.

```sh
wgsextract qc contamination --input sample.cram
wgsextract qc contamination --input sample.bam --sites 10k   # faster, noisier
```

## How it works

[VerifyBamID2](https://github.com/Griffan/VerifyBamID) (Zhang et al. 2020,
*Genome Research* 30:185) counts the reads' alleles at common SNPs and fits
them to a mixture of two people whose allele frequencies come from the
principal components of a reference panel. It needs no genotypes of the
sample, and it works when the sample and the contaminant have different
ancestries. Its **FREEMIX** is the estimated fraction of the reads from the
second person.

The panels are the 1000 Genomes phase 3 SVD resources that ship in
VerifyBamID2's Bioconda package: 100,000 sites (default) or 10,000 sites
(`--sites 10k`), for GRCh37/hg19 and GRCh38. Contig names (`chr1` or `1`)
are matched to the input. The Bioconda package is checksummed in `pixi.lock`;
WGS Extract downloads or redistributes nothing itself. VerifyBamID2 is MIT
licensed, and the 1000 Genomes data are freely usable.

**Builds.** T2T-CHM13 and other builds have no bundled panel, so the command
stops with a message saying so. Build a panel from a multi-sample VCF on that
build (`verifybamid2 --RefVCF panel.vcf.gz --Reference ref.fa`, at least 1,000
samples) and pass its prefix with `--svd-prefix`.

**Platforms.** Linux and macOS: `pixi install -e verifybamid2`. Bioconda has
no Windows build, so `qc contamination` is not available on native Windows
(use WSL2).

## Outputs

- `<stem>.contamination.selfSM`: VerifyBamID2's report (FREEMIX, reads, mean
  depth).
- `<stem>.contamination.Ancestry`: the sample's (and the contaminant's)
  position on the panel's first principal components.
- The result manifest's metrics: `freemix`, `freemix_sites` (panel sites with
  reads), `freemix_reads`, `freemix_depth`, `freemix_confidence`,
  `svd_panel` and `ancestry_pc1`, `ancestry_pc2`.
- In a [sample workspace](sample-workspace.md), FREEMIX is recorded as the
  `contamination` fact of the alignment, with `high` confidence from 5,000
  covered sites at 4x or more, `medium` with fewer, and `low` below 1,000 or
  when VerifyBamID2's marker check had to be skipped.

A region subset (such as one chromosome) covers too few sites for
VerifyBamID2's marker check. The check is then skipped, the estimate is still
made, and a `contamination_few_sites` warning says it is rough.

## What uses it

When FREEMIX is at or above `qc_max_contamination` (default 0.03, in
`config.toml`; 0 turns this off):

- `qc contamination` itself warns `sample_contamination`;
- `microarray`, the `vcf` callers and the haplogroup commands warn
  `sample_contamination` before writing genotypes from that alignment
  ([genotype QC](genotype-qc.md));
- [confidence labels](variant-confidence.md) of rare and clinical findings cap
  every call at `moderate`, with reason `sample_contaminated`, for VCFs in the
  same genome folder;
- the [personal report](personal-report.md) shows the estimate in the
  overview, warns there, and caps at `moderate` the haplogroups of runs that warned. Without
  an estimate it lists `qc contamination` among the missing steps.

Pipelines draw the line between 2% and 5%; gnomAD removed samples above 5%.
3% is a middle choice: clean 30x genomes measure well below 1% (see below),
so a value above it is very unlikely to be noise.

## Validation

GIAB NovaSeq 30x GRCh38 BAMs (Google brain-genomics-public), chr20 only,
VerifyBamID2 2.0.3, 100k-site panel. Mixtures keep 95% or 90% of HG002's
reads and add 5% or 10% of the other sample's, in one read group
(`samtools view --subsample`, `merge`, `addreplacerg`). Evidence in
`out/616/` of the branch's run.

| Input | Expected | FREEMIX | Sites | Mean depth |
| --- | --- | --- | --- | --- |
| HG002 | 0 | 0.0050 | 2,613 | 30.2x |
| HG001 (NA12878) | 0 | 0.0085 | 2,612 | 29.7x |
| HG005 | 0 | 0.0093 | 2,613 | 30.2x |
| 95% HG002 + 5% HG001 | 0.05 | 0.0544 | 2,613 | 30.2x |
| 90% HG002 + 10% HG001 | 0.10 | 0.1023 | 2,613 | 30.1x |
| 95% HG002 + 5% HG005 | 0.05 | 0.0544 | 2,613 | 30.2x |
| 90% HG002 + 10% HG005 | 0.10 | 0.1033 | 2,613 | 30.2x |

Each chr20 run took about 12 seconds on 4 threads. The whole HG002 30x BAM
(43.7 GB):

| Panel | FREEMIX | Sites | Mean depth | Time (4 threads) |
| --- | --- | --- | --- | --- |
| 100k | 0.0058 | 99,972 | 29.3x | 3 min 15 s |
| 10k | 0.0065 | 9,998 | 29.2x | 32 s |

The tests build a 5,000-site panel of 1,000 synthetic people on a random
reference and measure a 10% mixture of two of them (`tests/test_contamination.py`);
on it a clean sample reads 0.0 and 5% and 10% mixtures 0.048 and 0.097.
