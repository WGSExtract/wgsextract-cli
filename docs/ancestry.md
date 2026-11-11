# Ancestry proportions (`qc ancestry`)

`wgsextract qc ancestry` estimates what share of a genome is best matched by
each of a set of reference components, the way ADMIXTURE does. There are two
views:

- **Continental (K=7):** Sub-Saharan African, European, Middle Eastern,
  Central and South Asian, East Asian, Oceanian and Indigenous American.
- **Sub-continental (K=12):** the continental components split into, for
  example, West African (Senegambia) and West-Central and East African, or
  Northern European and Middle Eastern and Mediterranean.

```sh
wgsextract qc ancestry --input me.cram
wgsextract qc ancestry --input me.vcf.gz
wgsextract qc ancestry --input 23andme_raw.txt
```

**These are proportions of reference populations, not an ethnicity.** A
component is a set of allele frequencies learned from the reference samples.
It is named after the reference groups that carry it most, but many
present-day populations carry it. For example, the reference Europeans carry
on average about a quarter "Middle Eastern" component, which reflects shared
ancient ancestry, not recent migration. Groups missing from the reference
(see below) are described as mixtures of the components that are there.

## Reference

The components were learned with
[ADAMIXTURE](https://github.com/AI-sandbox/adamixture) 1.9.3 (BSD-3) from
3,129 unrelated samples of the HGDP and 1000 Genomes in gnomAD v3.1
([Koenig et al. 2024](https://doi.org/10.1101/gr.278378.123)). The samples
come from 80 populations in Africa, Europe, the Middle East, Central and South
Asia, East Asia, Oceania and the Americas, including admixed American
populations. gnomAD's LD-pruned set of 187,417 common SNPs is used. gnomAD
releases these data under CC0 1.0.

The reference is one file (about 16 MB). It holds the sites, the component
allele frequencies for K=7 and K=12, and a PCA of the reference samples, which
polygenic scores will reuse. It is downloaded from this project's GitHub
release `ancestry-ref-1` into `<library>/ancestry/` on first use, or with
`wgsextract ref ancestry --install`. Its SHA-256 is pinned and checked before
every use. `ref ancestry --delete` removes it. It covers GRCh38, GRCh37/hg19
(186,306 of the sites lift over) and T2T-CHM13 (140,020).
`scripts/ancestry_reference/` rebuilds it from gnomAD.

## Inputs

| Input | Genotypes | Notes |
| :--- | :--- | :--- |
| BAM/CRAM | read counts at the sites (`bcftools mpileup`, MAPQ and base quality 20), as genotype likelihoods | Low coverage gives wider intervals, not biased proportions. Needs its index, and the reference FASTA for a CRAM. |
| VCF | the sample's calls (REF and the site's ALT; filtered or other-allele calls are unknown) | `--sample` picks one sample of a multi-sample VCF. gVCFs are refused. |
| Microarray raw data | the genotypes at the chip's markers that are reference sites | 23andMe, AncestryDNA, FTDNA, MyHeritage and similar layouts (`.txt`, `.csv`, `.zip`, `.gz`). The build is found from the positions. |

A variants-only VCF omits hom-ref sites. For a whole-genome call set, a
missing site is hom-ref, and it is filled in as `qc relate` does (see
[relate.md](relate.md)). A variants-only VCF of part of the genome (one
chromosome, an exome, a panel) is refused: from its variants alone the
proportions would be biased. An all-sites VCF's missing sites stay unknown.

`--build` gives the input's build when its header doesn't tell it.

## Outputs

| File | Contents |
| :--- | :--- |
| `<stem>.ancestry.tsv` | For K=7 and K=12, one row per component: name, region, proportion, 95% interval and the reference populations that carry it most |
| `<stem>.ancestry.manifest.json` | The [result manifest](result-manifest.md). Metrics: `ancestry_k7_<component>` and `ancestry_k12_<component>`, each with `_lower`/`_upper`; `ancestry_sites`, `ancestry_sites_available`, `ancestry_depth`, `ancestry_build`, `ancestry_confidence` |

The 95% intervals come from a block bootstrap over the sites
(`--bootstrap N` replicates, default 100; 0 skips them). Blocks of 500
neighbouring sites are resampled, so linkage between nearby sites widens the
intervals as it should.

The K=7 proportions of at least 0.5% are recorded in the
[sample workspace](sample-workspace.md) as the `ancestry` fact, and the
[personal report](personal-report.md) shows both views.

## Accuracy

PHASE0_ACCURACY

## Warnings and errors

| Code | Meaning |
| :--- | :--- |
| `ancestry_few_sites` | Fewer than 10,000 sites have data: the proportions are rough (see their intervals) |

Fewer than 2,000 sites with data is an error: a region subset, or a file
from another species or build.

## Compared with `qc relate`

`qc relate` ([relate.md](relate.md)) places a sample in one of the five 1000
Genomes superpopulations with somalier. That is a classification, and it
can't describe admixture. `qc ancestry` gives proportions, uses about ten
times as many sites and has more reference populations (HGDP adds the Middle
East, Oceania, Central Asia and Indigenous Americans).
