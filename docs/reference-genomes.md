# Reference genomes: where they come from

`wgsextract ref library --install CODE` (and the app's Library page)
downloads a genome from the first source in the catalog
(`src/wgsextract_cli/assets/reference/seed_genomes.csv`) that works.
For most genomes that is a copy in this repository's GitHub releases. The
original source (NCBI, EBI, UCSC, AWS) is listed after it as an `-Alt` row,
and the install falls back to it automatically. The original servers have
been slow and unreliable, so the copies come first (#701).

## How a copy is made and checked

- **Made like an install:** the [Mirror reference genomes](../.github/workflows/mirror_reference_genomes.yml)
  workflow installs each genome from its original source exactly as a user's
  install does, using `scripts/mirror_reference_genomes.py`. It recompresses
  the file to BGZF, indexes it and checks its sequence-dictionary signature
  against the catalog's. Then it uploads the BGZF file under its catalog name
  to a `reference-genomes-N` release.
- **Same sequences:** the copy holds the original's sequences unchanged; only
  the compression differs.
- **Pinned:** the catalog's `SHA256` column pins each GitHub copy.
  - A download that doesn't match is discarded, and the next source is tried.
  - The install also checks the genome's signature, whichever source it came
    from.
- **Older copies:** the `v0.1.0` release copies (hs38, hs37, the hs38d1
  variants and others) predate the workflow and are pinned the same way.
- **Adding a genome:** run the workflow with the genome's file name, then
  record its row with
  `python scripts/mirror_reference_genomes.py catalog TAG RECORD.json...`
  (the records are in the release's `records.tar.gz`).

## Sources and terms

The genomes are public data. Recompressing a file doesn't change its terms.

| Original host | Genomes | Terms |
| --- | --- | --- |
| T2T Consortium (AWS) | T2T-CHM13 v2.0 and drafts v0.9 to v1.1, HG002 XY v2.0 and v2.7 | Public domain (CC0); citation of the T2T Consortium is requested ([CHM13](https://github.com/marbl/CHM13#data-reuse-and-license), [HG002](https://github.com/marbl/HG002#data-reuse-and-license)) |
| Human Pangenome Reference Consortium (AWS) | CHM13v11Y (HPP freeze 1) | Public domain, under the HPRC's [data-use protocol](https://humanpangenome.org/data-use/) |
| NCBI and 1000 Genomes | GRCh38 analysis sets, hs37d5, human_g1k_v37, T2T-CHM13 (GCA_009914755.4), dog and cat RefSeq | "NCBI places no restrictions on the use or distribution of the GenBank data" ([policy](https://www.ncbi.nlm.nih.gov/genbank/)); submitters may hold rights |
| UCSC | hg19, hg38 | hg19: "freely available for public use" ([README](https://hgdownload.soe.ucsc.edu/goldenPath/hg19/bigZips/README.txt)); hg38 follows NCBI's terms ([README](https://hgdownload.soe.ucsc.edu/goldenPath/hg38/bigZips/README.txt)) |
| Ensembl and GENCODE (EBI) | GRCh38 and GRCh37 top-level and primary assemblies | "Available without restriction"; EBI adds none ([terms](https://www.ebi.ac.uk/about/terms-of-use/)) |

**Not copied:**
- **JHU's Puerto Rican PR1 assembly (`THG1243v3`):** it states no licence.
- **YSEQ's `hg19.zip` and `hg38_CP086569` (`hg37`, `THGySeqp`):** YSEQ publishes no terms for them.

These download from their original source only.

**hs38DH:** `GRCh38_full_analysis_set_plus_decoy_hla` includes HLA sequences
from IMGT/HLA, whose [licence](https://www.ebi.ac.uk/ipd/imgt/hla/licence/)
asks for permission to redistribute modified data. Its copy in `v0.1.0`
predates #701 and is under review there.

GitHub limits each release file to 2 GiB. Every genome's BGZF file fits.
