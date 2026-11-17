# Download mirrors and source rights

Explicit downloads prefer byte-identical GitHub release copies when
`src/wgsextract_cli/assets/resource_mirrors.json` contains published SHA-256
pins. Merely inspecting resources does not contact GitHub. Each downloaded
part and the assembled file are verified before installation. An unavailable
or corrupt mirror produces `MIRROR_FALLBACK` and tries the original URL.
Immutable upstream copies must match the published snapshot too. Entries with
`upstream_mutable: true` (ClinVar weekly files and refGene maps) may fall back
to changed upstream bytes, with an `UPSTREAM_VERSION_DRIFT` warning rather than
failing every time the original publisher updates them. An explicitly supplied
SHA-256 remains strict even for mutable sources. Cancellation never starts a
fallback download.

Mirror parts use stable `DEST.mirror/partNNNN` staging paths so interrupted,
URL-bound `.partial` transfers resume on the next attempt. Completed parts stay
in `DEST.mirror/assembled`: a restart verifies its prefix against each pinned
part SHA-256, truncates incomplete/corrupt trailing bytes, and appends only the
remaining parts. It does not download the verified prefix again.
An existing upstream
`DEST.partial` takes precedence over starting a mirror. A successful mirror
install removes its staging directory and orphaned upstream partial markers;
a successful upstream fallback also removes all abandoned mirror staging.

## Redistribution audit (2026-11-17)

