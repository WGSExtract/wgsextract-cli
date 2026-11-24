# Exon callability (`vcf callability`)

A clinical findings list is silent about most genes, and silence can mean
three different things:

- **not reported**: the assay read every coding base of the gene and found
  nothing to report;
- **not readable**: the assay could not read some of it, so a variant there
  would have been missed;
- **not run**: no analysis looked at it.

Gene-body coverage can't tell these apart. A gene with reads over 90% of its
body can still hide a whole coding exon nobody read, and 90% of a body that is
mostly intron says little about its exons. `vcf callability` therefore
classifies every coding exon of a gene list, base by base, from the sample's
[reference evidence](reference-evidence.md) (a gVCF), and calls a gene
callable only when every one of its coding exons is. The
[personal report](personal-report.md) combines the result with the ClinVar
section.

> **Callability is technical, not clinical.** It says whether this sequencing
> run read the bases. A callable exon with nothing reported does not rule out
> disease, and no gene is "negative" while an exon, a paralog region or a
> variant class (below) remains unassessed.

```sh
wgsextract vcf snp --input sample.cram --reference-evidence gvcf
wgsextract vcf callability --vcf-input snps.vcf.gz
wgsextract vcf clinvar --vcf-input bcftools.g.vcf.gz --ref reference.fa
wgsextract report --input sample.cram
```

## Inputs

- **A single-sample gVCF.** `--vcf-input` is a gVCF, or a VCF whose caller also wrote one
  (`--reference-evidence gvcf` with `vcf snp`, `vcf indel`, `vcf gatk`,
  `vcf deepvariant` or `vcf clair3`), found through its result manifest. A VCF
  without one stops with the command that makes it: a variant-only VCF can't
  tell a confident reference base from one nobody read. Both files must name
  exactly one sample and agree on its name. The caller's
  `--region` limits what was assessed; exons outside it are `not_run`.
- **A gene list.** `--gene-list` takes the report's lists (default
  `acmg-sf-3.3`, or config `report_gene_list`) or a TSV/BED of symbols. Only
  the symbols are used, so nothing is downloaded.
- **An exon model.** Bundled for hg38 and hg19 (below); `--exons` gives
  another (format below). Genes the model lacks are `not_run`
  (`no_exon_model`).

## Bundled exon models

`assets/report/exons_hg38.tsv.gz` and `exons_hg19.tsv.gz` list, for each gene
of the built-in lists (ACMG SF v3.3 and v3.2, 84 genes, 1,852 exons), every
exon of one transcript, numbered in transcript order, with its coding part:

| Build | Transcript | Source |
| --- | --- | --- |
| hg38 | MANE Select | NCBI RefSeq annotation GCF_000001405.40-RS_2025_08 (GRCh38.p14) |
| hg19 | RefSeq Select | NCBI RefSeq annotation release 105.20220307 (GRCh37.p13) |

These are the transcripts the report's gene spans use. Each row also lists
the known short-read limitations within 50 bp of the exon, from the GIAB
genome stratifications v3.6:

| Limitation | GIAB file | Meaning |
| --- | --- | --- |
| `low_mappability` | `lowmappabilityall` | 100 bp reads (up to 2 mismatches and 1 indel) or 250 bp reads also match elsewhere |
| `segmental_duplication` | `segdups` | inside a copy of over 1 kb at 90% identity or more elsewhere |

`scripts/build_report_exons.py` builds both files from the pinned sources
(each checked against its SHA-256; the file headers record the URLs and
checksums). NCBI RefSeq annotation is public domain; the GIAB stratifications
are NIST data freely available for reuse. The files hold coordinates computed
from those sources and nothing else: no interpretation, classification or
gene-list table from another tool is copied. The gene symbols are the
ACMG SF lists' (facts, cited in the report).

## States

Each coding exon is assessed over its CDS plus `--flank` intronic bases on
each side (default 2, the splice-site dinucleotides; up to 50). Each base gets
the gVCF's [evidence state](reference-evidence.md#evidence-states) with the
`confidence_*` thresholds (`--confidence-depth`, `--confidence-gq`). A base is
**read** when it is `callable_reference` or inside a small-variant call that
passes the depth threshold and, when the evidence model supports it, the
genotype-quality threshold, with all carried alleles resolved to sequence.
A carried call that fails these checks is `low_quality_variant`, not readable.
Bases in known short-read limitations are not counted as read.

| Exon status | Meaning |
| --- | --- |
| `callable` | every base read |
| `partial` | some bases read, others not |
| `not_readable` | no base read: no reads (`uncovered`), too few or too poor (`low_quality_reference`), or failed calls (`filtered`, `no_call`) |
| `specialist_required` | overlaps a short-read limitation: short reads can't place reads there uniquely, whatever the gVCF says, so a paralog-aware method (long reads, a gene-specific assay) is needed |
| `not_run` | the caller did not assess these bases (`not_assessed`) |
| `not_applicable` | ploidy 0 for this sample (`excluded_by_policy`, such as chrY in a woman); left out of every count |

