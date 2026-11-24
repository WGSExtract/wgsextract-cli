# Result manifests, `--json` and exit codes

Commands describe each run in a JSON document that follows the versioned schema
[`wgsextract.result/1`](../src/wgsextract_cli/assets/schemas/wgsextract.result.1.schema.json)
(JSON Schema draft 2020-12, shipped in the wheel). The document lists what the
command read and wrote, what it measured and warned about, and how it ran. Use
it instead of parsing log text or guessing output file names.

The bundled schema is generated from the actual writer dataclasses in
`core/results.py`, through `core/result_schema.py`. Run
`scripts/gen_output_schemas.py` (or `just regen`) to update it; `--check` detects
drift. Defaulted and nullable fields remain required because the writer emits
them. This generation does not add runtime validation or change tolerant readers.
The same dataclass graph generates the complete TypeScript writer contract in
`gui/app/shared/generated/result-records.ts` through `scripts/gen_server_contracts.py`.
The GUI's normalized presentation types derive shared fields from it, but its
parser still treats external/compact documents as unvalidated input.

- **Manifest file.** A command that writes outputs also writes
  `<output>.manifest.json` next to them, atomically. The name is the first
  output file's name with any compression suffix (`.gz`, `.bgz`, `.zst`,
  `.bz2`, `.xz`) removed. The type extension stays, so outputs that differ only
  in type keep separate manifests. `sample_MT.vcf.gz` gives
  `sample_MT.vcf.manifest.json`, `sample_MT.fasta` gives
  `sample_MT.fasta.manifest.json`, and `sample_mtDNA.bam` gives
  `sample_mtDNA.bam.manifest.json`. Multi-output commands such as `microarray`
  name their manifest after the run (`<sample>_microarray.manifest.json`;
  `<vcf>_<sample>_microarray.manifest.json`, with a `sample` metric, for a
  sample chosen from a multi-sample VCF). `vcf trio` always writes
  `trio.manifest.json`, whichever modes ran.
  Report-only commands (`info`, `info chip`, `ref status`, `ref library --list`)
  and deletions (`ref library --delete`, `vep delete`, `--delete` options)
  write no file, except `info --format`, whose manifest is named after the first table
  (`<input>_chromosomes.parquet.manifest.json`).
  A failed run that registered outputs still writes its manifest, with
  `status: "failed"`. A `vcf` annotator (`clinvar`, `gnomad`, `cadd`,
  `revel`, `phylop`, `gerp`, `spliceai`, `alphamissense`, `pharmgkb`,
  `sv-freq`) that fails before registering any output, such as on a missing
  resource file, still records the attempt, so `report` can show it: as
  `<output>.failed.manifest.json` (`clinvar_pathogenic.vcf.failed.manifest.json`),
  leaving an earlier run's manifest in the folder as it was. The next manifest
  written under the output's own name removes it. Commands that name their
  manifest after the run, such as `vcf trio` and `analyze`, write it on failure
  as on success. A manifest never replaces this run's `--log-file`; when
  the two would share a name, the manifest is skipped with a warning.
- **`--json`.** This global option prints the same document to stdout
  *instead of* the human-readable result, for every command. Logs, progress
  and anything external tools print go to stderr, so stdout holds exactly one
  JSON document, even when the run fails. Argument-parsing errors are the
  exception: argparse rejects them before the command starts, prints the usage
  text and exits 2 without a document.
- **`--result-file PATH`.** This global option writes the same document to
  PATH and leaves stdout as it is without `--json`, for programs that show a
  command's human-readable output and also read its result, such as the
  desktop app. It is not listed in `--help`. Like `--json`, it writes nothing
  when argparse rejects the arguments.
- **`--card`.** This global option (or `result_card = true` in config.toml;
  `--no-card` overrides it) also writes `<name>.card.html` beside the
  manifest: `sample_MT.vcf.card.html` beside `sample_MT.vcf.manifest.json`.
  The card is a page to view, print to PDF or share. It shows the command,
  its status, what the command printed, metrics, warnings and output files,
  with the WGS Extract and tool versions, the input files with their size and
  an abbreviated fingerprint, the reference and when the run happened. Files
  and URLs appear by name only. In the printed text, messages and metrics,
  URLs, the run's files, its path options as given (missing or relative too)
  and any other path that exists on the computer are cut to their names, and
  the home folder to `~`, so sharing a card doesn't reveal your folders;
  the manifest beside it keeps the full paths. The page is self-contained: no
  scripts, nothing loaded from a network. A report-only command that reads
  a sample, such as `info`, which otherwise writes no manifest, writes both
  into its output folder (with no `--outdir`, where outputs go by default:
  `output_directory` or `wgse/` beside the input), named after its input and
  command (`sample.bam_info.card.html` and `sample.bam_info.manifest.json`); one
  that reads no sample, such as `ref status`, writes no card. The card is
  listed in the manifest's `outputs` with role `result_card`. A failed run's
  card shows the error. Help and settings commands (`help`, `config`, `deps`,
  `sample`, `serve`, `report-issue`) write no card, and neither does a
  `--status` run or a run whose card would replace its `--log-file`. `wgsextract serve --card`
  gives every job it runs a card, inside the folders the server may use; a
  job started with `?card=1` (or `card=0`) gets one (or not) whatever the
  server's default; otherwise jobs follow `result_card`. A read-only MCP
  session writes none. The card and manifest are written together under a lock, so
  concurrent runs on one sample can't pair one run's card with another's
  manifest; a successful run removes an earlier failed attempt's card with
  its manifest. Internal steps (`analyze`, `benchmark`) write no cards. The desktop app asks for cards only for its analysis
  actions and shows each one when the run ends.

