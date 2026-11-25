# Reference Genomes

The **Reference Genomes** page (near the bottom of the sidebar) manages the
human reference genome FASTA files and their indexes (`.fai`, `.dict`, and
aligner indexes) used for CRAM decoding, read alignment, variant calling, and
viewing reads in the [Genome Viewer](genome-viewer.md).

For scientific background on choosing a build (GRCh37/hg19, GRCh38/hg38, or
T2T-CHM13) and data provenance, see the CLI
[Reference genomes guide](../../docs/reference-genomes.md).

## Reference Genome Library folder

The **Reference Genome Library** path at the top of the page shows where FASTA
files and indexes are stored. Reference genomes and aligner indexes take several
gigabytes each, so you can place this library on a secondary or external drive.

Changing the folder opens the same **Move / Change Setting Only / Cancel** dialog
used across the app, in both Basic and Advanced mode.

## Catalog columns and statuses

Opening the page runs `wgsextract ref library --list` locally using bundled
catalog metadata and cached sizes — it does **not** contact remote servers on
page load.

Each row displays:

- **Name:** Human-readable genome description, with a **Recommended** badge on preferred builds.
- **Code:** Short catalog code used by the CLI (for example `hs37d5`, `hs38`, `hs38DH`, `chm13v2`).
- **Tag:** Build family tag (such as `37`, `38`, or `T2T`).
- **Source:** Upstream provider (NIH/NCBI, EBI, UCSC, T2T Consortium, etc.).
- **SN:** Sequence naming convention (`chr` prefix vs. numeric contigs).

Each genome has one of four statuses:

| Status | Meaning |
| --- | --- |
| **Installed** | The FASTA file and required indexes are present and ready to use. |
| **Unindexed** | The FASTA file is present, but one or more indexes need to be built. |
| **Incomplete** | A previous download was interrupted and can be resumed or deleted. |
| **Missing** | Not installed locally yet; available to download from the catalog. |

## Row actions

The icon buttons on each row adapt to that genome's status:

- **Download / Install** (missing rows): Downloads and installs the genome (`wgsextract ref library --install <code>`).
- **Resume** (incomplete rows): Resumes an interrupted download.
- **Index** (unindexed rows): Builds FASTA/aligner indexes locally (`wgsextract ref index`).
- **Verify** (installed or unindexed rows): Verifies sequence dictionary checksums and index integrity (`wgsextract ref verify`).
- **Count Ns** (installed or unindexed rows): Counts unknown (`N`) bases per chromosome (`wgsextract ref count-ns`).
- **Delete** (installed, unindexed, or incomplete rows): Asks for confirmation and removes the genome and its indexes to reclaim disk space.

> **Interactive prompts:** When you run an action on a CRAM file or call variants
> and the matching reference genome is not installed yet, WGS Extract detects the
> exact required reference from the file header and offers **Install and Continue**
> right in the confirmation dialog.

## Download sizes and privacy

Opening **Reference Genomes** reads only local metadata and any sizes already
cached in `.remote-sizes.json` (even if the cache is older than 30 days). No
network lookup runs automatically when you view the page. When a **Look up sizes**
action is used, it confirms the destination hosts first before sending lightweight
HTTP `HEAD` / FTP `SIZE` requests for uncached entries; no sample data is ever
sent. See [Privacy and network access](privacy.md).

## Advanced mode options

In [Advanced mode](modes.md), an **Aligner index** dropdown appears below the
genome table so you can choose whether **Index** builds default indexes, `bwa`
indexes, or `bwa-mem2` indexes.