An exon's `reasons` list the states and limitations of its unread bases.

| Gene state | Meaning |
| --- | --- |
| `callable` | every coding exon callable |
| `partial` | some coding exons not callable |
| `not_readable` | no coding exon callable or partial |
| `not_run` | no exon model, or no coding exon assessed |

In the report, each gene's **result** combines its state with the ClinVar
section:

| Result | Meaning |
| --- | --- |
| `reported` | the ClinVar section lists a consensus P/LP call in the gene (with its confidence) |
| `not_reported` | no call, and every coding exon was read |
| `not_readable` | no call, but not every coding exon was read |
| `not_run` | `vcf clinvar` has not run, the gene is not in its gene list, or the gene was not assessed; also every gene without a call when the two runs can't be combined (warning `clinvar_not_combined`). Membership is by the report's own gene list symbols |

Run `vcf clinvar` on the assessed gVCF itself. Annotating a variant-only
companion such as `snps.vcf.gz` can omit indels. The report compares the
gVCF fingerprints recorded by both runs and suppresses `not_reported` if
they don't match, the builds differ, or ClinVar findings could not all be
placed in genes.

## What a callable exon does not cover

A callable gene still leaves these unassessed (the JSON's
`unassessed_variant_classes`):

- exon or gene deletions and duplications and other structural variants
  (`vcf sv`, `vcf cnv`);
- repeat expansions longer than a read (`typing str`);
- promoters, untranslated regions and deep intronic variants;
- exons only in other transcripts;
- mosaic and low-fraction variants;
- paralog regions (`specialist_required` exons).

## Builds

The gVCF's `##contig` lengths must match the exon model's (`#contig` lines):
a bundled model is chosen by them, and a mismatch (another build, such as
T2T, or a model for another build) stops the run, naming the contigs that
differ. Contig names may differ by `chr` (and M/MT).

## Outputs

- `exon_callability.json` (role `exon_callability`, schema
  `wgsextract.exon_callability/1`): the gene list, the exon model (file,
  SHA-256, transcripts and source lines), the reference identity (build, the
  contigs checked, the FASTA's name), the evidence (the gVCF, relative to the
  document; engine and version, quality model, minimum mapping quality,
  assessed region), the thresholds, counts, and per gene its `state`,
  `reasons`, exons per status, the exon numbers not callable (most severe
  status first), and its assessed, read and limited bases.
- `exon_callability.tsv` (role `exon_callability_table`): one row per coding
  exon with its status, reasons, assessed region, bases per evidence state,
  read and limited bases, and limitation intervals.

The result manifest records the gVCF as an input (`vcf_input` when given
directly, `reference_evidence` when found as a companion), the
metrics `genes_<state>` and `exons_<status>`, and the warnings
`exons_not_callable` and `genes_without_exon_model`.

## Exon model format

A TSV (optionally gzipped) with `#contig<TAB>name<TAB>length` lines for every
contig it uses, other `#` lines as its source notes, and the columns `gene`,
`transcript`, `tag`, `strand`, `exon`, `exons`, `chrom`, `start`, `end`,
`cds_start`, `cds_end` (1-based, inclusive, in order and within the exon;
both CDS columns empty for an untranslated exon) and `limitations`
(`kind:start-end` items separated by `;`, may be empty).
Each gene's rows must be one transcript's exons, numbered
1 to its `exons` count, each once; a model missing an exon is refused.

## Measured on HG002

HG002 (35x Illumina NovaSeq PCR-free, GRCh38 no-alt), `vcf snp
--reference-evidence gvcf` over the ACMG SF v3.3 coding exons (±50 bp, 532 kb,
20 s on 3 threads), then `vcf callability` (under a second), defaults:

| | Genes | Coding exons |
| --- | --- | --- |
| callable | 70 | 1,735 |
| partial | 14 | |
| specialist required | | 88 |
| not readable, not run | 0 | 0 |

Every one of the 84 genes passes a gene-body rule: at least 90% of each body
has reads (and at least 90% has 10 or more reads at MAPQ 20). Exon by exon,
14 genes have coding exons short reads can't place uniquely, among them the
known paralog regions: PMS2 exons 1-5, 9 and 11-15 (PMS2CL and the PMS2P
pseudogenes), ABCD1 exons 7-10, PTEN exons 5, 8 and 9 (PTENP1), MYH7 (MYH6)
and the TTN exons 172-198. The gVCF alone called 14,097 of those exons'
14,429 bases confident reference; all 328 bases it called low-quality were in
them.
With ClinVar (release of 2026-11-15) the report lists MSH2 as reported (one
low-confidence call), 69 genes as not reported and the 14 as not readable.
