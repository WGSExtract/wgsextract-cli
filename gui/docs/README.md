# WGS Extract Desktop App — User Documentation

Welcome to the user documentation for the **WGS Extract desktop app** (the graphical front-end built on top of [WGS Extract CLI](../../docs/README.md)).

![WGS Extract desktop app](readme-screenshot.png)

> **Web version:** These guides are also published as rendered HTML at
> [wgse.bio/wgsextract-cli/app/docs/](https://wgse.bio/wgsextract-cli/app/docs/index.html)
> (CLI documentation is at [wgse.bio/wgsextract-cli/docs/](https://wgse.bio/wgsextract-cli/docs/index.html)).

## Core guides

| Guide | What it covers |
| --- | --- |
| [Basic and Advanced mode](modes.md) | Switching between Basic and Advanced mode, how hidden controls behave, and output folder defaults |
| [Reference Data](reference-data.md) | Installing, verifying, and removing annotation databases, Gene Maps, liftover chains, VEP caches, and research panels |
| [Reference Genomes](reference-genomes.md) | Installing, indexing, verifying, and deleting reference genomes; cached vs. looked-up download sizes |
| [Tools](tools.md) | Viewing mandatory and optional bioinformatics tools, installing/uninstalling Pixi-managed tools, and manual tool notes |
| [Settings](settings.md) | Setup, appearance & language, bug reporting, workspace folders (`config.toml`), and moving libraries to another drive |
| [Privacy and network access](privacy.md) | What stays local, automatic update checks, interactive download prompts, IP/metadata visibility, and how to run completely offline |

## Viewing genomes and genes

| Guide | What it covers |
| --- | --- |
| [Built-in genome viewer](genome-viewer.md) | Inspecting local BAM/CRAM alignments, VCF variants, RefSeq genes, and optional remote server viewing |
| [Gene page](gene-page.md) | Viewing one gene's RefSeq transcripts, exon coverage, and annotated variants offline |

## Analysis page guides (coming soon)

Detailed guides for the analysis and workflow pages (FASTQ, BAM, VCF, Extract,
Microarray, Ancestry, Annotate, and Workflow) will be added once the upcoming
analysis-page restructure is finished. Until then:

- Hover over any button, field, or table row in the app to see its built-in tooltip.
- For the underlying bioinformatics behavior, flags, and file formats, see the
  [WGS Extract CLI documentation](../../docs/README.md).

## More resources

- [Release changelog](CHANGELOG.md) — what changed in each desktop app release.
- [CLI documentation](../../docs/README.md) — command-line usage, file formats, hardware guidance, and scientific references.
- [Developer documentation](../devdocs/README.md) — building the app, bundle schemas, architecture, and packaging (also [gui/README.md](../README.md)).
