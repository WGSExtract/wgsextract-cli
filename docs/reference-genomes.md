# Reference genomes: where they come from

`wgsextract ref library --install CODE` (and the app's Library page)
downloads a genome from the first source in the catalog
(`src/wgsextract_cli/assets/reference/seed_genomes.csv`) that works.
For most genomes that is a copy in this repository's GitHub releases. The
original source (NCBI, EBI, UCSC, AWS) is listed after it as an `-Alt` row,
and the install falls back to it automatically. The original servers have
been slow and unreliable, so the copies come first (#701).

For annotation resources, gene maps and benchmark downloads, see
[download mirrors and source rights](resource-mirrors.md). Their separate
audit deliberately excludes restricted or unclear third-party data.

## Listing the library

`wgsextract ref library --list` shows every catalog row (one per source) with
its state in your library: installed (with its `.fai`), unindexed, incomplete
(a `.partial` download) or missing, and its size on disk. `--json` prints it as
the result manifest's `details` (fields in [api.md](api.md), `library_list`).

`--remote-sizes` also looks up each genome's download size from its source
(HTTP `HEAD`, FTP `SIZE`; each network wait times out after 4 s, and the
lookup gives up after 8 s). Sizes found are kept for 30 days in the library's `.remote-sizes.json`,
and later listings show them without going online.

## Detecting the genome build

Build detection prefers sequence-dictionary signatures and chromosome lengths
over name hints. When only a file or reference-folder name is available, only
that name is considered, not its ancestor folders. Build identifiers must be
separate tokens (for example `sample_GRCh38.bam`), with catalog variants such
as `hs37d5`, `hs38d1s` and `CHM13v2.0` recognized. An unknown build stays unknown;
placing it under a folder named `hg19` or `chm13` does not identify it.

FASTA descriptions and assembly tags, and VCF reference/assembly metadata,
remain build evidence. Tool command-history lines in VCF headers are not.

## Verification and gene maps

`ref verify` checks gzip integrity, the FASTA index and catalog
sequence-dictionary signature, failing on mismatch.
`ref library --install` also verifies the signature after indexing.
The catalog's `md5` is a sequence-name/length dictionary signature,
**not a FASTA content checksum**. `ref count-ns` writes `_ncnt.csv` and
`_nbin.csv` beside the FASTA, or in `--outdir`.

Gene-name filtering (`--gene`) requires `ref gene-map` maps for hg19/hg38;
T2T is rejected. Rebuild maps installed by older versions with `ref gene-map`.

### Offline cytobands

The library viewer can display chromosome ideograms from UCSC's public
`cytoBand` data. Install the appropriate assembly explicitly:

```sh
wgsextract ref cytobands --build hg19 --download
wgsextract ref cytobands --build hg38 --download
```

Use the same `config.toml` as the desktop, or pass `--ref /path/to/reference-library`.
Without `--download`, the command validates the installed asset offline;
missing or invalid data produces an error naming it and the explicit command.
It does not inherit the persistent download opt-in. Tables are installed as
`ref/cytobands_hg19.tsv` and `ref/cytobands_hg38.tsv` in the configured reference
library. The viewer never downloads them.
Validation-only runs record the table as an input and leave its installation
manifest unchanged. The default is the configured reference library even when
`reference_fasta` points elsewhere; only an explicit `--ref` FASTA selects its
containing library. Repair hints retain the selected library and configuration.

The installer checks pinned SHA-256 snapshots of `cytoBand.txt.gz`, gzip
integrity, five columns, zero-based half-open coordinates, continuous chromosome
coverage, band names and stains before publishing the table atomically.
Only primary chromosomes 1–22, X and Y are retained; UCSC's unbanded alternate
contigs are validated but excluded. Partial downloads use the standard
URL-bound resume helper. UCSC changing a snapshot requires a reviewed pin update,
not accepting unchecked replacement bytes.
Downloads and publication are serialized per assembly. Completed archives use
private staging without replacing user archives; new public tables follow the
process umask. Installed tables must stay within the library, use LF or CRLF
row endings and contain at most 10,000 primary bands, matching viewer validation.

Source URLs are under
`https://hgdownload.soe.ucsc.edu/goldenPath/{hg19,hg38}/database/cytoBand.txt.gz`.
The [UCSC download terms](https://hgdownload.soe.ucsc.edu/downloads.html#terms)
permit use of downloaded data for any purpose except directory-specific
exceptions; see the [database README](https://hgdownload.soe.ucsc.edu/goldenPath/hg38/database/README.txt)
and assembly credits. These are annotation data, not UCSC's separately licensed
Genome Browser software. Credit UCSC and its assembly contributors. No new
redistribution mirror is introduced.

## Download safety

Reference HTTP downloads resume only explicit `.partial` files bound to
their source URL by a `.partial.source` hash sidecar; segmented downloads
keep their own metadata. Unknown legacy partials or changed URLs restart.
Refreshing a complete file starts from zero and preserves it until replacement
finishes and passes available checksums, then publishes atomically.
Diagnostic URLs omit userinfo and queries, while requests keep the original
URL. MD5-pinned benchmark files enter the dev cache only after verification.

## The test genome

`wgsextract ref test-genome --install` downloads and verifies the small
HG00096 dataset. Its human-readable status identifies it as **WGS Extract hg19
mini benchmark genome**, the same title shown by the app. The status and path
are also available in the JSON result's `details`.

## Shared-library permissions

On POSIX systems, new `.lock` and `.guard` coordination files use `0666`
filtered by the process umask. For a group-writable reference library, use a
shared group, writable directories and a umask such as `0002` for every writer.
Existing guards keep their permissions. If an older guard denies access, ask
its owner or administrator to grant the shared users read/write access to the
named file and access to its directory. Do not delete guards while any run may
be using the library: replacing a guard can break locking between processes.

## Deleting a genome

`wgsextract ref library --delete CODE` (or the file name, such as
`hs38.fa.gz`) deletes a catalog genome from the library's genomes folder, as
the app's Library page does: the FASTA, a partial download, its `.fai`,
`.gzi` and `.dict`, the bwa and bwa-mem2 indexes, dense suffix-array files,
their run manifests and an interrupted index build's staging folders. Its N
counts stay, since the library bootstrap ships them for every catalog genome.
Only files in the genomes folder itself are deleted; a link there is removed,
not what it points to. While another run builds its bwa index or dense
suffix array, the delete is refused. With `--json`, the result's `details`
lists the deleted paths and the bytes freed (fields in [api.md](api.md),
`library_delete`).

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
is CC BY-ND, explicitly asks people not to mirror the data, and requires
permission to redistribute modified data. The composite FASTA is therefore
excluded from the new benchmark mirrors (the coordinate-only `.fai` may be
copied). Its copy in `v0.1.0` predates #701 (reference-genome mirrors) and
remains under review there.

GitHub limits each release file to 2 GiB. Every genome's BGZF file fits.