## Document

| Field | Meaning |
| --- | --- |
| `schema` | Always `"wgsextract.result/1"`. |
| `command` | The subcommand path, e.g. `"vcf snp"` or `"lineage y-haplogroup"`. |
| `status`, `exit_code`, `error` | `ok`, `failed` or `cancelled`; the process exit code; and `null` or `{kind, message}`, where `kind` is one of `failure`, `usage`, `missing_dependency` or `cancelled`. Some errors include a stable message `code`. |
| `inputs`, `outputs` | File records: `path` (absolute), `kind` (`bam`, `cram`, `vcf`, `fasta`, `fastq`, `tsv`, `json`, `directory`, ...), `role` (what the file is to this command, e.g. `input`, `snps`, `mt_bam`, `haplogroup_report`), `exists`, `size_bytes`, `mtime` (UTC ISO 8601), `fingerprint`, `index` (the `.bai`/`.crai`/`.csi`/`.tbi`/`.fai` beside it, if any) and per-file `metrics`. |
| `metrics` | Flat headline numbers and labels, e.g. `variants`, `haplogroup`, `sex`, `avg_depth`. |
| `warnings` | Structured warnings: `code`, `message` and scalar `details`. |
| `details` | The command-specific structured result, or `null`. |
| `manifest` | Absolute path of the manifest file this run wrote, or `null` (including when writing it failed). |
| `run` | This run's place in its workflow: `id`, `root`, `parent`, `step` and `children`. See [Run lineage and artifacts](#run-lineage-and-artifacts-pilot). |
| `provenance` | `version` (package), `argv` and `params` (the command line and the parsed options as the command used them, after `--genome` selection; values of options whose names contain token, secret, password, credential or api key are redacted, and URLs there, and in error and warning messages, lose their user name, password, query string and fragment), `tools` (versions of the external tools the command ran), `reference` (`path`, `build`, `fingerprint`), `platform`, `python`, `started`, `finished` and `elapsed_s`. |

Tool versions are probed only when a document is emitted, and only for tools the
run actually launched.

`inputs` lists only the files the run reads. A command that reads one VCF
(`vcf annotate`, `vcf clinvar` and the other annotators, `vcf filter`,
`vcf table`, `vcf to-fasta`, `vcf trio`, `qc vcf`) records just that one, as
`vcf_input` or `input` after the option that named it, even when both
`--vcf-input` and `--input` are given; its `provenance.params` show the other
as `null`. `vcf trio --proband` reads the proband (role `proband`) instead of either. `vcf phase`
reads both: its reads as `input` and its calls as `vcf_input`.

