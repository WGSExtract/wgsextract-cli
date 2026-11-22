# Privacy and network access

Ordinary analysis, inspection and reporting stay on this computer. Defaults
must not attempt a connection, even when a reference, tree, gene list or
model is missing. There is no telemetry, usage reporting or background CLI
update check. Missing dependencies are reported, never installed by analysis.
Runtime Pixi invocations use both `--no-install` and `--frozen`: skipping
installation alone would still allow lockfile resolution and registry queries.
Incomplete installed runtimes fail with setup guidance, rather than falling back
to a potentially different CLI on the host's PATH.

Explicit download/install commands may use the network. Other commands need
`--download` before fetching missing public data. Without permission they
name the missing data and explain how to confirm the download. Installed,
verified data is reused offline. `--no-download` overrides a persistent opt-in:

```toml
# config.toml; default is false
allow_reference_downloads = false
```

`ref cytobands` validates installed tables offline and requires its own
explicit `--download` to fetch missing UCSC `cytoBand` data, even if the
persistent setting is enabled. The request contains only the hg19/hg38 public
assembly path on `hgdownload.soe.ucsc.edu`; no sample-derived data is sent.
The genome viewer uses installed tables only and never requests UCSC data.

`analyze comprehensive` runs these same guarded subcommands. Provision missing
data first with the download command named in the error, or explicitly opt in
through `config.toml` before running the comprehensive workflow.
The desktop exposes the same default-off setting under Settings → advanced
settings → **Allow missing public-data downloads**, with the request disclosure
in its tooltip.
Lineage and microarray pages also offer an unchecked per-run download option.
Fresh-install automation plans select it explicitly; ordinary runs do not.

The option is shared by the CLI and typed Python/server parameters. The
read-only MCP surface refuses download permissions. A server job needs both
job authorization and download permission; merely starting the server grants
neither to an analysis. Do not enable downloads globally on a machine that
must remain offline.

## What goes where

Public-data requests reveal your IP address, requested asset/model identifiers,
HTTP headers and timing. GitHub downloads can redirect to GitHub's release
asset CDN; checksum discovery may request `api.github.com`. Audited
[resource mirrors](../src/wgsextract_cli/assets/resource_mirrors.json), when
available, use byte-identical public assets and the same consent. A mirror is
not permission to download. No reference download sends sample files,
genotypes or variant coordinates.

`vep run` uses a local cache and fails if the required Ensembl cache is
missing. Choosing `--engine ensembl` does not authorize sending variant
queries. The separate `vep download` command fetches public data only.