The JSON inventory records each individual URL, attribution, source grant,
version, audit-time size (HTTP HEAD, not a content pin), and the reason for its
distribution mode. This audit does not resolve the separate source-rights work
in [#864 (licence and provenance review)](https://github.com/WGSExtract/wgsextract-cli/issues/864).
Public access alone is not permission to redistribute.

| Resource | Decision | Size before splitting | Grant / evidence |
| --- | --- | --- | --- |
| ClinVar GRCh37/38 | Mirror a dated snapshot | ~202 MB each | [NCBI molecular data](https://www.ncbi.nlm.nih.gov/home/about/policies/): no restrictions on use or distribution; acknowledge NCBI ClinVar. |
| REVEL (ANNOVAR hg19/hg38) | Upstream only | ~415 MB each | [REVEL downloads](https://sites.google.com/site/revelgenomics/downloads): non-commercial/source-specific terms; no clear unrestricted grant for these ANNOVAR copies. |
| phyloP 100-way hg19/hg38 | Mirror | ~9.6–9.9 GB each | Directory [hg19 README](https://hgdownload.soe.ucsc.edu/goldenPath/hg19/phyloP100way/README.txt) and [hg38 README](https://hgdownload.soe.ucsc.edu/goldenPath/hg38/phyloP100way/README.txt) permit public use; [UCSC download terms](https://hgdownload.soe.ucsc.edu/downloads.html#terms) allow any purpose except directory exceptions. Retain UCSC / Pollard / Siepel attribution and the assembly-credits caveat. |
| gnomAD 2.1.1 complete exome VCFs | Upstream only pending component review | ~63 / 92 GB | [gnomAD terms](https://gnomad.broadinstitute.org/terms) grant CC0 for primary data but warn of third-party annotation restrictions. These full VEP-annotated VCFs are not cleared as a whole; this is not a restriction on primary AF facts. |
| gnomAD v4.1 joint sites, all 24 contigs | Mirror | ~900 GB total; up to ~72 GB/contig | [gnomAD terms](https://gnomad.broadinstitute.org/terms), [source text](https://raw.githubusercontent.com/broadinstitute/gnomad-browser/main/browser/about/policies/terms.md): CC0 primary data, acknowledge gnomAD v4.1 and do not reidentify participants. The joint-sites header contains population counts/frequencies, not CSQ / SpliceAI / REVEL / CADD predictions. No separately restricted prediction annotations are copied. |
| SpliceAI MANE v1.4 and index | Upstream only | Large VCF plus index | [Illumina licence](https://github.com/Illumina/SpliceAI/blob/master/LICENSE): CC BY-NC scores/models, PolyForm Strict code. An openly accessible Ensembl URL does not remove these terms. hg19 remains manual. |
| AlphaMissense hg19/hg38 predictions | Mirror, retaining the licence conflict explanation | ~600 MB each | The original 2023-08-03 files still embed **CC BY-NC-SA 4.0**. DeepMind subsequently relicensed the predictions database to **CC BY 4.0 on 2024-03-13**, in [commit fe2dc845](https://github.com/google-deepmind/alphamissense/commit/fe2dc845f93310abd6c1b0e8955d7a96c2144d66) and its [README grant](https://github.com/google-deepmind/alphamissense/blob/fe2dc845f93310abd6c1b0e8955d7a96c2144d66/README.md#alphamissense-predictions-license). Redistribution relies on that explicit later grant, not the stale embedded header or the code's Apache licence. The manifest and release NOTICE explain both; copied headers remain unmodified. Copyright 2023 DeepMind Technologies Limited; Cheng et al., Science 2023, doi:10.1126/science.adg7492; not validated for clinical use. |
| GERP GRCh38, Ensembl 114 / 91 mammals | Mirror | ~9.6 GB | [Ensembl data disclaimer](https://raw.githubusercontent.com/Ensembl/ensembl-webcode/release/116/htdocs/info/about/legal/disclaimer.html): project-generated data available without restriction; retain Ensembl Compara attribution. This does not redistribute the GERP program. |
| GERP hg19 `All_hg19_RS.bw` | Upstream only | ~7.6 GB | [Third-party UCSC track](https://genome.ucsc.edu/cgi-bin/hgTrackUi?db=hg19&g=All_hg19_RS): an unrestricted grant for this Stanford/Cooper score file was not independently established. |
| RefSeq gene maps hg19/hg38 | Mirror snapshots | ~8 MB each | [UCSC database README](https://hgdownload.soe.ucsc.edu/goldenPath/hg38/database/README.txt), [NCBI molecular-data terms](https://www.ncbi.nlm.nih.gov/home/about/policies/). RefGene annotations are not the licensed UCSC software or liftOver chains. |
| CADD v1.7 SNVs, indels, indexes, MD5 files | Upstream only | SNVs ~85 GB/build | [CADD downloads](https://cadd.gs.washington.edu/download): non-commercial applications; commercial use requires a licence. Do not copy even the ancillary files into the new release. |
| PharmGKB / ClinPGx clinical annotations and variants | Upstream only | ZIP snapshots | [ClinPGx policy](https://www.clinpgx.org/page/dataUsagePolicy): CC BY-SA 4.0 **plus research/no-sale restrictions**, not an unrestricted CC grant. Preserve existing download provenance handling. |
| ACMG SF 3.2 / 3.3 gene lists | No new copying; existing release only | Small TSVs | [Lee et al. source](https://pmc.ncbi.nlm.nih.gov/articles/PMC12318660/); underlying Elsevier/table-expression review remains in #864 (licence and provenance review). Existing GitHub URLs and explicit pins are unchanged, not re-cleared by this audit. |
| Reference bootstrap | No new copying; existing release only | ~170 MB | Already on GitHub. Mixed component rights (vendor lists, chains, presets) remain under #864 (licence and provenance review); do not duplicate based on the repository code licence. |
| Mini benchmark archive | Existing release and SHA-256 only | See inventory | Already on GitHub `v0.1.0`, explicitly pinned. No second archive or expanded source-rights claim. |
| 1000 Genomes real-1x / NYGC real-30x benchmark alignments, indexes and hs37d5 reference | Mirror, excluding the HLA-containing GRCh38 FASTA below | Small indexes through multi-GB CRAM/reference assets | [1000 Genomes reuse](https://www.internationalgenome.org/1000-genomes-summary/#g1k_data_reuse), [IGSR disclaimer](https://www.internationalgenome.org/IGSR_disclaimer/), [NCBI molecular-data terms](https://www.ncbi.nlm.nih.gov/home/about/policies/). Published, openly consented HG00096 data; acknowledge 1000 Genomes / IGSR and NYGC ERP114329 / ERR3240114; no participant reidentification. The coordinate-only GRCh38 `.fai` remains eligible. |
| Benchmark real-30x `GRCh38_full_analysis_set_plus_decoy_hla.fa` | Upstream only | ~3.3 GB | Includes IPD-IMGT/HLA sequences under CC BY-ND. [IMGT's licence](https://www.ebi.ac.uk/ipd/imgt/hla/licence/) explicitly asks people **not to mirror** its data and requires permission for modified distributions. Public hosting by 1000 Genomes does not remove that caveat; see the hs38DH note in [reference-genomes.md](reference-genomes.md). |
| Reference-genome catalog (including upstream alternatives) | Existing mirrors unchanged | See reference catalog | Audited separately in [reference-genomes.md](reference-genomes.md) and #701 (reference-genome mirrors). Restricted/unclear genomes remain upstream only. |

`UPSTREAM_ONLY` failures identify the resource, original URL and rights reason.
Retry the upstream download or supply a licensed local copy. WGS Extract does
not bypass authentication, accept terms on the user's behalf, or install
download dependencies automatically.

## Publishing and activating a snapshot

Like the reference-genome publisher, **Mirror download resources** is a manual
GitHub Actions workflow. Large payloads are streamed on CI, not a developer's
laptop. Every asset is at most **1,900,000,000 bytes** (below GitHub's 2 GiB
limit). Splitting is binary concatenation, not recompression or a scientific
transformation. Each part uses a separate bounded HTTP Range request; a short
connection resumes at its last received byte. The response is closed **before**
uploading that part, so an upload cannot leave a long upstream connection idle.
Sizes, Content-Range and source ETag/Last-Modified preconditions protect the
snapshot; local part hashes are checked against GitHub's uploaded SHA-256/size
before the final whole-file SHA-256 record is published.

Normally only one part is staged, uploaded, then deleted: no per-contig 72 GB
scratch copy is needed. If a host ignores Range, the publisher can stage a
complete file **only up to 10 GiB**, with space for a part plus a 2 GiB free-disk
reserve. Larger/non-fitting downloads fail clearly with `RANGE_REQUIRED`, not
an unbounded runner-disk download.

Audited licence/terms bytes are committed in `src/wgsextract_cli/assets/resource_terms/`, with
source URLs, retrieval dates and SHA-256 in `sources.json`. Publication uses
these verified snapshots without depending on live terms-page availability
(including Creative Commons legalcode pages). A live fetch, when needed, uses
the payload User-Agent and normalizes GitHub README links to raw content; HTTP
errors can fall back to an available committed snapshot. New records retain
the audit-snapshot provenance. Keep the explicit AlphaMissense relicensing
explanation in the NOTICE as well as the original data header.

The publishing step explicitly uses Bash with `pipefail`, so logging through
`tee` cannot turn a Python failure into a successful job. Failure evidence is
uploaded even when the job fails.

1. After the publishing workflow reaches the default branch, dispatch it with
   release tag `download-resources-1`. Start with `gene-map-hg19,gene-map-hg38`
   to smoke-test publication, then the remaining approved IDs. Empty selection
   selects every approved resource. Non-redistributable entries cannot publish.
2. The release keeps per-resource JSON records (upstream URL, version,
   retrieval date, complete-file SHA-256/size, ordered part SHA-256/sizes),
   attribution notices and source-licence snapshots. Data/records are never
   overwritten. A failed transfer can rerun against the same snapshot; if an
   upstream file changes, choose a **new release tag**, not `--clobber`.
   Completed, verified records are returned before any terms/payload fetch;
   rerunning `download-resources-1` preserves the first dispatch's 11 completed
   resources rather than rebuilding or overwriting them.
3. Download the release's `*.record.json` assets into `out/mirror-records/`.
   Run `pixi run python scripts/mirror_download_resources.py activate
   --records out/mirror-records --tag download-resources-1`, inspect the pins,
   and submit the updated JSON catalog in a follow-up PR. Until then these
   unpublished entries deliberately retain upstream behavior.
4. Run the mirror behavior and remote-resource checks before activating pins.
   Keep the issue open until the approved assets have been published and their
   pins activated. Do not claim that merging the publisher alone mirrors data.

Nightly URL checks cover **all upstream URLs**, including restricted resources
and catalog alternatives, plus every published mirror part. GitHub's published
asset SHA-256 and size are compared with local pins without downloading ~1 TB
each night. Complete byte checks run during publication and every on-demand
mirror download. PR URL probes compare resolved inventories at base and head
and visit only added/changed URLs; an unrelated upstream outage cannot block a
PR. See [CI routing](ci_routing.md).
Changes to annotation resource code retain live SpliceAI format/score tests;
changes to gene-list code or manifests retain live gene-list downloads and pin
checks on PRs, even when their URLs did not change.
