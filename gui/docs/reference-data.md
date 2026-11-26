# Reference Data

The **Reference Data** page (near the bottom of the sidebar) manages the shared
annotation databases, coordinate maps, liftover chains, microarray templates,
and research panels used by WGS Extract workflows, the [Genome Viewer](genome-viewer.md),
and the [Gene Page](gene-page.md).

Reference **genomes** (FASTA sequences and aligner indexes) live on their own
[Reference Genomes](reference-genomes.md) page.

## Reference Data Library folder

The **Reference Data Library** path at the top of the page shows where these
datasets are stored as an absolute path (by default inside your users workspace, `~/wgs-extract`).

- You can point this folder to another location, such as a larger external SSD.
- When you change the folder path, the app offers three choices:
  - **Move:** Copies existing reference data to the new folder and updates `config.toml`.
  - **Change Setting Only:** Updates `config.toml` to use the new folder without moving or deleting existing files.
  - **Cancel:** Leaves the path unchanged.
- This works in both [Basic and Advanced mode](modes.md).

## Installed and available datasets

The table queries `wgsextract ref status` locally — opening the page does not
contact the network. Each row shows its status (**Installed** or **Missing**),
version, target genome build, what it is used for, and either its on-disk size
(when installed) or approximate download size (when missing). Hover over a row to
see its data source, publishing organization, and license summary.

Library Bootstrap always shows the current archive's download size. If the
installed version differs, the versions are listed as installed / available.
An older installation is marked **Update available**.

| Dataset | Purpose | Notes |
| --- | --- | --- |
| **Library Bootstrap** | Core liftover chains, microarray chip templates, and N-count tables | Marked **Required** for microarray, liftover, and reference helpers. |
| **Gene Map** | RefSeq gene spans and exon/transcript models (GRCh37 & GRCh38) | Powers the [Gene Page](gene-page.md), Genome Viewer gene search & transcript tracks, and gene filtering. |
| **dbSNP (hg38 / hg19)** | NCBI dbSNP common human SNPs (~37M rsIDs) and offline SQLite lookup index | Enables offline rsID annotation and lookup. Large download; asks for confirmation before downloading. |
| **Cytobands (hg38 / hg19)** | UCSC chromosome banding tables | Draws the chromosome ideogram track in the [Genome Viewer](genome-viewer.md). |
| **Microarray Targets** | Genome-wide GRCh37 & GRCh38 SNP target files | Built locally from Library Bootstrap chip templates. |
| **AADR 2M** | Allen Ancient DNA Resource (v66.p1) 2M SNP marker panel | Optional research panel for ancient DNA exports; must be installed explicitly. |
| **yBrowse** | GRCh38 Y-chromosome SNP names and haplogroup annotations | Optional sites-only Y-SNP annotation resource (updated daily upstream). |
| **Ensembl VEP cache** | Local transcript cache for offline `vep-rs` / Ensembl VEP annotation | Supports **Download**, **Verify**, and **Delete** row actions. |
| **SpliceAI** | Precomputed splicing impact scores (GRCh38 MANE Select) | Used for splicing impact annotation and research ACMG workflows. |
| **AlphaMissense** | Missense pathogenicity scores (GRCh37 & GRCh38) | Research-only pathogenicity scoring; not validated for clinical use. |
| **PharmGKB / ClinPGx** | Pharmacogenomic variant–drug annotations | Used for pharmacogenomics annotation. |
| **ClinVar** | Clinical significance and disease assertions (GRCh37 & GRCh38) | Used by variant annotation, Gene Page, and personal reports. |
| **REVEL** | Ensemble missense pathogenicity scores (GRCh37 & GRCh38) | Research pathogenicity scoring. |
| **gnomAD** | Population allele frequencies (v2.1.1 / v4.1) | Used for rare-variant filtering, Gene Page, and Genome Viewer tracks. |
| **CADD** | Whole-genome deleteriousness scores (GRCh37 & GRCh38) | Research deleteriousness annotation. |
| **GERP++** | Evolutionary conservation RS scores (bigWig) | Conservation annotation and Genome Viewer track. |
| **PhyloP** | 100-way vertebrate conservation scores (bigWig) | Conservation annotation and Genome Viewer track. |

## Row actions

- **Install / Download:** Runs the corresponding `wgsextract ref ...` or `wgsextract vep download` command and streams progress to the terminal pane. Larger downloads ask for confirmation first.
- **Verify:** Checks integrity of an installed resource (for example, the Ensembl VEP cache).
- **Delete:** Asks for confirmation before removing an installed dataset from disk to free space.

> **Interactive prompts:** If you start an analysis or open the [Gene Page](gene-page.md)
> without a required dataset (such as **Gene Map**), WGS Extract lets you
> **Install and Continue** directly from the prompt instead of switching to this
> page first. See [Privacy and network access](privacy.md) for what each download
> sends.

## Advanced mode options

Switching to [Advanced mode](modes.md) reveals extra controls below the table:

- **Annotation build** (`hg38` / `hg19`) for CADD, GERP++, and PhyloP downloads.
- **gnomAD release** (`2.1.1` / `4.1`).
- **yBrowse snapshot** file path, to install from a local snapshot instead of fetching online.
- **VEP species**, **VEP version**, **VEP mirror** (`ebi` / `uk`), and **VEP assembly** (`GRCh37` / `GRCh38`).
- **Custom download section:** Download an arbitrary public reference file from a URL to a chosen local path (`wgsextract ref download`).