| Opt-in | Destination | Request data |
| --- | --- | --- |
| `qc ancestry --download` | GitHub WGSExtract releases/CDN | Pinned HGDP+1KG reference filename, not sample ancestry or positions |
| `microarray --download` | `hgdownload.soe.ucsc.edu`; GitHub WGSExtract releases/CDN as mirror | Whole hg18→hg19 / hg19→hg38 chain filenames; sample coordinates are lifted locally and never sent |
| `vcf pharmcat --download` | `zenodo.org`, via PharmCAT's reference downloader (record 7288118) | Public GRCh38 reference archive/index filenames, not pharmacogenetic variants or sample files |
| `ref index --aligner bwa-mem2 --download` | Published hs38DH index on Amazon S3 | Reference index filenames, not read/sample information; without permission the index is built locally if RAM permits |
| `typing hla --download` | IPD-IMGT/HLA's public distribution, via T1K (GitHub ANHIG/IMGTHLA) | Allele-database identifiers, not reads or HLA calls |
| `lineage mt-haplogroup --download` | GitHub `genepi/phylotree-fu-rcrs`, `genepi/phylotree-rcrs-17`, `genebygene/mitotree` releases/CDN | Selected tree ID/version, not haplogroup or variants |
| `lineage y-haplogroup --download` | GitHub WGSExtract releases/CDN; configured custom-tree host | Tree ID/version and selected reference build/table; custom-tree URL, not sample markers/calls |
| `report --download` | GitHub WGSExtract releases/CDN | Selected public gene-list filename/version, not findings |
| `ref recheck --online` | `ftp.ncbi.nlm.nih.gov` (ClinVar's weekly release listing for the recorded build) | A plain GET of the public listing URL; no sample name, paths, positions, genotypes or findings. Without `--online` (or with `--answers FILE`) nothing is contacted; see [recheck-and-delta.md](recheck-and-delta.md) |
| `score pgs --download` | `www.pgscatalog.org`, `ftp.ebi.ac.uk`, GitHub WGSExtract releases/CDN | PGS ID, coordinate build, public scoring weights and calibration/ancestry asset names, not sample scores or genotypes |
| `basecall --download` | Oxford Nanopore's Dorado model distribution | Model/modification names and POD5 chemistry metadata used to select a model. No signal/read files are uploaded. For offline use pass a local `--model` directory without `--modified-bases`. |
| `ref library --remote-sizes` / desktop **Look up sizes** | Each catalog entry's host, including UCSC, NCBI and EBI | HTTP(S) HEAD or FTP SIZE requests for public reference filenames with missing or at least 30-day-old cached sizes; no sample data |
| `realign --input https://…` | The URL's host and redirects | Explicitly selected remote alignment/index; HEAD, index download and sample-derived genomic byte ranges. Use a local file to avoid disclosing access patterns. Server clients cannot supply URLs. |
| `benchmark --dataset real` / `--dataset-url` | Selected public benchmark host | Dataset/index URLs; this explicitly selected dataset mode downloads public test data |
| Desktop **Check for Updates…**, then **Download** | Configured GitHub release/update endpoints/CDN | Application version, platform/architecture and asset requests, not sample information |
| Desktop **Setup**, reference/dataset download actions | GitHub, Pixi/Conda/PyPI registries and each selected resource publisher | Selected package versions, platform and public dataset identifiers, not sample information |
| Desktop genome viewer **Connect and view remote file**, after transfer consent | The operator-selected server URL displayed in the viewer | Selected server-side sample/index/reference paths, adjacent FASTA index paths and genomic byte offsets; the backend authenticates with the server token and receives selected genomic/reference bytes |

Opening an external report/documentation link is an explicit browser action;
the browser's own privacy settings apply. Generated reports do not load remote
scripts, fonts or images automatically. The desktop's UI and job server talk
over authenticated loopback connections on this computer. The optional
[remote genome viewer](../gui/docs/genome-viewer.md#view-files-on-a-remote-server)
uses that local backend to proxy explicitly selected server file ranges.
Importing or restoring its private TOML configuration makes no remote request.
The backend keeps the server token outside browser-delivered data; the viewer
receives revocable per-file grants. HTTPS retains certificate validation;
redirects and cookie forwarding are disabled. Plain HTTP exposes credentials
and genomic traffic, so use an operator-managed TLS proxy or encrypted tunnel.
Opening the desktop's Reference Genomes page reads only local/catalog metadata
and cached sizes, including old sizes. **Look up sizes** requires confirmation
of the destinations and requests before any remote size lookup.

## Audit for issue #1052 (offline defaults)

Baseline: main `28f56cb9`, 2026-11-17. This table groups commands that share
the same execution path; options not listed inherit their command's local
behavior. Classification describes the baseline, before this change.

| Command/option | Network on baseline? | Classification / disposition |
| --- | --- | --- |
| `help`, version, `config`, `sample`, `report-issue`, `info`, coverage, chip inspection | No | Local; no install/update/size probe; support builds a redacted report but does not upload it |
| `extract` and `bam` families, `repair`, `align`, local-input `realign`, `panel`, `sign`, `analyze batch-gen`, VCF conversion/calling/filtering/comparison/phasing/imputation, QC fake-data/fastp/FastQC/MultiQC/VCF/somalier/contamination, non-HLA typing, Y-STR, PET, methylation | No | Local; absent references, annotation data, panels and tools give guidance |
| CRAM decoding by htslib/Java tools | No remote fallback intended | Local-only `REF_PATH` retained; no Java reference-download property; missing references fail |
| Liftover/chip coordinates | Coordinate conversion is local; missing microarray chains were fetched | **Default-violation** found in review: missing whole-chain downloads now require `microarray --download`; pyliftover receives only local chain files |
| VEP run/verify, chain-annotate, ClinVar/REVEL/gnomAD/SpliceAI/etc. annotation | Ensembl VEP could fall back to online annotation if its cache directory was missing | Local caches/indexes only; a missing cache now stops `vep run` and names `vep download` |
| DeepVariant/Clair3/Parabricks containers and native callers | No registry pull | Installed images/models required; `--pull=never`; no runtime dependency installation |
| `ref download`, genome-library install, bootstrap, annotation resources, gene maps, microarray targets, ancestry/AADR/somalier/imputation/yBrowse/gnomAD-SV install, ACMG data preparation | Yes | Explicit network operations; retain downloads and mirrors |
| Reference/dataset listing/status/verify/count/delete | No | Local metadata; remote sizes only with the opt-in above |
| `ref index --aligner bwa-mem2` on hs38DH | Yes if published index absent | **Default-violation** found in independent review: fetching prebuilt indexes now requires `--download`; local index building stays available |
| `vep download`, `example-genome download` | Yes | Explicit network operations; retain downloads |
| `qc ancestry`, calibrated `score pgs` | Yes when reference absent | **Default-violation**: require `--download` |
| `score pgs --trait/--pgs-id` | Yes, including repeated runs | **Default-violation**: require `--download` for absent Catalog files; reuse local copies |
| `typing hla` without local index | Yes | **Default-violation**: require `--download` for IPD-IMGT/HLA |
| `vcf pharmcat` without its reference FASTA | Yes, in PharmCAT's downloader subprocess | **Default-violation** found in review: require `--download` before starting the subprocess |
| `lineage mt-haplogroup`, `lineage y-haplogroup`, custom remote Y trees | Yes when data absent | **Default-violation**: require `--download`; cache reuse remains local |
| `analyze comprehensive` | Inherits first-use tree downloads from lineage steps | **Default-violation**: child commands now refuse missing data without consent; pre-provision it or explicitly opt in through config |
| `report` with clinical findings and absent selected gene list | Yes | **Default-violation**: require `--download`, not silent partial placement |
| Dorado `basecall` model names/modifications | Yes | **Default-violation**: require `--download` or a local model directory |
| Remote realignment input, remote sizes, benchmark real-data selection | Yes | Explicit **opt-in**; request data disclosed above |
| `serve` HTTP/MCP, desktop local backend/path picker | Loopback only | Explicit local transport, no automatic remote call; stdio MCP opens no listener |
| Desktop launch | GitHub update check | **Default-violation**: only the explicit update action checks (automated update testing remains explicitly enabled by its harness) |
| Desktop Reference Genomes page rendering | Automatically ran `ref library --list --remote-sizes`, querying catalog hosts when sizes were missing/stale | **Default-violation** found in independent review: the automatic data source now uses cached sizes only; a separate, confirmed **Look up sizes** action opts in |
| `deps check` / shared optional-tool discovery and version probes | Pixi could resolve a changed manifest | **Default-violation**: frozen lockfile queries/probes, without environment installation |
| Installed CLI/desktop analysis launchers | Pixi could complete/update environments or resolve a changed manifest | **Default-violation**: always `--no-install --frozen`; Setup remains explicit |
| CLI/app telemetry | None found | No default telemetry endpoint |

PR #1019 (ACMG site-restricted annotation data) is coordinated separately:
its proposed remote tabix reads reveal sample-derived variant byte ranges to
Google Cloud Storage and Ensembl. That must never be an automatic fallback,
including on a missing local database. Its explicit sample-position permission
is distinct from permission to download a complete public reference. On the
audited main revision, `vcf acmg` requires local prepared databases; the
SpliceAI auto-fetch belongs to the explicit `ref acmg-data` preparation command.

Regression coverage blocks Python DNS/socket connections while representative
local commands process tiny synthetic data, checks first-use refusal and
explicit consent, and retains real samtools/bcftools. Subprocess network
isolation is used where the host permits an unprivileged network namespace;
socket monkeypatches alone do not sandbox C extensions or child processes.
