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
CHM13v2.0; the VCF filename does not identify its build. `--pad-kb` pads each
whole gene span on both sides (default: no padding).

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
| `gnomad_af` | real | gnomAD allele frequency of this ALT (from `vcf gnomad`; else VEP's `gnomADg_AF`, then `gnomADe_AF`, then `gnomAD_AF`). |
| `genotype`, `zygosity` | text | The sample's GT (`0/1`, `1\|1`, ...), and its copies of this ALT: `het`, `hom_alt`, `hemizygous`, `not_carried` or `no_call`. |
| `depth`, `gq`, `qual`, `filter` | integer, integer, real, text | Read depth (FORMAT/DP, else the sum of AD, else INFO/DP), genotype quality, QUAL and FILTER. |
| `confidence` | text | The depth-aware `high`/`moderate`/`low` label in `INFO/WGSE_CONF`, when the VCF has one ([variant-confidence.md](variant-confidence.md)). |
| `confidence_reasons` | text | The reason codes behind that label, `\|`-separated (`INFO/WGSE_CONF_REASONS`), for example `low_depth\|strand_bias`. |
| `pharmgkb_level`, `pharmgkb_drugs` | text | Best PharmGKB level of evidence (1A, 1B, 2A, 2B, 3, 4) and the drugs, from `vcf pharmgkb`. |
| `pharmcat_diplotype`, `pharmcat_phenotype` | text | PharmCAT's call for the row's gene, with `--pharmcat-json <stem>.phenotype.json` from `vcf pharmcat`. |
| `sv_af`, `sv_freq` | real, text | A structural variant's population frequency and its class (`common`, `low`, `rare` or `novel`), from [`vcf sv-freq`](sv-frequency.md). |
| `acmg_class`, `acmg_criteria`, `acmg_transcript` | text | **Research use only; not a diagnosis.** fastVEP's automated ACMG/AMP class (`P`, `LP`, `VUS`, `LB`, `B`), the criteria it found met (`&`-separated) and the transcript it classified on, from [`vcf acmg`](acmg.md). Empty when the VCF has no classes. |
| `imputed`, `genotype_probability` | integer, real | `1` when the genotype was imputed from a reference panel (`INFO/IMP`, from [`vcf impute`](imputation.md)), else `0`; and the largest FORMAT/GP when its values are probabilities in [0, 1]. Unrecognised/phred-scaled scores leave the probability empty. Imputed rows below `vcf impute`'s thresholds have `filter` `ImpLowConf`. |

The TSV has a header row and leaves missing values empty. The SQLite database
has the table `variants` with these columns, `columns` (each column's type and
meaning, for an agent to read first) and `metadata` (wgsextract version,
source VCF and its SHA-256, sample, reference build, region, annotations found,
row count and command line). It is indexed on `gene`, `rsid` and
`(chrom, pos)`.

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

-- Rare protein-changing variants (absent from gnomAD or below 0.1%)
SELECT gene, hgvs_p, consequence, gnomad_af, zygosity
FROM variants
WHERE impact IN ('HIGH', 'MODERATE')
  AND (gnomad_af IS NULL OR gnomad_af < 0.001)
  AND filter = 'PASS';

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
