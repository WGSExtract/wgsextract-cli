# Local cohort summaries

`wgsextract cohort summarize` counts alleles across multiple local genomes for
research. It does not call variants, run QC, adjust for ancestry, test for
association, or contact a service. The Python equivalent is
`api.cohort.summarize(api.CohortSummarizeParams(...))`. Neither HTTP nor MCP
offers it: the cohort manifest itself names files outside the request schema.

## Bind each genome to its evidence

Create a JSON file with schema `wgsextract.cohort/1`. A cohort needs at least
two distinct participants. Give each an ID used only for the local join, the
sample name in the two VCF headers, an original variant VCF or BCF, and its
matching gVCF or all-sites VCF. Keep unsplit genotype records: a carried-only
variant table cannot recover the denominator. Choose artifacts from the same
calling run and participant. The command detects repeated artifact/sample
bindings and IDs; it cannot establish biological identity or consent.

```json
{
  "schema": "wgsextract.cohort/1",
  "assembly": "GRCh38",
  "caller": "deepvariant",
  "reference": {"path": "reference.fa", "sha256": "<64 hex digits>"},
  "reference_index": {"path": "reference.fa.fai", "sha256": "<64 hex digits>"},
  "participants": [
    {
      "id": "P01",
      "sample": "sample-a",
      "variants": {"path": "a.vcf.gz", "sha256": "<64 hex digits>"},
      "evidence": {"path": "a.g.vcf.gz", "sha256": "<64 hex digits>"}
    },
    {
      "id": "P02",
      "sample": "sample-b",
      "variants": {"path": "b.vcf.gz", "sha256": "<64 hex digits>"},
      "evidence": {"path": "b.g.vcf.gz", "sha256": "<64 hex digits>"},
      "ancestry": {"path": "b.ancestry.manifest.json", "sha256": "<64 hex digits>"}
    }
  ]
}
```

Paths are absolute or relative to the manifest's directory. Replace each
placeholder with the file's SHA-256 (for example, from `wgsextract sign create`).
Use a plain FASTA with its existing `samtools faidx` index. The assembly must
be recognized (GRCh37, GRCh38, CHM13v2, or a supported alias) and agree with
the VCF headers and contig dictionaries. All participants must use one
caller: `bcftools`, `deepvariant`, `gatk`, or `clair3`. Header evidence must
agree when it names the caller. The command verifies each file's SHA-256
and checks REF against the FASTA during normalization. It does not change
inputs, infer a liftover, or download missing data.

An optional `ancestry` binding points to the result manifest from [`qc
ancestry`](ancestry.md) for that variant input. Its input fingerprint,
assembly, panel version and fingerprint, and PC1–10 and site overlap must
be verifiable. Mixed panels fail. Missing ancestry remains not assessed; it
is not a QC pass. No ancestry or QC threshold excludes participants.
For contamination, give both `alignment` (the original BAM/CRAM) and
`contamination` (the `qc contamination` result manifest) in the participant's
entry. For relatedness and sex, give a top-level `relatedness` object with
`result`, `samples`, and `pairs` artifact bindings from the same `qc relate`
run; use its TSV outputs. Match input paths and sample names to the
participants. An identified same-person pair fails; other flags and
uncertainty are preserved privately, not used as exclusion criteria.

## Run and interpret

```bash
wgsextract cohort summarize cohort.json --region chr22 --outdir summary
wgsextract cohort summarize cohort.json --bed targets.bed --format parquet --outdir summary
```

Without a scope all autosomes are included. `--region` takes 1-based
inclusive intervals; `--bed` takes 0-based half-open intervals. Both accept
`chr1` and `1` for the same autosome. Output coordinates follow FASTA names
and order. The supported alleles are autosomal SNVs, not indels, MNVs, or
sex/PAR variants. A multi-allelic record retains its GT and ALT mapping.
An uncarried indel ALT cannot hide a padded SNV; a genotype carrying an
unsupported allele remains uncallable for overlapping SNVs. Separately
represented conflicting calls cannot be reconciled. The summary does not
copy file-specific annotation or provenance strings into the allele table.
It stages normalized BCFs and one chromosome of text records per input in
the temporary workload directory. It reads those records in small buffers
without running a process per participant during the merge.

The table (`cohort.variants.tsv` or `.parquet`) has one row per normalized
`chrom,pos,ref,alt` with an observed ALT genotype, even if that genotype did
not pass the call checks.

| Column | Meaning |
| --- | --- |
| `ac`, `an`, `af` | Copies of this ALT, total eligible called chromosomes at the site, and `ac/an` (blank/null when `an=0`). |
| `n_het`, `n_hom_alt`, `n_hemi` | Diploid one-copy, diploid two-copy, and explicitly haploid ALT calls. |
| `callable_samples` | Participants contributing alleles to `an`. |
| `reference_called_samples` | Callable participants whose entire genotype is reference. |
| `not_assessed_samples` | Participants without a record or a gVCF scope declaration covering the site. |
| `uncallable_samples` | Other participants that did not contribute alleles, such as covered no-calls, low-quality records, or gaps inside the assessed interval. |

A `1/2` genotype contributes one copy to each ALT with two called
chromosomes. Calls carrying only another ALT count toward `an`, not `ac` or
`reference_called_samples`. Do not sum `an` across ALT rows. An explicit
call needs complete GT, passing or unset FILTER and FORMAT/FT, FORMAT/DP
of at least `--min-depth` (default 10, halved for explicit haploid GT), and
FORMAT/GQ of at least `--min-gq` (default 20) when the evidence model uses GQ.
An absent VCF call counts as two reference alleles only with passing,
diploid hom-ref evidence over the SNV. Blocks use MIN_DP rather than
average depth. The caller's [reference evidence classifier](reference-evidence.md)
accounts for assessed intervals, exclusions and overlapping records. A
rejected explicit call never falls back to a reference fill.

## Keep the results private

The [result manifest](result-manifest.md), table and Parquet metadata omit
participant IDs, sample names, source paths and fingerprints. For a private
per-person join, add `--include-identifiers` to write
`cohort.participants.private.json`; this exposes source file paths, names
and, when available, PCs. A later run without that option does not remove an
earlier private file. Choose an output directory without sensitive names and
keep inputs, temporary work and outputs on a local, access-controlled disk,
outside cloud sync and network mounts. Even without identifiers, rare
alleles and exact counts can reveal membership: these data are not anonymous
or approved for sharing.