`align` and `realign` report the effective lossless output profile
(`default`, `archive` or `calling`) in `metrics.output_profile`; see
[output profiles](ont.md#lossless-output-profiles).

### Repeat calls

`typing str` records `details.repeat_calls` in the manifest and in `--json`
output. These are the same locus-sorted rows as the `repeat_summary` output
(`<stem>.str.tsv` or `<stem>.str.parquet`), not the caller's own JSON.
`details.requested_loci` contains the sorted `--loci` IDs, or `[]` for a full
catalog run. Targeted runs append a digest of the IDs to their `.str` prefix
so their outputs have their own manifests. An unnamed LongTR or STRkit locus
uses its catalog interval (`contig:start-end`, zero-based with an exclusive
end) as its `locus` ID.

- `allele_1`, `allele_2`, `ci_1_low`, `ci_1_high`, `ci_2_low` and
  `ci_2_high` are integer repeat counts. `allele_1_reads`, `allele_2_reads`
  and `clipped_reads` are integer read counts. An uncalled allele or a bound
  or support count the caller could not provide is `null` (blank in TSV),
  not zero. A hemizygous call has no second allele. No-call variants from
  the caller are retained with any coverage it reported.
- `coverage` is caller-reported locus coverage, rounded to one decimal.
  `clipped_reads` counts reads that meet the unsized-expansion warning rule
  for long-read callers; zero means no such signal was reported.
- `status` retains the readable classification and caveats. `confidence`
  is `low` for a no-call, incomplete or low-support call, a below-threshold
  estimate whose CI reaches the pathogenic range, an unchecked expansion
  motif or evidence of an unsized expansion. `not_assessed` does not mean
  high confidence.
  `normal_max` and `pathogenic_min` are nullable integers (`-` in TSV);
  custom loci without thresholds are sized but not classified.
- `normal_repeats`, `flagged_repeats` and `unconfirmed_repeats` summarize
  the calls. An unconfirmed call can also be flagged, for example when
  clipped reads suggest an expansion. The personal report reads this
  manifest without running a caller.

### Fingerprints

Inputs, outputs and the reference carry a `wgse-fp1:<sha256 hex>` fingerprint.
It is a SHA-256 of the file size plus the whole file when it is at most 3 MiB,
or of three 1 MiB blocks (head, middle, tail) when it is larger. That costs at
most 3 MiB of reads, even for a 100 GB BAM. Hashing the whole of such a file
would take minutes. The mtime is not hashed, so a copied file keeps its
fingerprint. Only regular files are read. A named pipe, `/dev/stdin` or a process
substitution is recorded with `size_bytes` and `fingerprint` set to `null` and
is never opened, so the command still gets its whole stream.
`align` separately spools one-shot read streams before validating and mapping
them; fingerprint collection itself still never consumes stream input.

A fingerprint identifies a file cheaply ("is this the same BAM as last time?").
It is not an integrity check: an edit outside the sampled blocks that keeps the
size goes unnoticed. Use `wgsextract sign` for full-file digests.

### Run lineage and artifacts (pilot)

The pilot of [#1182](https://github.com/WGSExtract/wgsextract-cli/issues/1182)
links the manifests of a BAM/VCF workflow, such as `vcf snp` followed by
`analyze comprehensive --vcf-inputs`, without a separate lineage file.

- **`run`.** Every document has a run record. `id` is a random 32-character
  identifier of this run. `root` is the first run of its workflow (this
  run's `id` when nothing started it). `parent` and `step` name the run
  that started this one as a step, or are `null`. `analyze` starts its
  steps' commands as steps of itself, and `vcf chain-annotate` does the same
  for its annotators. A workflow lists its steps in `children`: `step`, the
  `run` and `command` that did the step's work, `status` (`ok`, `failed`,
  `cancelled`, or `reused` when the step journal skipped it), and the
  journal's `reason`. A reused step names the run that did its work in the
  earlier run, and so do the artifacts of the files it wrote. A step done inside the workflow's own process names the
  workflow's run. A step whose command failed is listed with its `run`,
  whose own failed manifest names this run as its `parent`.
- **`artifact`.** A BAM, CRAM, VCF or BCF in `inputs` or `outputs` has an
  `artifact` record. Other files have none.

  | Field | Meaning |
  | --- | --- |
  | `origin` | `this_run`: written by this run or one of its steps. `recorded`: written by an earlier run, named in `producer`: a manifest in the input's folder lists it with the same fingerprint (its own `<file>.manifest.json` first; a command writes one manifest for all its outputs), or a reused step's earlier run wrote the output. `external`: nothing is known about its history. |
  | `lifecycle` | `final` (a deliverable), `intermediate`, `cache` or `temp`; `null` when unknown. |
  | `retained` | `false` when the run deleted the file on purpose. |
  | `digest_kind` | `full` when the `fingerprint` read the whole file (3 MiB or less), else `sampled`. |
  | `assembly` | `hg19`, `hg38` or `t2t`, from the chr1 length in the header; `null` when it isn't recognised. |
  | `reference` | Fingerprint of the FASTA the file was made with, for this run's outputs and for recorded inputs. |
  | `samples` | The BAM's `@RG SM` names, or the VCF's sample columns. |
  | `index` | `path` and `state`: `ready`, `missing`, `stale` or `mismatched`; `null` for an uncompressed VCF. `stale`: older than the file and pointing at compressed blocks the file no longer has, as after the file was rewritten. An older index whose blocks are all in the file is `ready`, since downloads and copies often leave the index the older file. A CRAM index (`.crai`) isn't read: only its presence is checked. `mismatched`: another sequence dictionary, blocks that aren't the file's, or an unreadable index. Every index a reader may pick is checked (htslib picks a `.csi` before a `.bai` or `.tbi`), and the first unusable one is reported. |
  | `producer` | `run`, `root`, `command` and `step` of the run that wrote it. |
  | `sources` | The files it was made from: `path`, `role`, `fingerprint`, `producer` (run), `lifecycle` and `retained`. |

  The facts come from the file itself: its header (read without running a
  tool; a CRAM's header isn't read, so its assembly and samples are
  unknown), its index and its fingerprint. A producer is taken from the
  manifest beside the file only while that manifest still lists the same
  fingerprint. A copied, edited or third-party file is `external`, and
  nothing about its history is assumed. An output's `sources` are the
  run's BAM/CRAM/VCF/BCF inputs, unless the command names them. `vcf call
  --merge` names the reads and its per-type VCFs, which it deletes after
  merging. They stay in `sources` with `lifecycle: temp` and `retained:
  false`, and they are not listed in `outputs`. `analyze`'s findings name
  only the VCF they were found in.
- **Consumers refuse unusable inputs before they run.** The error's
  `code` names the problem, and the run exits 1:

  | Code | Raised by | Problem |
  | --- | --- | --- |
  | `assembly_mismatch` | BAM/CRAM variant callers (`vcf snp`, `vcf indel`, `vcf call`, ...), `analyze comprehensive` | The input is on another genome build than the reference or the other inputs. |
  | `reference_mismatch` | the same | Same build, but the contig names, lengths or MD5s differ from the reference. |
  | `index_stale`, `index_mismatch` | the same | The BAM/CRAM index is `stale` or `mismatched`. A `mismatched` VCF index is refused too. A missing or older VCF index is rebuilt, as before. |
  | `index_missing` | BAM/CRAM variant callers with `--region` | The region is read through the index. Calling the whole file still streams an unindexed BAM. |
  | `sample_mismatch` | `analyze comprehensive --input BAM --vcf-inputs VCF` | A VCF that wgsextract called from a BAM or CRAM (`recorded`, with the reads among its `sources`) names none of the reads' samples. For any other VCF, whose sample may only be named differently (an `external` VCF, or one filtered or annotated from it), this is a warning with the same code, and the run continues. Recorded producers are read only from manifests a server job may read. |

  Nothing in this pilot reaches the network or installs anything. Other
  commands record artifacts but don't check them yet.

### Warning codes

| Code | Raised by | Meaning |
| --- | --- | --- |
| `low_coverage` | `info --detailed`, variant calling, lineage, `microarray`, `methylation`, `typing smn` | Depth is below a threshold (`depth`, `threshold`). |
| `aligner_not_calibrated` | `typing smn` | The @PG aligner isn't bwa-like (BWA, DRAGEN, Isaac), which SMNCopyNumberCaller is calibrated on (`aligner`). |
| `possible_silent_carrier` | `typing smn` | Two SMN1 copies with g.27134T>G, associated with a 2+0 silent carrier (`g27134tg_copy_number`). |
| `uneven_coverage`, `status_uncertain` | `typing smn` | Coverage MAD above 0.11 (`coverage_mad`); the caller couldn't settle carrier/affected status. |
| `sex_unknown` | `info --detailed`, `typing repeats` | The sex could not be inferred from X/Y coverage. |
| `long_reads` | `info --detailed` | Long-read data; the short-read tools give poor results. |
| `short_read_n50` | `info` | Long reads' N50 is under 3 kb, which suggests degraded DNA or a failed library (`read_n50`, `exact`, `threshold`). |
| `low_mapped_read_fraction` | `info --detailed` | Under 95% of long reads map: contamination, a failed library or the wrong reference (`mapped_read_pct`, `threshold`). |
| `mapped_read_fraction_unavailable` | `info --detailed` | Long reads' mapped percentage could not be estimated: no read starts in the sampled windows (a small targeted file). |
| `alt_unaware`, `duplicates_unmarked` | `info`, `vcf snp`/`indel`, `microarray` | The alignment has ALT contigs without ALT-aware mapping, or its duplicates are not marked. |
| `high_homozygosity`, `low_homozygosity`, `high_no_call_rate` | `microarray` | Genotype statistics outside the expected range. |
| `liftover_ambiguous` | `microarray` on hg38 | Sites the hg38-to-hg19 chain places more than once (`ambiguous`), or distinct sites that land on one hg19 position (`collision`), are exported as no-calls rather than placed arbitrarily. The run's `liftover_*` metrics count every outcome. |
| `alignment_memory` | `align`, `realign` | minimap2 is estimated to need more RAM than is available (`aligner`, `estimated_gib`, `available_gib`, and `suggested_threads`/`suggested_memory` that fit, or `null`). The run continues. |
| `aligner_stalled` | `align`, `realign` | The aligner reported no progress, and no stage of its pipeline used CPU, for 15 minutes (`tool`, `minutes`): it is waiting on its input or output. A quiet aligner whose pipeline is busy (a slow batch of centromeric long reads, or samtools collate reading the whole input first) is not stalled. |
| `dense_suffix_array_unused` | `align`, `realign` asked to use the dense suffix-array file; `ref dense-sa` | It wasn't used (`reason`: `other_aligner` with the `aligner` that ran, `missing`, `unusable`, `wgse_bwa_too_old` or `no_wgse_bwa`; `path`, `version`); the alignment ran without it, with the same records. |
| `native_aligner_unusable` | `align`, `realign` on Apple Silicon | A `wgse-bwa`, `wgse-bwa-mem2`, `wgse-bwa-mem3` or `wgse-minimap2` on `PATH` failed its `capabilities --json` check or is too old (`binary`, `path`, `aligner`); `aligner` ran instead. |
| `bwa_mem3_fast` | `align`, `realign` with `--fast` | bwa-mem3's speed preset aligned the reads (`aligner`: the binary), so the alignments differ from BWA's (on 5M HG002 pairs, 3.5% of primary records were placed differently). |
| `short_read_phasing`, `no_variants_phased` | `vcf phase` | The reads are not Nanopore or PacBio (`platform`), so read-backed phasing links few variants; or no heterozygous variant (`heterozygous_variants`) was phased. |
| `high_depth_imputation` | `vcf impute` | The reads average more than 8x on the autosomes (`depth`): calling them directly is as accurate and keeps rare variants. |
| `rare_imputed_not_listed` | `vcf clinvar`, `vcf gnomad --max-af`, `analyze` | Imputed alleles rarer than 1% in the panel (`count`) were left out of the findings. |
| `imputed_vcf` | `qc vcf`, `microarray` | The VCF was written by `vcf impute` (`tool`, `panel`): its genotypes are predicted from a reference panel, not read from the sample. `microarray` exports its `ImpLowConf` genotypes as no-calls. |
| `parabricks_low_memory` | `align`/`realign`/`vcf gatk` with `--gpu` | The GPU had too little free memory for the tool's default mode, so it ran with its low-memory option (`tool`, `option`, `gpu_free_gib`). |
| `parabricks_host_memory` | `--gpu` commands | Less RAM is available than the Parabricks tool needs for a whole genome, though the computer has its minimum (`tool`, `needed_gib`, `available_gib`); the run continues and may be killed partway. |
| `parabricks_memory` | `--gpu` commands with `--no-gpu-memory-check` | The GPU or host memory is below what the tool needs (`tool`); the run continues and may fail. |
| `no_variants`, `no_reads`, `nothing_annotated` | variant calling, alignment, extraction, annotation | An output is empty. |
| `low_confidence_findings` | `vcf clinvar`, `vcf gnomad --max-af`, `vcf pharmcat`, `analyze` | Some reported calls are low confidence (`count`, `total`, `output`, top `reasons`); see [variant-confidence.md](variant-confidence.md). |
| `unconfirmed_clinical_findings` | `vcf clinvar`, `analyze` | The sample carries ClinVar pathogenic/likely pathogenic calls (`count`): a technical screen to confirm with an accredited laboratory, not a diagnosis. |
| `confidence_first_sample_only` | confidence labelling | A multi-sample VCF was judged by its first sample (`sample`). |
| `sample_female` | Y extraction | Female sample; Y outputs will be empty or noise. |
| `ybrowse_reference_n_sites_excluded` | `extract ydna-vcf --ybrowse` | The reference chrY is N at some yBrowse sites (the PARs in hs38 and the GRCh38 no-alt analysis set), so they were not called (`sites`, `contig`). The `ybrowse_excluded_reference_n_sites` metric and the VCF header's `##ybrowse_excluded_reference_N_sites` give the count. |
| `sex_check_skipped` | Y extraction | The female-sample check couldn't run to completion (for example, the alignment lacks supported GRCh37/38 contigs or the CRAM reference couldn't be read). Declare the sex with `sample set --sex`, or pass `--ref` if a CRAM reference is missing. |
| `build_unknown`, `build_unsupported` | personal report, Y haplogroup | The reference build is unknown or unsupported by the tool. |
| `chip_ambiguous` | `info chip` | Two catalogued revisions match equally well. |
| `gaps_not_excluded` | `vcf filter --exclude-near-gaps` | The reference or its gap table was not found, so variants near gaps were kept. |
| `panel_contigs_not_in_vcf` | `vcf filter` and `vcf table` panel selection | Panel intervals on contigs absent from the VCF were omitted. |
| `gvcf_reference_missing`, `inconclusive_parent_genotypes` | `vcf trio` | A gVCF trio has no `--ref`, so reference blocks count only where they start at a variant; missing, partial or unassessed parental genotypes prevent inheritance classification. Inconclusive candidates are written separately (`count`, `mode`) and included in the personal report's overview warnings. |
| `no_indel_scores` | `vcf cadd` | The CADD indel file is missing, so indels are not scored. |
| `contigs_skipped` | `vcf to-fasta` | VCF contigs absent from the reference were left out (`count`). |
| `mt_reference_unconfirmed` | `lineage mt-haplogroup` | Nothing confirms the chrM is the rCRS whose positions Haplogrep classifies: no rCRS header MD5, a reference chrM that isn't a recognisable rCRS, or VCF REF alleles that CHM13's rotated chrM fits too. CHM13's own chrM is mapped onto the rCRS instead (`details.mt_reference`). |
| `mt_haplogroup_skipped` | `analyze comprehensive` | `lineage mt-haplogroup` would refuse the alignment's chrM (hg19's Yoruba, another rotation of the rCRS, or a CHM13 header without CHM13's chrM in the reference); the analysis went on without an mtDNA haplogroup. |
| `y_haplogroup_failed` | `analyze comprehensive` | `lineage y-haplogroup` failed (often too few chrY reads); the analysis went on without it, and a rerun retries it. |
| `annotation_skipped`, `annotation_failed` | `vcf chain-annotate`, `analyze` | One annotation was skipped (`annotation`, `reason`) or failed. |
| `read_groups_mixed`, `read_group_conflict` | `bam unalign`, `bam merge` | Read groups differ in a way that loses or merges information. |
| `read_group_renamed` | `bam merge` | Runs with identical `@RG` lines that name no run (`PU`, ONT `runid=`) keep separate read groups; the later ones get suffixed IDs (`read_groups`). |
| `read_name_collision`, `sample_name_assumed` | `bam merge` | Different reads in two runs share names (`inputs`), so name-based tools may pair them; no input names its sample, so `SM` is the output name (`sample`). |
| `mod_tags_partial`, `mod_models_differ` | `bam merge` | Only some runs carry MM/ML tags (`inputs` lists those without), or the runs' basecalls name different modified-base models (`models`). |
| `few_modification_tags`, `opposite_strand_modifications`, `pacbio_5hmc_limited`, `unknown_platform` | `methylation` | The input's MM/ML modification tags are sparse, strand-mixed, or from a platform with limited calls. |
| `genotype_ambiguous`, `high_copy_number`, `no_call` | `typing` | A star-allele or HLA call is ambiguous, has extra copies, or failed (`gene`). For CYP2D6, an ambiguous call (`filter`, `call_info`, `candidates`) or Cyrius's `LowQ_high_CN` (about 6 or more CYP2D6 copies; `cyp2d6_cyp2d7_copies`) writes no PharmCAT outside call. |
| `repeat_outside_normal_range` | `typing repeats` | A repeat length is outside its normal range (`locus`). |
| `repeat_unconfirmed`, `repeat_unavailable` | `typing str` | A low-support allele alone changes the grade, all called alleles have low support, or missing expected copies leave the call unresolved; the build's catalog lacks the repeat (NOTCH2NLC on hg19) (`locus`). |
| `repeat_clipped_reads` | `typing str` | Reads clipped at an unclassified locus may indicate an unsized expansion; the locus has no disease thresholds (`locus`). |
| `primary_overwrite` | any | An output of a lab primary kind (BAM, CRAM, FASTQ, VCF, microarray file) was written into the folder of an input, outside the output folder, where it could replace a file from the lab (`path`). |
| `output_exists`, `input_failed` | `vep` | An input was skipped because its output exists (`path`), or failed while the batch went on. |
| `vep_info_missing`, `chromosomes_missing` | `vep verify` | The VEP cache lacks its `info.txt` or chromosome directories (`chromosomes`). |
| `dict_missing` | `ref verify` | The reference has no sequence dictionary. |
| `step_failed` | `benchmark` | A benchmark step failed (`step`, `log`). |
| `cache_path_too_long` | `microarray` on Windows | The reference library path (`path`, `length`) is too deep for its derived caches to fit the 260-character `MAX_PATH`, so the run is uncached; keep the library path at most `limit` characters. |
| `unsigned` | `sign verify` | The checksum manifest has no signature; only hashes were checked. |
| `sample_mismatch` | `analyze comprehensive` | A VCF not called from reads by wgsextract names none of the reads' samples (`path`); see [Run lineage and artifacts](#run-lineage-and-artifacts-pilot). |
| `ystr_unverified_convention` | `lineage y-str` | Markers use a repeat-count convention the panel has not verified. |
| `known_markers_partial` | `lineage y-private` on T2T | Only the YFull YTree positions are known markers there (the YBrowse snapshot is GRCh38-only), so SNPs YBrowse names but YFull's tree doesn't place can be candidates (`build`). |
| `y_private_excess` | `lineage y-private` | More candidate private Y SNVs (`count`) than expected (`threshold`): suspect contamination, a mismatched reference, poor data or a marker set that misses the lineage. |

New codes may be added in any release. Consumers should treat unknown codes
as generic warnings.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Success. |
| 1 | Failure: bad input, a failed tool, an unexpected error. |
| 2 | Usage error: a missing or invalid option, or an argument-parsing error. |
| 3 | Missing dependency: a required external tool is absent, will not run, or is too old, including when a command reports it as part of its own failure message. |
| 128+N | Stopped by signal N (130 for Ctrl-C). |

How refusals are classified:

- **Your option choice is unsupported → 2.** Conflicting flags (`vcf sv
  --pacbio --ont`, `align --r2` with ONT, Ultima or pbmm2 reads, `vcf gatk
  --scatter --gpu`, a `--batch-size` that is not positive), aligner, preset
  and platform combinations that `align` and `realign` refuse (`--preset` with
  BWA, BWA for long reads, pbmm2 for ONT, `--gpu` with ONT, Ultima,
  `bwa-mem2`, PacBio CLR/subreads or a minimap2 preset Parabricks lacks, pbmm2
  with a temporary directory), `vcf deepvariant --gpu` with `--checkpoint` or
  a model Parabricks lacks, conflicting DeepVariant model selectors, a Clair3
  `--model` for another platform, a malformed `vcf sv --region`, `bam merge`
  with fewer than two distinct inputs or an `--output` extension that
  contradicts `--cram`, `bam to-ubam` without exactly one of `--input` and
  `--r1`, `methylation --combine-strands` with a non-CpG context, `sign verify
  --allowed-signers` without `--identity`, a non-positive `benchmark
  --coverage` or `--target-count`, `qc fake-data --legacy-bam --full-size`,
  `example-genome download --method aspera` without a key, a resource file
  option of a `vcf` annotator (`--clinvar-file`, `--gnomad-file`,
  `--cadd-indel-file`, `--gnomad-sv-file` and the rest, or its config.toml setting)
  that names no file, and options
  this platform cannot honour while the command itself can run: `--gpu` off
  Linux x86-64, and `--aligner pbmm2` on Windows. Choosing different options
  fixes the run.
- **The command or input is unsupported here → 1.** A command with no native
  build on this platform (for example `vep`, `typing`, `vcf deepvariant` or
  `vcf sv` on Windows), or an input that needs one (Ultima reads needing
  GATK, or `vcf deepvariant --gpu` on reads it detects as Ultima). No choice
  of options would help.
- **A required tool is missing → 3.** This includes `deps check` when a
  mandatory tool is missing, `deps check --tool NAME` when `NAME` is not
  found, and a missing container setup (Docker or its daemon, an image, or for
  `--gpu` the NVIDIA driver or container runtime, or `curl`/`ascp` for
  `example-genome download`). Missing optional tools are reported but keep exit 0.

## Versioning

Adding optional fields, warning codes, output roles, metrics or `details` keys is
backward compatible and keeps `wgsextract.result/1`. Removing or renaming a field,
or changing its meaning or type, requires `wgsextract.result/2` and a new schema
file. Check `schema` before reading a document.

The envelope fields are **stable**; `details`, `metrics` values and
`provenance.params` are **experimental**, because they follow each command and
its unvalidated scientific calls. [Output contracts](output-contracts.md)
lists every tier and how each change is checked against the previous
release's pinned baseline.

## Compatibility

`info chip --json` and `ref status --json` existed before the manifest. Their
previous payloads are now the manifest's `details`: the `info chip` detection
(or `{"revisions": [...]}` with `--list`), and the `ref status` object. Read
`.details` where you used to read the top-level document. `ref status --values`
is unchanged.

## Coverage

These commands record full manifests: `info` (including `--detailed`),
`info chip`, `info calculate-coverage`, `info coverage-sample`, `ref status`,
`align`, `realign`, every `vcf` subcommand, every `extract` subcommand,
`lineage mt-haplogroup`, `lineage y-haplogroup`, `lineage y-str`,
`lineage y-private`,
`microarray`, every `bam`, `qc`, `ref`, `vep` and `typing` subcommand,
`analyze`, `pet`, `methylation`, `repair`, `benchmark`, `example-genome
download`, `sign` and `report`.

`align` and `realign` record the `aligner` (bwa, bwa-mem2, bwa-mem3, minimap2
or pbmm2) and the alignment's `records`. When bwa-mem3 ran, `bwa_mem3_mode`
records its output: `compat=bwa-mem` (bwa's records), `compat=bwa-mem2`
(bwa-mem2's) or `fast`. When one of WGS Extract's aligner builds ran in the
aligner's place they also record `native_aligner` (for example `wgse-bwa`, or
`wgse-bwa-mem3` standing in for bwa),
`native_aligner_version` and `native_aligner_backend` (`metal` or `cuda`
when it ran with `--backend auto` for that GPU, which uses the GPU where it can
and otherwise finishes on the CPU, so the GPU may have done none of the work;
`cpu` when it ran on the CPU only), and list the build under `tools`. For a
BAM also `mapped_records` and `unmapped_records` (from the index, without
decoding reads). CRAM counts come from container headers, which also count
unmapped reads placed beside their mates, so a CRAM reports `records` only.
Variant callers and annotators record `variants` per output VCF;
`vcf snp` and `vcf indel` also record `bcftools_allocator` (`mimalloc` when
bcftools ran with wgse-mimalloc on Apple Silicon, else `system`; see
[hardware.md](hardware.md#bcftools-with-mimalloc)); filtered
outputs (`--min-score`, `--max-af`) are separate outputs with their own counts.
With `--reference-evidence gvcf` the gVCF is an output with role
`reference_evidence`, whose metrics are its provenance (`engine`,
`quality_model`, `bands`, `ploidy_policy`, ...), and its index one with role
`reference_evidence_index`; see [reference-evidence.md](reference-evidence.md).

Other commands' metrics:

- `bam` subcommands that write a BAM/CRAM record its `records` as `align` does
  (an unsorted or unaligned BAM has no index to count from, so it has none);
  `bam merge` adds `merged_inputs`, `read_groups` and, for indexed inputs,
  the combined `autosomal_depth` (each run's in `details.runs`); `bam unalign`
  its `read_groups`.
- Y extractions (`extract ydna-bam`, `extract y-mt-extract`, `extract region
  --target y|ymt`) record each output's `y_scope` and its comma-separated
  `contigs`; `details` holds `y_scope` and `contigs`, one row per considered
  contig (`name`, `length`, `role`, `included`, `reason`, and for a BAM its
  `mapped_records` and `placed_unmapped_records`). See
  [Y-associated contigs](lineage.md#y-associated-contigs---y-scope).
- `qc fastp` records read counts before and after filtering, the Q30 rate and
  duplication rate; `qc fastqc` the module pass/warn/fail counts and total
  sequences (`r2_`-prefixed for `--r2`); `qc vcf` the `bcftools stats` totals (records, SNPs, indels,
  ts/tv).
- `analyze` reads each step's result document (the steps still print their
  tables and results) and includes the step's manifest (role
  `step_manifest`), its outputs with their metrics and its warnings with their
  details (tagged with `step`), even for steps that write no manifest such as
  `info --detailed`; plus `sex` and `significant_variants`.
- `methylation` records sites, median coverage and weighted percent modified
  per modification (and, with `--format parquet`, the `rows` of the
  `bedmethyl_table`); `typing` the called genotypes (`cyp2d6_genotype` with
  `cyp2d6_call_status`, Cyrius's `cyp2d6_call_info` and candidate
  `cyp2d6_raw_star_alleles`, and `cyp2d6_cyp2d7_copy_number`, Cyrius's
  combined CYP2D6+CYP2D7 copy number; one diplotype per HLA gene, flagged repeats, SMN1/SMN2 copy numbers with
  `sma_carrier` and `sma_affected`); `repair` the records read and
  repaired.
- `vcf prepare` records `records_in`, `records_out`, the input's
  compression and index state and the number of `repairs`, with one
  `vcf_prepare_*` warning per repair
  ([variants.md](variants.md#preparing-a-vcf-from-a-provider)).
- `microarray` on hg38 input records how the hg38-to-hg19 liftover placed the
  CombinedKit's sites: `liftover_lifted`, and the no-calls by reason,
  `liftover_unmapped`, `liftover_alt_contig`, `liftover_ambiguous` (more than
  one placement) and `liftover_collision` (sharing an hg19 position with a
  different site, or with one of its several placements). Each vendor kit and
  the kept `combined_kit_hg19` carry the same counts, so a rerun that reuses
  them reports them too.
- `vep` records `inputs`, `succeeded`, `skipped` and `failed`, and `variants`
  per annotated VCF; a directory of inputs writes `vep.manifest.json`.
- Reference installers (`ref library --install`, `ref bootstrap`, the
  annotation downloads, `ref gene-map`) record every file they add or change in
  the library; `ref index` its new index files; `ref count-ns` the N totals.
- `benchmark` records its report files and step counts in
  `benchmark.manifest.json` in the run directory.
- `report` records its documents, the source manifests it read (role
  `manifest`), the number of `findings` (and `findings_<label>` per
  confidence label) and each section's status (`section_<name>`), with a
  `section_failed` warning per failed section and a `newer_attempt_failed`
  warning per section showing an older result than the step's newest,
  failed attempt; its `details` is the `wgsextract.report/1` document
  ([personal-report.md](personal-report.md)).

`repair` in pipe mode (no `--input`) streams to stdout, so `--json` is a usage
error there. Commands that only print (`deps`, `info` listings, `ref library
--list`, `config`) record no outputs.

## Alignment implementation

Alignment output registration, streamed BAM/CRAM publication and atomic index
staging are shared in `commands/alignment/output.py`. Existing
`commands.align` callers use the same helpers through its import interface.

## MultiQC

`qc multiqc-metrics` turns a directory of manifests (and saved `--json`
output) into MultiQC custom-content tables, and `qc multiqc` includes them in
its report. See [Working with professional pipelines](interop.md#multiqc).
