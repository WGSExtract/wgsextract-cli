# Variant table: query your variants with SQL

`wgsextract vcf table` turns an annotated VCF into a flat table with one row per
variant allele and fixed, typed columns. Spreadsheets, `sqlite3`, DuckDB,
pandas or an AI agent can read it without knowing the VCF format.

```bash
wgsextract vep run --input sample.vcf.gz                   # gene, consequence, HGVS
wgsextract vcf clinvar --vcf-input sample_vep.vcf.gz       # ClinVar significance
wgsextract vcf gnomad --vcf-input clinvar_annotated.vcf.gz # gnomAD frequency
wgsextract vcf table --vcf-input gnomad_annotated.vcf.gz
```

This writes `<input stem>_variants.tsv` and `<input stem>_variants.sqlite`
(`--no-sqlite` skips the database) and a result manifest naming both.
`--format parquet` writes `<input stem>_variants.parquet` instead of the TSV
(see [Parquet output](parquet.md); it needs the optional `pyarrow`). The
table uses whatever annotations the VCF has. Columns whose annotation is
missing stay empty, so annotate first; any subset and order of the commands
above works.

## Rows

- **One row per ALT allele.** A multi-allelic site gives a row per ALT, each with
  its own allele frequency, consequence and zygosity. Rows keep the VCF's
  order, which is sorted by position.
- **Only alleles the sample carries.** Calls of `0/0`, no-calls and the other ALT
  of a `0/1` multi-allelic site are left out, so a gVCF or a DeepVariant VCF
  with `RefCall` records yields only real variants. `--all-alleles` keeps every
  ALT, with `zygosity` saying which the sample carries. A sites-only VCF (no
  samples) keeps every ALT.
- gVCF reference blocks (`<NON_REF>`, `<*>`) and the spanning-deletion
  placeholder `*` never get a row.
- A multi-sample VCF uses its first sample, or the one named with `--sample`.
- `--region chr22` or `--region chr1:1000000-2000000` exports one region.

### Slice a VCF to a panel

Use the same options on `vcf filter` and `vcf table`:

```bash
wgsextract vcf filter --vcf-input sample.vcf.gz --genes BRCA1,BRCA2 --bed targets.bed
wgsextract vcf table --vcf-input sample.vcf.gz --preset acmg-sf --region chr17
```

Repeat `--gene SYMBOL` or `--genes SYMBOL1,SYMBOL2`, give `--gene-file FILE`,
or select a `--preset` listed by `wgsextract panel bed --list-presets`. A gene
file accepts names separated by spaces, commas or newlines; `#` starts a
comment. Symbols and unique aliases resolve offline against the bundled RefSeq
gene spans. An unknown name or ambiguous alias is an error. The VCF header or
the `.fai` of a matching `--ref` FASTA must identify GRCh37, GRCh38 or
CHM13v2.0. If `--ref` is a local library and the header doesn't identify a
build, its indexed FASTAs must point to a single build. Filenames alone
don't identify an assembly. `--pad-kb` pads each whole gene span on both
sides (default: no padding).

The commands take the **union** of the gene spans and the zero-based,
half-open `--bed` intervals (plain or gzipped), then **intersect** it with
`--region` if given. `--region` takes a contig, a 1-based position or an
inclusive range; separate multiple regions with commas. Contig names such
as `chr17` and `17` match automatically. Supply BED coordinates for the VCF's
assembly; this is not liftover. A VCF record is kept if it overlaps the final
intervals, without duplicating records from overlapping targets. A panel with
no matches returns a header-only VCF or table. Other filters (such as `--expr`
or `--sample`) still apply.

The result manifest's `details.panel` records the resolved genes and presets,
the final intervals in VCF contig naming (BED coordinates), and the SHA-256
of the BED file as supplied.

## Columns

