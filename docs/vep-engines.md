# VEP engines

## Assembly and download mirror

`vep run --assembly GRCh37` and deprecated `--vep-assembly GRCh37`
select the same build. Conflict warnings use canonical `--assembly` for
either spelling. `vep download --assembly` selects the cache build;
`--mirror uk` (default) or `--mirror ebi` chooses its download mirror.

## Choosing an engine

`wgsextract vep run` annotates with one of two engines:

| | `vep-rs` (default) | `ensembl` |
| --- | --- | --- |
| What it is | [vep-rs](https://github.com/natera-open-source/vep-rs) 0.5, a Rust reimplementation of Ensembl VEP 116 | [Ensembl VEP](https://www.ensembl.org/info/docs/tools/vep/) 116, Perl |
| Platforms | Linux, macOS, **native Windows** | Linux, macOS |
| Pixi environment | `vep-rs` | `vep` |
| Cache | its own, built from Ensembl's files by `vep download` | Ensembl's indexed cache, `vep download --engine ensembl` |

Choose the engine per run with `--engine`, or for every run with `vep_engine` in
`config.toml`:

```toml
vep_engine = "ensembl"
```

```bash
wgsextract vep download --assembly GRCh38   # vep-rs's cache
wgsextract vep verify --assembly GRCh38
wgsextract vep run --input sample.vcf.gz
wgsextract vep run --engine ensembl --input sample.vcf.gz
```

Both write the same `<input stem>_vep.vcf.gz` with a VEP `CSQ` field and the same
field names, so `vcf table`, `vcf trio` and the report read either.

### BAM/CRAM provisional calls

BAM/CRAM input first runs bcftools mpileup and multiallelic calling, discovering
both SNPs and small indels before annotation. `--region` restricts discovery;
`--ref` must match the alignment, and the ploidy options control calling.
Indel calls discard stale bcftools bias tags (SNP values are retained), and
reference normalization resolves IUPAC ambiguity codes to valid VCF alleles.
This convenience stage is not a validated clinical discovery pipeline: it does
not discover structural variants or guarantee small-variant sensitivity.
For reviewed calls from another caller, supply its VCF/BCF directly; VEP
annotates those records without recalling variants.

### Upgrading from Ensembl VEP

vep-rs became the default after Ensembl VEP. If you choose no engine, have
no vep-rs cache, and Ensembl VEP and its cache are installed, `vep run`
keeps using Ensembl VEP. It warns `vep_engine_fallback` with the switch:
`wgsextract vep download` builds vep-rs's cache, and `--engine ensembl` or
`vep_engine = "ensembl"` keeps Ensembl VEP and silences the warning. A run
without the selected engine's local cache fails with `vep_cache_missing`, as
does an Ensembl cache missing its metadata or chromosome directories.
Download the matching release and assembly with `wgsextract vep download`
(`--engine ensembl` for Ensembl VEP), then rerun annotation. Neither engine
falls back to the online Ensembl database.

## Speed, memory and disk

Measured on HG002 (5.3 million bcftools calls), GRCh38, Ensembl release 116,
with `--everything`, on a Linux x64 desktop. "Default cache" is the vep-rs cache
`vep download` builds, with known variants; "no-variants cache" is the same
built with `--no-variations`.

| | vep-rs, default cache | vep-rs, no-variants cache | Ensembl VEP |
| --- | --- | --- | --- |
| Whole genome, 6 threads (wall / CPU) | **8.8 min** / 34 CPU-min | 2.9 min / 15 CPU-min | 3 h 34 min / 16.3 CPU-h |
| Whole genome, peak memory | **7.5 GB** | 7.7 GB | 1.4 GB |
| chr1 (422,168 calls), 4 threads | 51 s, 1.0 GB | | 20 min, 0.7 GB |
| Cache on disk | **17 GB** (5.7 GB transcripts + 11 GB known variants) | 5.7 GB | 26 GB |
| Download | ≈17 GB | 1.6 GB | 27.6 GB |
| Build: peak memory | 11 GB | 8.5 GB | none (unpack only) |
| Build: peak disk | ≈37 GB (downloads, genome and cache, until the cache is complete) | ≈10 GB | ≈54 GB (tarball and unpacked cache) |
| Build time, after downloading | 80 min on 2 cores | 2 min | |

vep-rs keeps each chromosome's transcripts in memory once it has read them, so
memory grows to the 7.5 GB above over a whole genome, whatever `--fork`;
one chromosome's VCF needs about 1 GB. A 16 GB computer runs a whole genome;
8 GB is not enough. `vep download` refuses a cache folder without room for the
build, and `vep download` and `vep run` report a memory preflight.

## The vep-rs cache

`vep download` (engine vep-rs) downloads Ensembl's GFF3 gene set, protein and
genome FASTA and table dumps for the release and, unless `--no-variations`,
its variation VCFs, several files at a time. It checks each against Ensembl's
`CHECKSUMS` and builds the cache with vep-rs's `vep-cache-builder` in
`<vep cache>/vep-rs/homo_sapiens/<release>_<assembly>`, beside any Ensembl VEP
cache. An interrupted download resumes; the old cache is replaced only once
the new one is complete. `vep verify` checks every file against the
checksums the builder wrote, and `vep delete` removes the cache and any
unfinished download.

## T2T-CHM13v2.0

vep-rs also annotates T2T-CHM13v2.0 (`chm13v2.0`) VCFs; Ensembl VEP has no
CHM13 cache here, so `--engine ensembl` refuses the assembly.

```bash
wgsextract vep download --assembly T2T-CHM13v2.0
wgsextract vep run --input sample.chm13.vcf.gz   # the assembly is read from the VCF
```

`vep download` builds the cache from Ensembl's T2T-CHM13v2.0 gene set
(GCA_009914755.4, rapid release `2022_07`, GENCODE 38 projected onto CHM13 for
the HPRC; Ensembl data, free to use without restriction): its GFF3, GTF,
protein and genome FASTA, about 1 GB of downloads, each checked against the
folder's `md5sum.txt`. The build takes seconds, peaks at 2.8 GB of memory and
writes a 1.2 GB cache in `<vep cache>/vep-rs/homo_sapiens/116_T2T-CHM13v2.0`.
`vep run` takes the assembly from the reference library's build, else from the
VCF's contig lengths; `--assembly T2T-CHM13v2.0` names it outright.

What a CHM13 cache lacks, beyond the GRCh38 gaps below:

- **Known variants** (`Existing_variation`, frequencies, ClinVar columns):
  Ensembl publishes none in a form the builder reads. `vep download` warns
  `vep_variations_unavailable` and `vep run` warns `vep_cache_gaps`.
- **MANE, CCDS, protein domains, `GENE_PHENO`**: these come from Ensembl's
  table dumps, which the rapid release doesn't have. The canonical transcript
  is flagged.
- **chrM**: Ensembl doesn't annotate CHM13's mitochondrion.
- **Gene set age**: GENCODE 38 (2021). GRCh38's release 116 is GENCODE 50,
  with many more lncRNA transcripts. A third of its genes, nearly all
  non-coding (0.7% of protein-coding ones), have no symbol: `SYMBOL` is empty
  and `Gene` holds the CHM13 stable ID (`ENSG0522…`).

### How CHM13 annotation compares with GRCh38's

HG002's GIAB v5.0q small-variant calls on CHM13 (5,829,374 records) were
annotated on CHM13, then lifted to GRCh38 with the reference library's
`chm13v2-grch38.chain` (CrossMap) and annotated there with vep-rs caches of
Ensembl 116 and of Ensembl 104 (GENCODE 38, the set CHM13's is projected
from). Agreement of the `vcf table` row of the same call (gene, consequence
and impact of the transcript the table picks) over the 2,491,092 table rows
on both, and of all CSQ entries over the 2,577,520 lifted records:

| | vs Ensembl 104 (same gene set) | vs Ensembl 116 |
| --- | --- | --- |
| Gene | 96.8% | 91.5% |
| Consequence | 96.0% | 83.5% |
| Impact | 99.9% | 99.9% |
| Most severe consequence, all entries | 98.0% | 82.8% |
| Same set of genes, all entries | 97.8% | 89.7% |

Against release 116 most differences are lncRNA transcripts GENCODE has
added since 38 (`intergenic_variant` on CHM13, `intron_variant` of a new
non-coding transcript on GRCh38). Against 104 they are paralogs
(RGPD5/RGPD6, LIMS3/LIMS4), a different canonical transcript, and indels at
splice sites placed differently. Annotating on CHM13 also keeps calls a lift
to GRCh38 loses: 1.26 million of the 5.83 million (mostly centromeric
satellites, 61,000 in genes) don't lift, and 2.0 million match GRCh38's
reference there. A whole genome took 82 s on 4 threads and 3.5 GB of memory.

## Where vep-rs differs from Ensembl VEP

Agreement per (variant, transcript) pair, where Ensembl VEP has a value:

| Field | HG002 whole genome, no-variants cache (61,451,654 pairs) | HG002 chr1, default cache (4,947,499 pairs) |
| --- | --- | --- |
| Consequence, IMPACT | 99.99997% | 99.99996%, 99.99998% |
| SYMBOL, Gene, BIOTYPE, EXON, INTRON, CANONICAL, MANE_SELECT, MANE | 100% | 100% |
| HGVSc | 99.9999% (48,639,197 of 48,639,241) | 99.9999% (3,832,967 of 3,832,972) |
| HGVSp | 99.9968% (278,871 of 278,880) | 99.9962% (26,416 of 26,417) |
| Protein_position | 99.9921% (279,068 of 279,090) | 99.9886% (26,421 of 26,424) |
| Existing_variation | empty | 99.9998% (4,898,139 of 4,898,148) |
| 1000 Genomes AF (AF, AFR_AF, …) | empty | 99.78–99.998% |
| CLIN_SIG, SOMATIC, PHENO, GENE_PHENO | empty (GENE_PHENO 100%) | 99.97–100% |
| PICK | 99.96% of pairs | 100% |

- **PICK.** On the whole genome, 0.04% of pairs (23,910 of 61.45 million)
  pick differently. These come from records where Ensembl VEP picks a
  regulatory-feature row, which a cache built by `vep download` doesn't hold;
  vep-rs picks the intergenic or transcript row there. The chr1 comparison ran
  Ensembl VEP without regulatory features, so it agreed on every pick. In `vcf table` this
  changes the consequence of those records, from `regulatory_region_variant` or
  `TF_binding_site_variant` to, usually, `intergenic_variant`. On HG002 chr22
  that was 440 of 85,046 rows (0.5%), and no other column changed.
- **SIFT and PolyPhen are empty.** The builder can't fetch the prediction
  matrices without Ensembl's MySQL server. Use `vcf alphamissense` or
  `vcf revel` instead.
- **gnomAD frequencies (`gnomADe_*`, `gnomADg_*`) are empty**, and so
  `MAX_AF` differs: Ensembl publishes no gnomAD VCF to build them from. Use
  `vcf gnomad`. `PUBMED` is empty too.
- **Regulatory features** are not annotated.
- **Not supported:** `--custom` sources. `vep run` refuses these with vep-rs
  rather than ignoring them; use `--engine ensembl` for local custom sources.
- Records Ensembl VEP skips (an SV class it has no term for, such as `<CPX>`,
  or one larger than `--max_sv_size`) stay in vep-rs's output without `CSQ`;
  Ensembl VEP leaves them out.

`vep run` warns `vep_cache_gaps` when the cache it uses lacks known variants,
gnomAD frequencies or predictions, and `vcf table` warns `gnomad_af_missing`
when its `gnomad_af` column would be empty for want of them; VEP's 1000
Genomes `AF` and `MAX_AF` go to the table's `kg_af` and `max_af` instead. The
full comparison, including fastVEP, vareffect and bcftools csq, is in
[#602](https://github.com/WGSExtract/wgsextract-cli/issues/602).
