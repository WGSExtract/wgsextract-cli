# Research panels

A **research panel** is a file that says which genes, sites and allele
combinations you want to examine, and which filters a variant must pass to
count. `wgsextract panel check` validates one and resolves its genes.
`panel audit --research-panel` applies it to a genome
([#1287, declarative research panel audit](https://github.com/WGSExtract/wgsextract-cli/issues/1287)).

> **Research use only; not a diagnosis.** A panel records its author's
> hypotheses. A variant that "qualifies" only passes the panel's filters;
> nothing in a panel check or audit says that it causes or predicts any
> condition. Every output carries `research_only: true` and the
> `panel_research_only` warning. Don't act on panel results without a clinical
> genetics professional.

Panels stay on your computer: checking one reads only the file and the gene
annotations bundled with the CLI, and contacts no server. WGS Extract ships no
clinical panels. If you build one from PanelApp, PharmVar or ClinPGx data,
their terms (including CC BY-SA share-alike) apply to your file.

## Check a panel

```console
$ wgsextract panel check --panel my_panel.toml
Example research panel (example-panel 0.1.0): a valid wgsextract.panel/1 research panel.
SHA-256: 9ac1…
Genes resolved in hg38 (NCBI RefSeq GCF_000001405.40-RS_2025_08):
  group_a: 6 of 6 genes, 10,776 coding bp
  group_b: 3 of 4 genes, 4,083 coding bp; not resolved: NOT_A_GENE
```

- Genes resolve in the panel's `build`, else hg38; `--build hg19` or
  `--build t2t` resolves them in another build.
- An invalid panel exits 1 with error code `panel_invalid` and lists every
  problem with its location (below). `--json` (or `api.panel.check`) gives
  the same list in `details.problems`.
- A valid panel exits 0. `details` holds the panel's id, version and SHA-256;
  each gene's resolution, HGNC ID, spans and coding length; each group's
  effective filters; and the calibration settings.
- The check writes no files.

### Warnings

| Code | Meaning |
| --- | --- |
| `panel_research_only` | Always: the research-only disclaimer. |
| `panel_gene_unresolved` | A name is no gene in the bundled annotation (with close symbols when there are some). The audit leaves it out. |
| `panel_gene_ambiguous` | A synonym of several genes. Give the symbol. |
| `panel_gene_alias` | A synonym of one gene, which is used instead. |
| `panel_gene_noncoding` | The gene has no coding sequence, so coding filters can't qualify its variants. |
| `panel_haplotype_pharmcat_gene` | A haplotype watches a gene whose star alleles PharmCAT calls. The watch is allowed, but `wgsextract vcf pharmcat` handles that gene's exceptions and copy-number alleles; this watch only matches the panel's combinations. |
| `panel_build_differs` | `--build` differs from the panel's build, so its sites and haplotypes can't be audited on that build. |

## The format: `wgsextract.panel/1`

A panel is TOML (`.toml`) or the same structure in JSON (`.json`). Both are
checked against one schema,
[`wgsextract.panel.1.schema.json`](../src/wgsextract_cli/assets/schemas/wgsextract.panel.1.schema.json),
which editors can use for completion. Every key is checked; an unknown key is
an error, never ignored. No key holds an expression.

```toml
schema = "wgsextract.panel/1"
id = "example-panel"                 # lower-case letters, digits and '-'
name = "Example research panel"
version = "0.1.0"                    # your version; outputs also record the file's SHA-256
build = "GRCh38"                     # coordinates of sites and haplotypes: GRCh38, GRCh37 or CHM13v2.0

[source]                             # free text, copied into outputs, never read
citation = "Synthetic example"

[defaults]                           # filters for every group; a group may override any
max_af = 0.003                       # gnomAD allele frequency, as a fraction (not a percentage)
max_popmax_af = 0.01
pass_only = true                     # default true
min_confidence = "moderate"          # high, moderate or low
missing_scores = "unscored"          # unscored (default) or drop

[[defaults.qualify]]                 # OR between clauses ...
impacts = ["HIGH"]
[[defaults.qualify]]                 # ... AND inside one
consequences = ["missense_variant"]
min_revel = 0.5
[[defaults.qualify]]
min_spliceai = 0.2

[[groups]]
id = "group_a"
name = "Group A"
genes = ["GENE1", "GENE2", "HGNC:613"]   # symbols, unique synonyms or HGNC IDs
recessive = ["GENE2"]                    # a subset of genes: report cis/trans and biallelic state
[groups.overrides]
max_af = 0.001

[[sites]]                            # one site to watch
id = "site_1"
locus = "GRCh38:chr1:1000000:G>A"    # BUILD:CHROM:POS:REF>ALT, 1-based
rsid = "rs123"                       # optional

[[haplotypes]]                       # named allele combinations over sites on one chromosome
id = "hap_1"
gene = "GENE3"
sites = ["GRCh38:chr19:2000000:T", "GRCh38:chr19:2000138:C"]   # BUILD:CHROM:POS:REF
alleles = { a = ["C", "T"], b = ["T", "T"], c = ["T", "C"] }

[calibration]                        # all optional
null_sets = 1000                     # 100 to 100000; default 1000
match_on = ["callable_cds_bp"]       # default; see the gnomAD option below
bins = 20                            # 2 to 100; default 20
seed = 1287                          # default 0
```

A panel needs at least one group, site or haplotype.

### Filters

`[defaults]` sets the filters of every group, and a group's `[groups.overrides]`
replaces any of them for that group. `qualify` is replaced whole, not merged.
Every group needs `qualify` clauses, from the defaults or its overrides.

| Key | Value |
| --- | --- |
| `max_af` | Keep variants whose gnomAD allele frequency is at most this fraction (0–1). A variant without a frequency is kept and flagged. |
| `max_popmax_af` | The same for the highest population frequency, where the table has one. |
| `pass_only` | Require FILTER `PASS` unless `missing_filter` allows an unset FILTER. Default `true`. |
| `min_confidence` | The lowest [confidence label](variant-confidence.md) kept: `high`, `moderate` or `low`. |
| `missing_filter` | With `pass_only`, `drop` (default) or `keep` records with FILTER `.` (not applied). `keep` still rejects named filters such as `LowQual`. |
| `missing_confidence` | With `min_confidence`, `drop` (default) or `keep` records with no confidence label. `keep` still rejects labels below the minimum. |
| `missing_scores` | What to do when a clause needs a score the variant lacks: `unscored` (list it, never count it; the default) or `drop`. |
| `score_order` | Optional fallback order from `revel`, `alphamissense`, `cadd_phred`, `spliceai`. An allele uses the first present, valid score, even if it fails that predictor's thresholds. |
| `exclude_clinvar_benign` | When `true`, veto an otherwise qualifying allele with an unambiguous allele-matched ClinVar germline benign or likely benign aggregate. Default `false`. |
| `qualify` | Clauses; a variant qualifies when it passes any clause. |

A clause passes when the variant passes every condition in it:

| Key | Passes when |
| --- | --- |
| `impacts` | The impact is one of `HIGH`, `MODERATE`, `LOW`, `MODIFIER`. |
| `consequences` | The consequence is one of these Sequence Ontology terms (`missense_variant`). |
| `min_revel`, `min_alphamissense`, `min_spliceai` | The score is at least this (0–1). |
| `min_cadd_phred` | The CADD PHRED score is at least this (0–100). |
| `weight` | Not a condition: a passing clause contributes this weight (0–1, default 1). The ALT takes the largest passing weight. A zero-weight ALT still qualifies and is counted in the raw results. |

Scores use three-valued logic. A failed condition rules out its clause even
if that clause also needs a missing score. A passing clause qualifies the
variant regardless of what other clauses say. If no clause passes and one is
undecided, the variant is `unscored`, listed but never counted by default.

With `score_order`, every score-bearing clause must test exactly one
predictor from the list. A valid score is finite and in its documented
range; the presence of a higher-priority score disables clauses for
lower-priority predictors even when the higher-priority score fails.
If none is valid, applicable score clauses still use the missing-score logic
above. Clauses without a score test are independent. Predictor values are
not interchangeable probabilities. See the [score annotation notes](variant-table.md#in-silico-scores)
for local inputs and track-specific terms.

The ClinVar veto uses the direct germline `CLNSIG`, ClinVar Variation ID and
review annotation written by local `vcf clinvar` matching. The evidence
must be ALT-specific: `vcf clinvar` now writes a per-ALT germline tag so
scalar fields copied by VCF splitting don't qualify as independent evidence.
Re-run local `vcf clinvar` matching if an older VCF lacks this evidence.
It accepts exact benign, likely benign or combined benign/likely benign
aggregates only. VEP-only `CLIN_SIG`, ambiguous multiallelic fields,
conflicting assertions, pathogenicity and missing evidence never veto an
ALT. Missing annotation does not imply benignity. The audit lists vetoed
ALTs and warns when the group has no usable matched annotation. When a
missing FILTER or confidence policy is explicit and active, the audit counts
missing labels on observed group ALTs and warns when it finds any; these
counts precede other filters. No annotation or downloads are done by these
options.

### Optional weighted burden

Set `weight` on qualifying clauses or set a group-level `dosage` table to
enable weighted burden for that group. For example, after a panel's header:

```toml
[defaults]
score_order = ["revel", "cadd_phred"]
qualify = [
  { min_revel = 0.75, weight = 1.0 },
  { min_revel = 0.5, weight = 0.5 },
  { min_cadd_phred = 25, weight = 0.5 }
]

[[groups]]
id = "example"
genes = ["MTHFR"]
dosage = { het = 0.5, hom = 1.0, trans = 1.0, cis = 0.5, phase_unknown = 0.5 }
```

Each qualifying ALT contributes `called copies × clause weight × dosage
multiplier`. Dosage multipliers range from 0 to 1; an omitted category uses
1. A complete diploid homozygous ALT (`hom`) contributes two copies. A
complete haploid ALT contributes one copy with multiplier 1 and doesn't
affect the dosage category of diploid hits. Incomplete and polyploid calls
use `phase_unknown` and prevent phase comparisons across records.

Two qualifying ALTs in a complete diploid `1/2` call use `trans` even
without a phase set or when other calls are unresolved. Otherwise, a lone
diploid heterozygote uses `het` if there are no qualifying incomplete or
polyploid calls. Multiple diploid heterozygotes use `cis` or `trans` only
when all are phased on the same contig in the same explicit phase set and
there are no qualifying incomplete or polyploid calls. In that block,
if all are on one copy they use `cis`; if both copies have hits they
use `trans`. Heterozygotes that meet none of these rules use `phase_unknown`.
Dosage categories never stack. Only qualifying ALTs are considered after
vetoes, including zero-weight ALTs. The gene's separate biallelic evidence
summary can report a demonstrated pair even when other hits are unresolved.

The audit JSON keeps raw copy counts and adds the applied clause weight,
dosage category, multiplier, and contribution for each qualifying ALT. It
reports gene-level dosage components and a group total; the allele table
adds the four per-ALT fields when at least one group uses weights. Without
the new keys the legacy report and allele table are unchanged.

### Genes

A gene is a symbol (any case), an unambiguous synonym, or an HGNC ID
(`HGNC:613`), resolved in the bundled NCBI RefSeq annotations
(`assets/panel/genes_<build>.tsv.gz`, public domain). A gene listed twice in
one group (also through a synonym) is an error.

Each gene's coding length (`cds_bp`, stop codon included) is that of one
transcript: its MANE Select transcript on GRCh38, else its RefSeq Select one
(the GRCh37 and T2T annotations tag no MANE), else its longest coding
transcript. `details` names the transcript and how it was chosen
(`cds_basis`). Null-panel calibration will match genes on this length, counting
only bases a genome actually covers.

### Sites and haplotypes

Coordinates are build-qualified and must match the panel's `build`; nothing is
lifted over. A haplotype's sites share one chromosome, each allele lists one
base per site in the sites' order, and two allele names can't share the same
bases. Allele names are the panel author's labels and get no interpretation.

## Audit a sample

```console
wgsextract panel audit --panel my_panel.toml --vcf-input sample.vcf.gz \
  --gvcf sample.g.vcf.gz --research-panel
```

The audit reads one local sample. Gene groups use the [variant table's
annotations](variant-table.md): annotate gene consequences first with VEP
or SnpEff. The annotation must name a gene and the variant must fall within
its RefSeq span. A homozygous ALT counts as two copies; imputed genotypes do
not enter group counts. Missing population frequencies are flagged, not
treated as evidence of rarity.

At watched loci, a gVCF can show confident reference. An absent locus in a
variants-only VCF is `not_assessed`. The audit uses the input's gVCF companion
if one was recorded, or you can name one with `--gvcf`. It recognizes DeepVariant
reference blocks even when the caller omits the symbolic ALT header. For gene
callability the default exon models cover selected genes on GRCh38 and GRCh37; specify
`--exons` for a local model covering other genes. No gVCF or no exon model for
a gene means the audit does not infer that its coding bases were read.

Heterozygotes are compared across positions only within a common phase set;
two qualifying ALTs in a complete diploid `1/2` call establish `trans`
without a phase set. Two qualifying ALTs on the same chromosome copy are
`cis` (`one_copy` if there is no other evidence); on opposite copies they
are `trans` (`biallelic`). A gene's phase and a haplotype watch's named allele
match are separate results.
Haplotype watches show `ambiguous_phase` when more than one orientation is
possible, even if only one matches the named patterns. For pharmacogenes,
prefer `vcf pharmcat` for star alleles and copy-number exceptions.

The results are a JSON document, a variant TSV (or `--format parquet`), and a
result manifest. They are research-only, not a diagnosis. No files, variants,
or coordinates are sent over the network.

### Matched null calibration

Add `[calibration]` to the panel, or pass `--null-sets 1000`, to request a null
comparison. Use a whole-genome GRCh38 VCF with a comparable annotation scope
for the panel and controls. `--no-calibration` skips it even if the panel
requests it. A VCF marked as called on a restricted region is rejected for
calibration. The first run uses an installed, checksum-pinned MANE Select
exon model; `--download` explicitly fetches the public model from the
WGSExtract GitHub release, or `--exons FILE` supplies a local model in the
same format. No sample information is sent. The model is derived from NCBI
RefSeq (public domain) and NIST GIAB stratifications (freely reusable).

The audit applies the same group filters to the observed and control genes.
For calibration it counts only qualifying alleles inside coding bases where
the gVCF supports a variant or reference call, or inside model CDS when no
gVCF is supplied. It excludes genes with at least half their CDS in
segmental duplications, and all panel genes from the control pool. Controls
are drawn without replacement within quantile bins of coding length and the
same chromosome class. ChrX is included only when the input declares known
sex in its calling policy. Missing opportunity or too few matched genes
does not produce a null statistic. ChrY is excluded from calibration. For XY
samples, pseudoautosomal and non-pseudoautosomal X genes are matched separately;
genes whose CDS crosses both are excluded. In haploid X regions, `1` and `1/1`
each count as one ALT copy. In those regions, heterozygous and partial
calls contribute no copies to calibration; ordinary audit counts are
unaffected. The audit withholds the statistic if a target length lies
outside its chromosome class's control range or the expected control total
differs by more than 20% from the target total.

The report shows observed copies and genes, the null allele-count histogram
and matched lengths, mean, population standard deviation, percentile
(midrank for ties), and the finite-sample empirical upper-tail probability
`(1 + null counts >= observed) / (1 + number of draws)`. Small panels and a
null mean below one are flagged as underpowered. The comparison is to other
genes in **this sample**, not population disease association.

For weighted groups, panel genes and controls use the same per-gene scorer
after the same coding-opportunity gate. Phase for the matched statistic
uses only alleles that pass that gate, and non-PAR haploid X calls keep the
one-copy convention. The observed raw count is still shown, alongside the
weighted score. Weighted groups skip symbolic and breakend ALTs and ALTs
with an `SVTYPE` annotation; use an annotated small-variant call set, not a
structural-variant or target-CNV call set. Histogram keys are lower
edges of fixed unit-width bins
(`[0,1)`, `[1,2)`, …). The empirical tail and percentile use unbinned,
exact sums of the configured decimal weights, so equal scores tie
regardless of how the copies were combined. `z_score` is null when the
standard deviation is zero or the result cannot be represented. For weighted
groups, the low-count warning uses the mean **raw** ALT count; zero variance
also flags the comparison as underpowered. Copy-number burden is not part
of this statistic.

### Optional gnomAD expected-count covariate

`--gnomad-expected`, or `match_on = ["callable_cds_bp", "gnomad_expected_syn"]`
under `[calibration]`, adds a second comparison. Its source is gnomAD v4.1
gene constraint metrics (`syn.exp`, [CC0 1.0](https://gnomad.broadinstitute.org/terms)),
matched to the exact versioned RefSeq MANE Select transcript. Genes without
a matching expectation are listed or counted as exclusions; missing values
are not zero. The public checksum-pinned file is fetched from the WGS Extract
GitHub data release only with explicit `--download`. No sample data is sent.

The original CDS-only null is unchanged. On the subset with expected
counts, the report compares a CDS-only null with one also matched within
four expected-count quantiles per CDS stratum. The two arms use the same
genes and filters, so the comparison separates covariate effects from
missing data. It shows balance on both axes and flags a joint null that
becomes underpowered or has no comparable controls. A joint target more
than 20% outside its actual control range on either axis is not calibrated.
The synonymous
expectation is a matching covariate, not a risk score.

## Problems and their locations

A location is a key path with 0-based indices (`groups[1].genes[3]`), or a
line and column for a syntax error.

| Code | Meaning |
| --- | --- |
| `syntax` | Not valid TOML or JSON, or (JSON) a key given twice. |
| `unsupported_schema` | `schema` names another format or version. |
| `unknown_key` | A key the format doesn't have; the message lists the ones it has. |
| `missing_key` | A required key is absent. |
| `wrong_type` | A value of the wrong type (a string where a number belongs). |
| `bad_value` | A value out of range, not allowed, empty or in the wrong form. |
| `bad_qualify_rule` | A weighted clause lacks a condition, or a score clause does not test exactly one predictor from the effective `score_order`. |
| `duplicate` | A repeated id, gene, site, list item or allele combination. |
| `empty_panel` | No groups, sites or haplotypes. |
| `no_qualify_rule` | A group without `qualify` clauses. |
| `recessive_not_in_group` | A `recessive` gene that isn't one of the group's genes. |
| `build_mismatch` | Sites or haplotypes without a panel `build`, or on another build. |
| `locus_out_of_range` | A position beyond the end of its chromosome. |
| `allele_length` | An allele with more or fewer bases than the haplotype has sites. |
| `haplotype_split` | A haplotype's sites on more than one chromosome. |
