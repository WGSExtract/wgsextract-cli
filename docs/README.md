# Documentation by task

Start with the [README quick start](../README.md#quick-start), the
[documentation site](https://wgsextract.github.io/wgsextract-cli/), or
`wgsextract help --all`. This index lists every top-level guide; the
[command reference](commands.md), [API](api.md) and
[configuration reference](configuration.md) are generated from their sources.

## Set up and run

| Guide | Task |
| --- | --- |
| [Installation](installation.md) | Install, pin versions, update or uninstall |
| [Migrating from v4](migrating-from-v4.md) | Map WGS Extract v4 buttons to current pages/commands and read what changed |
| [Running commands](usage.md) | Discover help, understand outputs/progress, log runs or report a problem |
| [Privacy](privacy.md) | Review network permissions and destinations |
| [Windows](windows.md) | Use native Windows tools and GATK |
| [Linux ARM64](linux-arm64.md) | Check supported tools and qualification |
| [Hardware](hardware.md) | Choose CPU/native/GPU acceleration and align safely |
| [Resource estimates](resource_estimates.md) | Plan RAM/disk and benchmark workflows |
| [Apple Silicon 16 GB](apple-silicon-16gb.md) | Read constrained-memory measurements |
| [Configuration](configuration.md) | Set paths, resources, temp placement and keep-awake |
| [Sample workspace](sample-workspace.md) | Organise the dataset library, example genomes and sample facts |
| [Reference genomes](reference-genomes.md) | List, install, verify and delete references; inspect source rights |
| [Calling reference](calling-reference.md) | Choose and verify a reference for calling |
| [Download mirrors](resource-mirrors.md) | Review resource provenance and redistribution rights |
| [Step resume](step-resume.md) | Recover interrupted workflows |

## Export and interpret

| Guide | Task |
| --- | --- |
| [Commands](commands.md) | Find a command starting from your input |
| [Microarray](microarray.md) | Export consumer files with explicit call/identity policies |
| [Microarray indels](microarray-indels.md) | Supply sequence-defined insertion/deletion probes |
| [CHM13 microarrays](microarray-chm13.md) | Project qualified CHM13 input to hg19/rCRS |
| [Microarray benchmarks](microarray-benchmarks.md) | Review concordance and parallel parity |
| [AADR](aadr.md) | Export research panels as PLINK/EIGENSTRAT |
| [Lineage](lineage.md) | Extract Y/mtDNA, call haplogroups/STRs, set up yBrowse and review data licences |
| [FTDNA VCF repair](ftdna-vcf-repair.md) | Turn legacy Big Y FILTER expressions into declared IDs |
| [Variant workflows](variants.md) | Call/annotate, analyse trios and type pharmacogenes/repeats |
| [Variant confidence](variant-confidence.md) | Understand technical evidence labels and caller accuracy |
| [Built-in genome viewer](../gui/docs/genome-viewer.md) | Inspect local reads and variants, or open an extraction result at its locus |
| [Caller ploidy](caller-ploidy.md) | Handle sex, PARs and policy masks |
| [Reference evidence](reference-evidence.md) | Distinguish assessed reference from absent calls |
| [Genotype QC](genotype-qc.md) | Interpret depth, homozygosity and no-call warnings |
| [ONT/PacBio](ont.md) | Align, basecall, call, phase and read methylation |
| [DeepVariant ARM64](deepvariant-arm64.md) | Build and validate the native CPU image |
| [VEP engines](vep-engines.md) | Annotate with vep-rs or Ensembl VEP |
| [SV frequencies](sv-frequency.md) | Match structural variants to population frequencies |
| [ACMG](acmg.md) | Use research-only classification with fastVEP |
| [Research panels](panel.md) | Write and check a research panel file of genes, filters, sites and haplotypes |
| [Personal report](personal-report.md) | Read findings with links to evidence, not diagnoses |
| [Contamination](contamination.md) | Screen for another person's DNA |
| [Relatedness](relate.md) | Check sex, relatedness and continental ancestry |
| [Ancestry](ancestry.md) | Estimate HGDP+1KG component proportions |
| [Runs of homozygosity](roh.md) | Find ROH with population frequencies; review isodisomy flags |
| [Imputation](imputation.md) | Review phasing/imputation workflows and limits |
| [Polygenic scores](pgs.md) | Calculate research scores and inspect their limitations |

## Integrate

| Guide | Task |
| --- | --- |
| [Professional pipelines / MultiQC](interop.md) | Hand off BAM/CRAM, bring back VCFs and combine QC |
| [Python API](api.md) | Call typed operations from Python |
| [Local server](server.md) | Use HTTP/JSON or MCP with allowed-path restrictions |
| [Result manifest](result-manifest.md) | Consume structured results, warnings and provenance |
| [Output contracts](output-contracts.md) | Know which output fields are stable, experimental or internal |
| [Variant table](variant-table.md) | Query TSV/SQLite output |
| [Parquet](parquet.md) | Use typed tables with DuckDB, Polars or other tools |

## Contribute and maintain

| Guide | Task |
| --- | --- |
| [Developer setup](developer_setup.md) | Prepare environments, change code and run tests |
| [Install testing](install_testing.md) | Exercise installation and removal |
| [CI routing](ci_routing.md) | Understand selection, certification and readiness |
| [CI failures](ci_failures.md) | Observe failures and apply audited retries |
| [Compiler caches](compiler_caches.md) | Understand build-cache boundaries |
| [Site generation](site_generation.md) | Edit and build website sources |
| [Releasing](releasing.md) | Release the CLI and desktop app together |
| [Bootstrap compression results](bootstrap_compression_results.md) | Read historical compression measurements |

Release notes: [unreleased](release-notes/unreleased/README.md),
[v0.8.9](release-notes/v0.8.9.md), [v0.8.8](release-notes/v0.8.8.md), [v0.8.7](release-notes/v0.8.7.md),
[v0.8.6](release-notes/v0.8.6.md), [v0.8.5](release-notes/v0.8.5.md).
Documentation inputs under `data/` include the
[configuration guide source](data/configuration-guide.md) (rendered into
`configuration.md`) and the [ONT HG002 manifest](data/ont_hg002_manifest.json).
Desktop app documentation starts at [gui/README.md](../gui/README.md).
