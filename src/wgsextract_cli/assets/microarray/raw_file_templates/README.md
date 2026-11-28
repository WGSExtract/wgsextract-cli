# Microarray templates shipped with the CLI

Most templates come with the reference library (`ref bootstrap`). These are the
ones it lacks; `microarray` searches here last. A body file holds one marker per
line (rsID, chromosome, position, tab-separated) and is gzipped; the head file
is written as-is at the top of the output.

Source rights for the exact vendor marker compilations, template headers and
corrections are **unspecified**. The donations documented below establish
provenance, not permission from the vendors to redistribute their material.
The openSNP archive's CC0 grant covers that source, not a separate vendor grant.
See the [source-rights record](https://github.com/WGSExtract/wgsextract-cli/blob/main/src/wgsextract_cli/assets/resource_terms/rights.json).

Most bodies are stored as a delta (`NAME.<suffix>.delta.gz`) against another
shipped body, which keeps the wheel small: its first line is `#base<TAB>BASE`,
then `=N` copies BASE's next N rows, `-N` skips them and
`+ID<TAB>chromosome<TAB>position` inserts a row; BASE's remaining rows are
dropped. A base may itself be a delta (at most four deep). The extractor
verifies that a delta reproduces the export before writing it.

## Letter-named variants and further vendors (CombinedKit v5)

Variants are named by letter, as h600.org's
[Microarray File Formats](https://h600.org/wiki/Microarray+File+Formats) names
them, never by size or date. Exports within a few thousand markers of each
other are one sub-minor variant; similar minor variants are merged into one
template (the union, in position order when every export is, as Ancestry's and
MyHeritage's are; otherwise the first export's rows in order, a later export's
markers after the unchanged row preceding them in that export, as for
tellmeGen. A marker ID keeps the first export's coordinates, so a marker the
older export places differently, even on another chromosome, takes the newer
export's position).

- Source: private donated exports from the H600 Project's sample set, one or
  two per variant. Only marker IDs and positions are kept, no genotypes,
  headers or identifiers.

| Format ID (`--formats`) | Template | Variant, exports merged | Markers | Build | Stored as |
|---|---|---|---:|---|---|
| `23andme_v4` | `23andMe_V4` | v4c + v4b + v4a merged | 622,474 | GRCh37 | full |
| *(internal v3)* | `23andMe_V4_v3` | classic v0.2.0 v4a (frozen for CombinedKit v3) | 601,885 | GRCh37 | delta vs `23andMe_V4` |
| `ancestry_v2ab` (`ancestry_v2`) | `Ancestry_V2ab` | v2a (2016) + v2b (2018) | 670,296 | GRCh37 | delta vs `Ancestry_V2cd` |
| `ancestry_v2cd` | `Ancestry_V2cd` | v2d (2019) + v2c (2018) | 700,164 | GRCh37 | full |
| `ancestry_v2e` | `Ancestry_V2e` | v2e (2026) | 434,818 | GRCh37 | delta vs `Ancestry_V2cd` |
| `ftdna_v2` | `FTDNA_V2` | v2d + v2c + v2b + v2a merged | 725,297 | GRCh37 | full |
| *(internal v3)* | `FTDNA_V2_v3` | classic v0.2.0 v2d (frozen for CombinedKit v3) | 720,449 | GRCh37 | delta vs `FTDNA_V2` |
| `ftdna_v4` | `FTDNA_V4` | v4 (2026) | 758,990 | GRCh37 | full |
| `ftdna_v2y` | `FTDNA_V2Y` | v2y, the separate Y file | 15,408 | GRCh38 | full |
| `ftdna_v3y` | `FTDNA_V3Y` | v3y, the separate Y file | 10,578 | GRCh38 | full |
| `ftdna_v4y` | `FTDNA_V4Y` | v4y, the separate Y file | 20,000 | GRCh38 | full |
| `myheritage_v3` | `MyHeritage_V3` | v3 male (2026) + female (2025) | 593,345 | GRCh37 | full |
| `tellmegen` | `TellMeGen_V1` | corporate male + male | 779,518 | GRCh37 | full |
| `genera` | `Genera_V1` | template export | 639,813 | GRCh37 | delta vs `MyHeritage_V3` |
| `mthfr` | `MTHFR_V1` | female template (no Y) | 640,418 | GRCh37 | full |
| `meudna_v1` | `meuDNA_V1` | v1 (2021) | 632,150 | GRCh37 | delta vs `MyHeritage_V3` |
| `meudna_v2` | `meuDNA_V2` | v2 template (2022) | 654,017 | GRCh37 | delta vs `Genera_V1` |
| `selfdecode` | `SelfDecode_V1` | 2023 | 686,763 | GRCh37 | full |

- FTDNA's Y files are delivered on GRCh38 and are never merged with its
  autosomal files. Their markers are lifted to GRCh37 with UCSC's
  `hg38ToHg19.over.chain.gz` (downloaded once into the reference library) to
  find their calls, and written at their GRCh38 positions, like the NCBI36
  `FTDNA_V1_Affy`.
- The SelfDecode export's header declares GRCh38, but its positions are GRCh37
  (rs3131972 at 752,721, rs114525117 at 759,036); it was extracted with
  `--ignore-header-build`.
- `23andMe_V4_v3` and `FTDNA_V2_v3` (plus classic `Ancestry_V2` in the
  bootstrap) are internal frozen v0.2.0 marker lists retained solely so
  CombinedKit v3 stays frozen while `--formats 23andme_v4`, `ftdna_v2` and
  `ancestry_v2` emit the merged v4a–c, v2a–d and v2ab layouts.
- Sub-minor variants without their own template: LivingDNA v2's 660,634-marker
  female export (a subset of `ldna_v2`), and minor date/size variants of 23andMe
  v5, Ancestry v2 and MyHeritage v2. `info chip` still names a file's letter
  variant.
- Not added: 23andMe v2 and FTDNA v1 (NCBI36) already have templates, and
  National Geographic Geno 2.0's files lack positions (v2 has no position column;
  v2+ has NCBI36 positions only for autosomal/X with Y and MT given as SNP
  names without positions); LivingDNA's Y and MT files list derived SNPs only,
  so they are no template of what was tested.
- Rebuild, e.g.: `python scripts/microarray_template_from_raw.py
  AncestryDNA_v2d.txt AncestryDNA_v2c.txt Ancestry_V2cd --delta-base auto
  --overwrite` (rebuild a template's dependants after it).

## 23andMe_V2 (`--formats 23andme_v2`, also `23andme_v1`)

- Source: openSNP data dump 2017-12-08, CC0 1.0
  (https://archive.org/details/opensnp_data_dumps), a 23andMe
  export of 2017-05 on build 37 (574,518 markers). Only marker IDs and
  positions are kept, no genotypes.
- Every build-37 v1/v2-era export in the dump with this marker count has the
  identical list, and the files don't name their chip; within an export period
  v1 and v2 files share one list, so `23andme_v1` is an alias.
- Rebuild: `python scripts/microarray_template_from_raw.py <raw file> 23andMe_V2 --overwrite`.

## Nucleus_V1 (`--formats nucleus`)

- Source: private donated Nucleus array export of 2025 (665,107 markers). Only
  marker IDs and positions are kept, no genotypes. Nucleus exports have no
  header, so there is no head file.
- Rebuild: `python scripts/microarray_template_from_raw.py <raw file> Nucleus_V1 --overwrite`.

New headers use generic vendor boilerplate and the vendor's column-label format;
no source comments, names, account links, kit/sample IDs or individual timestamps
are copied. Bodies contain exactly three columns: marker ID, chromosome and
position. No genotype or call-status filtering is used, including for no-call rows.
Unplaced markers (position zero) stay in the vendor layout and produce no-calls;
they are excluded only from the one-based genotyping target.
Gzip bodies have neither a source filename nor a timestamp in their metadata.

Rebuild with `python scripts/microarray_template_from_raw.py <private raw export>
<template name> --overwrite`; omit `--overwrite` when adding a new format.
The extractor validates the source schema and declared reference build, rejects
existing templates by default, and writes only generic headers and marker lists.
Raw exports and identifying provenance must stay outside version control.

## AADR v66.p1 (`aadr_1240k`, `aadr_ho`, `aadr_1240k+ho`)

These are research panels, not consumer-vendor templates. Their shared,
marker-only table is `../aadr/v66.p1.tsv.gz`. It retains AADR's SNP ID
(including non-rs IDs), chromosome, genetic position in Morgans, one-based
GRCh37 physical position, reference allele and variant allele, plus an HO
membership flag. The last two allele columns are **AADR's** reference/variant
coding, not an inferred ancestral allele or a call from an individual.

- Source: Mallick et al., *The Allen Ancient DNA Resource (AADR): A curated
  compendium of ancient human genomes*, DOI
  [10.7910/DVN/FFIDCW](https://doi.org/10.7910/DVN/FFIDCW),
  Harvard Dataverse dataset version **14.0**, released **2026-06-08**,
  panel release **v66.p1**; retrieved **2026-10-30**.
- Terms: the pinned Dataverse version declares **CC0 1.0**
  (<https://creativecommons.org/publicdomain/zero/1.0/>). See also the release
  README, <https://dataverse.harvard.edu/api/access/datafile/13994530>.
  Cite the AADR release and Mallick et al. (2024), *Scientific Data* 11, 182,
  DOI [10.1038/s41597-024-03031-7](https://doi.org/10.1038/s41597-024-03031-7),
  when using the resource. No `.geno`, `.ind` or individual annotations were
  downloaded or packaged.
- 1240K: **1,233,013** markers, including X and Y;
  <https://dataverse.harvard.edu/api/access/datafile/13994514>
  (`v66.p1_1240K.aadr.patch.PUB.snp`), SHA256
  `1189e253b82058822fddabc4966c1448dea70373a96c40106e720bedd93b55bf`.
- Human Origins (HO): **584,131** markers, including X and Y;
  <https://dataverse.harvard.edu/api/access/datafile/13994527>
  (`v66.p1_HO.aadr.patch.PUB.snp`), SHA256
  `c0d565ee9e6a6edf30c2063430ee4f828e5db45f26d4747fd76f19eee650a5de`.
- Every HO marker is in this release's 1240K panel, with identical ID,
  coordinates, genetic position and alleles. Consequently **1240K+HO has
  1,233,013 markers**, not the sum of the panel sizes. It is an explicit
  union target, not a third invented panel.
- The deterministic gzip asset is **14,149,959 bytes**, SHA256
  `7ecbbc06a876c57236c36b4779f881648dbe7754851c42fd12d625ada8269b05`.
  Sharing one table avoids shipping HO twice. The incremental compressed size
  is about 13.5 MiB, comparable to the existing packaged chip lists; the wheel
  remains below PyPI's standard 100 MB limit. No runtime panel download is
  needed. The UCSC GRCh37-to-GRCh38 chain, when needed, is reused read-only from
  the reference library or obtained through the existing reference-download
  helper into the run's unique output subdirectory.

Rebuild from the **two SNP metadata files only**:

```sh
python scripts/microarray_aadr_markers.py 1240K.snp HO.snp \
  src/wgsextract_cli/assets/microarray/aadr/v66.p1.tsv.gz
```

The builder rejects duplicate/out-of-order positions, invalid alleles and panel
disagreements, and validates HO subset membership before writing anything.
It preserves SNP IDs and allele order and adds no source filenames or timestamps
to gzip metadata. See [AADR export and validation](../../../../../docs/aadr.md)
for file conventions, allele checks, and compatibility limitations.

## External AADR 2M resource

`aadr_2m` uses the **unbundled**, explicitly installed v66.p1 2M SNP metadata
from the same CC0 Dataverse version 14.0 release:
<https://dataverse.harvard.edu/api/access/datafile/13994517>.
The file `v66.p1_2M.aadr.patch.PUB.snp` contains **2,142,271** markers and is
134,963,073 bytes; SHA256
`39312150ce88ec757a88e52a7921ca7fd1190d9cce11cfe39c756ea5737b7a9b`.
Every current 1240K/HO ID, physical coordinate and allele pair is included,
but **1,200,251** legacy 1240K genetic distances differ. The panel's metadata
are kept separate; existing bundled 1240K/HO data are not changed.
See [explicit installation, attribution and map differences](../../../../../docs/aadr.md#opt-in-aadr-2m-v66p1).

## corrections.tsv

Position fixes applied when reading the reference library's templates. 102 MT
markers in 23andMe_V4, V5 and V35 are at Yoruba (UCSC hg19 chrM) positions in
an otherwise rCRS list, so they missed the kit's calls. Each is corrected only
when a 2025 23andMe export gives the marker another position and the
template's position, lifted with hg19ToHg38's chrM chain, is exactly that.

- They apply in both `--quirks` modes: they fix the template, not a vendor
  report. A [strand quirk](../revisions/README.md#strand-quirks-strandtsv)
  can't name a marker they relocate.
- Rebuild: `python scripts/microarray_template_corrections.py <reflib> <23andMe raw file>`.

## FTDNA_V1_Affy (reference library)

This template's positions are NCBI36 (hg18). Its markers are lifted to GRCh37
with UCSC's `hg18ToHg19.over.chain.gz`, downloaded once into the reference
library, both for the genome-wide target and to find each marker's call.
Without the chain, calls are matched by rsID.

## Which revision a template is

Each template is one marker list, so one chip revision. The
[chip-revision catalogue](../revisions/README.md) fingerprints every template
and the revisions observed in public exports, and `wgsextract info chip
--list` shows which template each revision matches, or the closest one with
the markers it adds and drops. Run `wgsextract info chip --input <raw file>` to
check which revision a kit is before choosing `--formats`.