| Column | Type | Meaning |
| --- | --- | --- |
| `chrom`, `pos`, `ref`, `alt` | text, integer, text, text | The allele, as in the VCF (1-based position). |
| `rsid` | text | dbSNP rsIDs from the VCF's ID column, else VEP's `Existing_variation`, else PharmGKB's; `;`-separated. Callers such as DRAGEN and DeepVariant leave the ID column empty, so annotate with `vep` (the default `everything` preset) to fill it. |
| `gene` | text | VEP `SYMBOL`, else SnpEff `Gene_Name`, else the PharmGKB gene. |
| `consequence`, `impact` | text | Sequence Ontology terms (`&`-separated) and HIGH/MODERATE/LOW/MODIFIER, from the chosen transcript. |
| `hgvs_c`, `hgvs_p` | text | HGVS coding and protein change on the chosen transcript. |
| `clinvar_significance`, `clinvar_condition` | text | ClinVar `CLNSIG` and `CLNDN` (from `vcf clinvar`; else VEP's `CLIN_SIG`). |
| `gnomad_af` | real | gnomAD allele frequency of this ALT (from `vcf gnomad`; else VEP's `gnomADg_AF`, then `gnomADe_AF`, then `gnomAD_AF`). Empty when gnomAD lacks the ALT, or when the VCF has no gnomAD annotation: a vep-rs cache from `vep download` has none, so run `vcf gnomad` (see [Population frequencies](#population-frequencies)). |
| `genotype`, `zygosity` | text | The sample's GT (`0/1`, `1\|1`, ...), and its copies of this ALT: `het`, `hom_alt`, `hemizygous`, `not_carried` or `no_call`. |
| `depth`, `gq`, `qual`, `filter` | integer, integer, real, text | Read depth (FORMAT/DP, else the sum of AD, else INFO/DP), genotype quality, QUAL and FILTER. |
| `confidence` | text | The depth-aware `high`/`moderate`/`low` label in `INFO/WGSE_CONF`, when the VCF has one ([variant-confidence.md](variant-confidence.md)). |
| `confidence_reasons` | text | The reason codes behind that label, `\|`-separated (`INFO/WGSE_CONF_REASONS`), for example `low_depth\|strand_bias`. |
| `pharmgkb_level`, `pharmgkb_drugs` | text | Best PharmGKB level of evidence (1A, 1B, 2A, 2B, 3, 4) and the drugs, from `vcf pharmgkb`. |
| `pharmcat_diplotype`, `pharmcat_phenotype` | text | PharmCAT's call for the row's gene, with `--pharmcat-json <stem>.phenotype.json` from `vcf pharmcat`. |
| `sv_af`, `sv_freq` | real, text | A structural variant's population frequency and its class (`common`, `low`, `rare` or `novel`), from [`vcf sv-freq`](sv-frequency.md). |
| `acmg_class`, `acmg_criteria`, `acmg_transcript` | text | **Research use only; not a diagnosis.** fastVEP's automated ACMG/AMP class (`P`, `LP`, `VUS`, `LB`, `B`), the criteria it found met (`&`-separated) and the transcript it classified on, from [`vcf acmg`](acmg.md). Empty when the VCF has no classes. |
| `imputed`, `genotype_probability` | integer, real | `1` when the genotype was imputed from a reference panel (`INFO/IMP`, from [`vcf impute`](imputation.md)), else `0`; and the largest FORMAT/GP when its values are probabilities in [0, 1]. Unrecognised/phred-scaled scores leave the probability empty. Imputed rows below `vcf impute`'s thresholds have `filter` `ImpLowConf`. |
| `phase_set` | text | The sample's `FORMAT/PS` for a phased genotype, otherwise empty. Compare phase sets only within the same sample and chromosome. Use `genotype` together with `phase_set` to see which alleles are on the same haplotype. |
| `alt_fraction` | real | This ALT's depth divided by the sum of `FORMAT/AD` across REF and all ALTs in the record. If depths are incomplete or sum to zero, falls back to the allele's `FORMAT/VAF`, then `FORMAT/AF`, if present and in [0, 1]. `INFO/AF` is not a read fraction. |
| `transcript_id` | text | The transcript associated with `consequence`: VEP `Feature` or SnpEff `Feature_ID`. Empty for non-transcript effects. |
| `gnomad_popmax_af` | real | Maximum allele frequency across gnomAD genetic ancestry groups for this ALT (`GNOMAD_POPMAX_AF` from `vcf gnomad`, a native gnomAD `AF_grpmax_joint`/`AF_grpmax`/`AF_popmax` field, or a gnomAD-specific group-maximum field in VEP `CSQ`). VEP `MAX_AF` combines databases and is not a fallback. |
| `gnomad_nhomalt` | integer | Count of homozygotes for this ALT in gnomAD (`GNOMAD_NHOMALT` from `vcf gnomad`, native `nhomalt_joint`/`nhomalt`, or a gnomAD homozygote count in VEP `CSQ`). A known count of zero is distinct from a missing count. |
| `kg_af`, `max_af` | real | VEP's 1000 Genomes phase 3 frequency of this ALT over all populations (`AF`), and its `MAX_AF`: the highest frequency in any population VEP's cache has, gnomAD and 1000 Genomes with Ensembl VEP's cache but 1000 Genomes only with a vep-rs cache from `vep download`. Where VEP lists several co-located known variants, the highest value. |
| `ad_ref`, `ad_alt` | integer, integer | The sample's `FORMAT/AD` read counts for REF and this ALT. Empty when AD is missing or incomplete; an explicit count of zero stays `0`. A record split from a multi-allelic one keeps REF and its own ALT. |
| `revel`, `alphamissense_score`, `alphamissense_class` | real, real, text | REVEL and AlphaMissense missense pathogenicity (0–1) and AlphaMissense's class (`likely_benign`, `ambiguous`, `likely_pathogenic`); see [In-silico scores](#in-silico-scores). |
| `cadd_raw`, `cadd_phred` | real, real | CADD's raw and PHRED-scaled deleteriousness scores. |
| `spliceai_max` | real | The highest of the ALT's four SpliceAI delta scores (`DS_AG`, `DS_AL`, `DS_DG`, `DS_DL`, 0–1), for the row's gene when scored. |
| `phylop`, `gerp` | real, real | PhyloP and GERP++ conservation scores for the position. |
| `sift_score`, `sift_prediction`, `polyphen_score`, `polyphen_prediction` | real, text, real, text | SIFT and PolyPhen-2 for the chosen transcript (for example `deleterious` 0.01, `probably_damaging` 0.998). |
| `gnomad_faf95` | real | Maximum filtering allele frequency at 95% confidence across gnomAD ancestry groups for this ALT (`GNOMAD_FAF95` from `vcf gnomad`, or the native group-maximum field). Zero is a reported value; empty means unavailable. |

When a WGSE annotation splits a multi-allelic record, it saves the original
AD total so the read fraction stays the same. A known split without a
complete original total can only use `FORMAT/VAF` or `FORMAT/AF`.

The TSV has a header row and leaves missing values empty. The SQLite database
has the table `variants` with these columns, `columns` (each column's type and
meaning, for an agent to read first) and `metadata` (wgsextract version,
source VCF and its SHA-256, sample, reference build, region, annotations found,
`population_af_sources`, row count and command line). It is indexed on `gene`,
`rsid` and `(chrom, pos)`.

[`/v1/variants` and MCP `query_variants`](server.md) can return these
columns from any of the table formats. They omit fields that are missing.

New gnomAD v4.1 tables (schema 4) keep the joint dataset's `AF_grpmax_joint`,
`nhomalt_joint` and `fafmax_faf95_max_joint`. `vcf gnomad` carries these into
its annotated VCF and, with `--max-af`, its confidence table. For a native
standalone exome or genome sites VCF, the corresponding `fafmax_faf95_max`
describes only that callset. The joint maximum is distinct from overall
`faf95_joint` and from maximum AF. A missing ALT never falls back to those
fields or to another callset.

Schema 3 tables still work offline, including index repair, but lack FAF95.
Older five-column tables only supply `gnomad_af`. If you want FAF95, run
`wgsextract ref gnomad --release 4.1 --download` (streams about 900 GB), then
rerun `vcf gnomad` and `vcf table`. A small or missing FAF95 is not proof of
rarity. This column does not change `vcf gnomad --max-af` or ACMG defaults.

A table of a [`vcf acmg`](acmg.md) VCF keeps the research-only disclaimer
where its readers look: the `acmg_*` column descriptions, the metadata key
`acmg_disclaimer` (SQLite `metadata`, the Parquet footer), the result
manifest's warning `acmg_research_only` and the server's `query_variants`
pages that return `acmg_*` values (`disclaimer`). The TSV has only its header row, so
read the classes with that disclaimer in mind:

```sql
-- Research use only: automated P/LP calls, with the criteria behind them
SELECT gene, hgvs_p, zygosity, acmg_class, acmg_criteria, acmg_transcript
FROM variants WHERE acmg_class IN ('P', 'LP') ORDER BY gene;
```

**Which transcript.** VEP annotates every transcript a variant touches. The
table keeps one per allele, as VEP's `--pick` would: the one VEP flagged
`PICK`, else the MANE Select transcript, then the canonical one, then a
protein-coding one, then the most severe consequence. SnpEff lists the most
severe effect first, and the table takes that one.

## Population frequencies

`gnomad_af` holds gnomAD frequencies only. VEP with a vep-rs cache built by
`vep download` fills just the 1000 Genomes frequencies: Ensembl publishes no
gnomAD frequencies to build the cache from, so VEP's `gnomADe_*` and
`gnomADg_*` fields stay empty. Those 1000 Genomes values go to `kg_af` and
`max_af`, never to `gnomad_af`. When no row has a gnomAD frequency and the VCF
has no `vcf gnomad` annotation, `vcf table` warns `gnomad_af_missing`: an
empty `gnomad_af` may then mean "not annotated" rather than "rare" (VEP leaves
its gnomAD fields empty both for a cache without gnomAD and for variants
gnomAD lacks, so the warning names both). The metadata key
`population_af_sources` (`gnomad`, `1000genomes`, both or neither) and the
manifest metrics `gnomad_af_rows`, `kg_af_rows` and `max_af_rows` say which
frequencies the table holds. Run `vcf gnomad` before filtering on rarity.

## In-silico scores

`vcf table` reads scores already in the VCF. It does not compute or download
them. You can annotate first with `vcf revel`, `vcf alphamissense`, `vcf cadd`,
`vcf spliceai`, `vcf phylop` and `vcf gerp`, or run them together:

```bash
wgsextract vcf chain-annotate --vcf-input sample.vcf.gz \
  --annotations revel,alphamissense,cadd,spliceai,phylop,gerp
```

- **Source priority.** The table first looks for `REVEL`, `am_pathogenicity` /
  `am_class`, `CADD_RAW` / `CADD_PHRED`, `SpliceAI`, `PHYLOP` and `GERP` in
  INFO. It then tries fastVEP's `FV_REVEL`, `FV_ALPHAMISSENSE`, `FV_PHYLOP`
  and `FV_GERP`, then VEP's CSQ plugin fields and dbNSFP fields. SIFT and
  PolyPhen come from VEP CSQ first, then fastVEP's `FV_DBNSFP`. When fastVEP
  has several AlphaMissense entries for one ALT, the highest-scoring entry
  supplies both the score and, if present, its class.
- **Transcript choice.** REVEL, AlphaMissense, SIFT and PolyPhen from CSQ
  use the chosen transcript. CADD, PhyloP and GERP can fall back to another
  CSQ entry for the same allele. When a numeric CSQ field lists several
  values separated by `&`, the table uses the highest.
- **SpliceAI.** Each row reads only its own ALT's entries and skips missing
  (`.`) deltas. It uses the highest delta for the row's gene if that gene was
  scored, or the highest delta across all genes otherwise. For split records
  with `OLD_MULTIALLELIC`, SpliceAI and fastVEP entries match the original ALT
  first. A normalized spelling is used only if it cannot name another original
  ALT, or if `vcf spliceai` marked the score as refreshed for this row.
- **Missing and site-level scores.** Missing scores stay empty, not zero.
  At a site with several variant ALTs, a single allele-specific score
  (`Number=1`) cannot be assigned to an ALT and stays empty. PhyloP and GERP
  describe the position and apply to every ALT.

## Example queries

With `sqlite3 sample_variants.sqlite` (or any SQLite client):

```sql
-- What the database holds, and where it came from
SELECT name, type, description FROM columns;
SELECT key, value FROM metadata;

-- Variants in a pharmacogene
SELECT chrom, pos, ref, alt, rsid, consequence, hgvs_p, genotype
FROM variants WHERE gene = 'CYP2C19';

-- One rsID
SELECT * FROM variants WHERE rsid = 'rs4244285';

-- A region (uses the (chrom, pos) index)
SELECT * FROM variants
WHERE chrom = 'chr17' AND pos BETWEEN 43044295 AND 43125483;

-- ClinVar pathogenic or likely pathogenic, well covered
SELECT chrom, pos, ref, alt, gene, clinvar_significance, clinvar_condition,
       zygosity, depth
FROM variants
WHERE (clinvar_significance LIKE 'Pathogenic%'
       OR clinvar_significance LIKE 'Likely_pathogenic%')
  AND depth >= 20
ORDER BY gene;

-- Clinical findings whose call is not high confidence, and why
SELECT gene, rsid, clinvar_significance, confidence, confidence_reasons
FROM variants
WHERE clinvar_significance IS NOT NULL AND confidence IN ('low', 'moderate');

-- Protein-changing variants with a measured gnomAD frequency below 0.1%
-- and no known VEP population frequency at or above 0.1%
SELECT gene, hgvs_p, consequence, gnomad_af, max_af, zygosity
FROM variants
WHERE impact IN ('HIGH', 'MODERATE')
  AND gnomad_af < 0.001
  AND (max_af IS NULL OR max_af < 0.001)
  AND filter = 'PASS';

-- Rare missense variants with a high predicted pathogenicity score
SELECT gene, hgvs_p, revel, alphamissense_score, cadd_phred
FROM variants
WHERE consequence LIKE '%missense_variant%'
  AND (gnomad_af IS NULL OR gnomad_af < 0.001)
  AND (revel >= 0.5 OR alphamissense_class = 'likely_pathogenic');

-- Variants SpliceAI predicts to change splicing
SELECT gene, hgvs_c, spliceai_max, consequence
FROM variants WHERE spliceai_max >= 0.5 ORDER BY spliceai_max DESC;

-- Variants rare overall but common in some ancestry group, or seen
-- homozygous in gnomAD's (largely healthy) individuals
SELECT gene, hgvs_p, gnomad_af, gnomad_popmax_af, gnomad_nhomalt
FROM variants
WHERE gnomad_af < 0.001 AND (gnomad_popmax_af >= 0.01 OR gnomad_nhomalt > 0);

-- Two phased heterozygous hits in one gene on the same haplotype (cis) or
-- on opposite ones (trans), within one phase block. Only 0|1 and 1|0: a
-- multi-allelic site's rows (1|2) all keep the site's genotype.
SELECT a.gene, a.hgvs_p, a.genotype, b.hgvs_p, b.genotype,
       CASE WHEN a.genotype = b.genotype THEN 'cis' ELSE 'trans' END AS phase
FROM variants a JOIN variants b
  ON a.gene = b.gene AND a.chrom = b.chrom AND a.phase_set = b.phase_set
     AND a.pos < b.pos
WHERE a.genotype IN ('0|1', '1|0') AND b.genotype IN ('0|1', '1|0')
  AND a.impact IN ('HIGH', 'MODERATE') AND b.impact IN ('HIGH', 'MODERATE');

-- Heterozygous calls with skewed allele balance (possible mosaicism,
-- clonal haematopoiesis or an artifact)
SELECT chrom, pos, gene, genotype, ad_ref, ad_alt, alt_fraction
FROM variants
WHERE zygosity = 'het' AND alt_fraction < 0.25 AND ad_alt >= 3;

-- Homozygous loss-of-function candidates
SELECT gene, hgvs_c, hgvs_p, consequence
FROM variants WHERE impact = 'HIGH' AND zygosity = 'hom_alt';

-- Variants per gene, most first
SELECT gene, count(*) AS n FROM variants
WHERE gene IS NOT NULL GROUP BY gene ORDER BY n DESC LIMIT 20;

-- Drug-response variants with strong PharmGKB evidence
SELECT gene, rsid, genotype, pharmgkb_level, pharmgkb_drugs
FROM variants WHERE pharmgkb_level IN ('1A', '1B');
```

DuckDB reads the TSV without loading it into a database first:

```bash
duckdb -c "SELECT gene, count(*) FROM read_csv('sample_variants.tsv', delim='\t')
           WHERE impact = 'HIGH' GROUP BY gene ORDER BY 2 DESC"
```

With `--format parquet`, DuckDB reads the typed columns and the provenance
straight from the file:

```bash
duckdb -c "SELECT chrom, pos, gene, hgvs_p FROM 'sample_variants.parquet'
           WHERE clinvar_significance ILIKE '%pathogenic%' AND zygosity = 'hom_alt'"
duckdb -c "SELECT decode(key), decode(value)
           FROM parquet_kv_metadata('sample_variants.parquet')"
```

and pandas: `pd.read_csv("sample_variants.tsv", sep="\t")`,
`pd.read_parquet("sample_variants.parquet")`, or
`pd.read_sql("SELECT * FROM variants WHERE gene = 'BRCA2'", sqlite3.connect(...))`.

## Asking an agent

Point an AI agent at the SQLite file and ask in plain language. Tell it to read
the `columns` and `metadata` tables first. Questions it can answer with the
queries above:

- "Do I carry any ClinVar pathogenic or likely pathogenic variants? Which genes,
  and is the read depth good enough to trust them?"
- "Which variants do I have in CYP2C19, CYP2C9 and VKORC1, and what do they mean
  for clopidogrel and warfarin?"
- "List my rare (gnomAD below 0.1%) protein-changing variants, grouped by gene."
- "Am I homozygous for any loss-of-function variant?"
- "What is my genotype at rs429358 and rs7412 (APOE)?"
- "How many variants of each consequence type do I have?"

An agent's reading of a variant is not a diagnosis. ClinVar classifications
change, a low-depth or low-confidence call can be wrong, and the table holds
only what was called and annotated. Confirm anything that matters with an
accredited laboratory and a genetic counsellor.

## Size and speed

A 30x whole-genome DRAGEN VCF of 4.98 million records (5.07 million ALT
alleles) annotated with `vcf clinvar` took 2 minutes 14 seconds on one CPU
core with a peak of 260 MB of memory. Records are streamed, so memory does not
grow with the VCF. It wrote a 306 MB TSV and a 518 MB SQLite database, in
which an indexed lookup by gene, rsID or region takes about a millisecond and a
query that scans the whole table one to three seconds.
