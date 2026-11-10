# VEP engines

`wgsextract vep run` annotates with one of two engines:

| | `ensembl` (default) | `vep-rs` |
| --- | --- | --- |
| What it is | [Ensembl VEP](https://www.ensembl.org/info/docs/tools/vep/) 116, Perl | [vep-rs](https://github.com/natera-open-source/vep-rs) 0.5, a Rust reimplementation of Ensembl VEP 116 |
| Platforms | Linux, macOS | Linux, macOS, **native Windows** |
| Pixi environment | `vep` | `vep-rs` |
| Cache | Ensembl's indexed cache, `vep download` (26 GB for GRCh38) | its own JSON cache, built from Ensembl's files by `vep download --engine vep-rs` (6 GB without known variants) |
| Speed on a 30x genome | hours | minutes |

Choose the engine per run with `--engine`, or for every run with `vep_engine` in
`config.toml`:

```toml
vep_engine = "vep-rs"
```

```bash
wgsextract vep download --engine vep-rs --assembly GRCh38
wgsextract vep verify --engine vep-rs --assembly GRCh38
wgsextract vep run --engine vep-rs --input sample.vcf.gz
```

Both write the same `<input stem>_vep.vcf.gz` with a VEP `CSQ` field and the same
field names, so `vcf table`, `vcf trio` and the report read either.

## The vep-rs cache

`vep download --engine vep-rs` downloads Ensembl's GFF3 gene set, protein and
genome FASTA and table dumps for the release (about 1.6 GB for GRCh38), checks
each against Ensembl's `CHECKSUMS`, and builds the cache with vep-rs's
`vep-cache-builder` into `<vep cache>/vep-rs/homo_sapiens/<release>_<assembly>`,
beside Ensembl VEP's cache. An interrupted download resumes; the old cache is
replaced only when the new one is complete. With `--variations` (the default)
it also downloads Ensembl's variation VCFs (about 15 GB) for
`Existing_variation` and the 1000 Genomes frequencies; `--no-variations` skips
them. `vep verify --engine vep-rs` checks every file against the checksums the
builder wrote.

## Where vep-rs differs

On the same transcripts, vep-rs matched Ensembl VEP on every consequence,
impact, gene, HGVS and MANE value of HG002's chr22 (1.3 million
variant-transcript pairs; see [#602](https://github.com/WGSExtract/wgsextract-cli/issues/602)
for the full comparison). The differences are in what the cache holds and what
vep-rs implements:

- **SIFT and PolyPhen** are empty: a cache built by `vep download` has no
  prediction matrices. Use `vcf alphamissense` or `vcf revel` instead.
- **gnomAD frequencies** (`gnomADe_*`, `gnomADg_*`) are empty: Ensembl publishes
  no gnomAD VCF to build them from. Use `vcf gnomad`.
- **Regulatory features** are not annotated.
- **Not supported:** `--custom` sources and Ensembl's database (there is no
  `--database` fallback when the cache is missing). `vep run --engine vep-rs`
  refuses these rather than ignoring them; use `--engine ensembl`.
- Records Ensembl VEP skips (an SV class it has no term for, such as `<CPX>`,
  or one larger than `--max_sv_size`) stay in vep-rs's output without `CSQ`;
  Ensembl VEP leaves them out.

`vep run` warns (`vep_cache_gaps`) when the cache it uses lacks known variants
or predictions.
