# Lookup: a genotype, its evidence and what your results say

`wgsextract lookup` answers one question about one sample: what is its
genotype at an rsID, a position or a gene, how sure can we be, and what do
the analyses already run on it say there? It reads local files only and
writes none; nothing is sent over the network.

```console
$ wgsextract lookup --input HG002.vcf.gz --rsid rs429358
$ wgsextract lookup --input HG002.vcf.gz --locus GRCh38:chr19:44908684:T>C
$ wgsextract lookup --input HG002.vcf.gz --gene APOE --json
```

The same lookup is `api.lookup.lookup(api.LookupParams(...))` in the
[typed API](https://wgsextract.github.io/wgsextract-cli/api.html) and the read-only `lookup` operation of
[`wgsextract serve`](server.md): `POST /v1/ops/lookup` over HTTP and the
`lookup` tool over MCP, offered without `--allow-jobs`. All three return the
same `wgsextract.lookup/1` document as the result's `details`.

## Inputs

- `--input`: the sample's VCF or BCF, variants-only or a gVCF. A
  multi-sample file needs `--sample`; the first sample is never assumed.
- Reference evidence: an explicit `--gvcf`, else the input itself when it is
  a gVCF, else the gVCF its run published beside it
  ([reference evidence](reference-evidence.md#commands-that-read-it)).
  Without one, a site the VCF doesn't list is **not assessed**, because a
  variants-only VCF can't show that a base was read as reference; the run
  warns `lookup_no_reference_evidence`.
- `--report`: the sample's [personal report](personal-report.md) JSON, or
  its folder. By default the one report in the input's folder is used; with
  several, none is chosen and the answer lists them. A report found there but
  made from another VCF isn't joined (`report_for_other_input`), nor is any
  report for a multi-sample VCF's second or later sample, because reports
  judge its first (`report_for_other_sample`). A section made on another
  build joins nothing (`build_mismatch`).
- `--build`: only when the input's header doesn't show its build (chr1's
  length or `##reference`). A different `--build`, or a `--locus` on another
  build, is refused: lookup never converts coordinates between builds.

## What to look up

- `--locus BUILD:CHROM:POS`, `BUILD:CHROM:START-END` (a region of at most
  10 Mb) or `BUILD:CHROM:POS:REF>ALT`, for example
  `GRCh38:chr19:44908684:T>C`. Contigs match with or without `chr`.
- `--rsid`: resolved through the packaged dbSNP build 157 identity history
  (a merged ID follows to its current ID; a withdrawn one is refused), then
  placed by the reference library's genome-wide microarray targets
  (`microarray/All_SNPs_<build>_ref.tab.gz`, which
  `wgsextract ref microarray-targets` builds offline from the `ref bootstrap`
  chip templates). They list the sites of consumer genotyping chips, about
  2.3 million rsIDs on GRCh37 and GRCh38, not all of dbSNP: another rsID is
  an error that suggests `--locus`. A missing table is an error naming the
  command that builds it; it is never fetched.
- `--gene`: a symbol or alias in the packaged NCBI RefSeq annotation (the one
  `panel bed` uses, for GRCh37, GRCh38 and CHM13v2.0). Its calls come in pages
  of `--limit` (default 50, at most 1000); `--cursor` takes the previous
  page's `next_cursor`.

An identifier with several matches (an rsID listed at two positions, a merge
history dbSNP calls ambiguous, an alias of several genes, a gene on both chrX
and chrY) is never answered with the first match. The `resolution` has
`status: "ambiguous"` and its `candidates`, the run warns `lookup_ambiguous`,
and `--locus` (beside `--rsid` or `--gene`) or the gene's symbol chooses one.

There is no remote identifier search: no lookup sends anything anywhere.

## The answer

A site's `status`:

| `status` | Meaning |
| --- | --- |
| `confident_call` | A non-reference genotype, FILTER passing, labelled high or moderate confidence. |
| `confident_reference` | Reference: every base `callable_reference` in the gVCF, or an explicit `0/0` call labelled high or moderate. |
| `weak_evidence` | A call or reference below the thresholds: a low-confidence or filtered call, or `low_quality_reference`. |
| `no_call` | The caller recorded a missing genotype (`./.`), or only part of one (`1/.`, evidence state `partial_call`). |
| `not_assessed` | No evidence: no reads (`uncovered`), outside the region the caller assessed, ploidy 0 for this sample, or a variants-only VCF without a gVCF. |

`evidence_state` gives the finer reason: `variant`, `reference_call`,
`filtered`, `no_call`, or a [reference evidence state](reference-evidence.md#evidence-states).
Confidence labels and thresholds are those of
[variant confidence labels](variant-confidence.md) (`--confidence-depth`,
`--confidence-gq`).

Each call lists its genotype as written (`0|1`) and as alleles, its depth,
genotype quality, allele depths, FILTER and IDs, its phase (`phased`,
`phase_set` and, when phased, `haplotypes`: the allele on each copy) and
`variants`: each carried allele in its minimal representation (shared bases
trimmed, one anchor base kept for an indel) with its type. A multiallelic
record keeps every allele. Labels use the caller's recorded ploidy policy
(a haploid chrX call's halved depth thresholds) and, in a multi-sample VCF,
only the chosen sample's own counts. A reference site's genotype is the
gVCF block's own (`0/0`, or `0` where the caller called one copy); filtered
or no-call evidence gives none. A `--locus` with `REF>ALT` adds
`query_allele_copies`: the copies of that allele, 0 at confident reference,
and none when the evidence can't say. With `--rsid` or `--gene`, an
allele-qualified `--locus` keeps all of its REF's bases.

A region (`--gene`, or a `--locus` range) has its calls and `call_states`,
its bases per evidence state (`evidence.bases`, with the first non-callable
spans) and `phase_sets`: the phased calls of each phase set, with the alleles
on each copy in order.

## What the results say

`interpretation` joins the personal report and attributes it (`report.path`,
`generated`, version and disclaimer). It copies, unchanged, the findings of
the variant and gene sections (`clinvar`, `acmg_research`,
`pharmacogenomics`, `carrier_screening`, `structural_variants`) at the site
(`match: "site"`; a structural variant matches across its recorded
interval) or about the gene (`match: "gene"`), each with its own
confidence label, reasons, evidence file and disclaimer. Lookup adds no
classification of its own. A site lookup also matches findings without a
position (a diplotype, a copy number) about the genes at the site, but not
the gene's other variants.

Each section is listed with its `status`. A section that was `not_run` lists
the commands that would run it (`missing`), so "no finding" is never taken
for "not looked at"; `limitations` says what an empty section doesn't mean.
Without a report the answer says how to make one (`wgsextract report
--input ...`). A report older than the input warns
`lookup_report_older_than_input`.

A server job reads only inside its allowed folders: a report or a
manifest's gVCF found beside the input but outside them is skipped.

## Provenance

`resources` names every resource used: the dbSNP history's release and
terms, the rsID placements file, and the gene annotation's release, licence
and source. The result manifest lists the input, gVCF, report and placements
as inputs, with their fingerprints. Results stay on this computer.

## Measured

HG002 (Oxford Nanopore, GRCh38), `vcf snp --reference-evidence gvcf` on
chr20:10.0-10.5 Mb, looking up each of the 618 chip rsIDs placed there
against GIAB v4.2.1:

| GIAB | `confident_call` | `confident_reference` | `weak_evidence` |
| --- | --- | --- | --- |
| variant (239) | 237, all the same genotype | 1 (a SNV bcftools missed at 25x) | 1 |
| reference, confident region (373) | 0 | 371 | 2 |

Six sites outside GIAB's confident regions aren't counted. With GIAB's
variants-only truth VCF as the input, its sites without a variant were
`not_assessed`, not reference. A lookup took about 0.4 s.
