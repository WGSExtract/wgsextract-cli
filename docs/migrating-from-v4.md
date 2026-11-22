# Migrating from WGS Extract v4 (v44.2)

This guide is for people moving from the released WGS Extract v4 desktop
program (v44.2, 28 Feb 2023, with the v8 reference library) to the current
desktop app and `wgsextract` CLI. It maps each v4 action to its current page
and command, lists changed defaults and what is not yet equivalent, and
records the public-data comparison behind those statements.

The comparison is release-qualified: it inspected and ran the released v44.2
program, not the older `WGSExtract-Dev` repository, which holds v33-era source
and lacks actions that v4 shipped (alignment, FASTQ QC, WES extraction,
whole-genome SNP and InDel VCFs). The research is in #994 (WGS Extract v4
before/after); this guide and its corpus are #1187.

| Pin | Identity |
| --- | --- |
| v44.2 program | [`WGSExtract-v44.2_28Feb2023_program.zip`](https://get.wgse.io/WGSExtract-v44.2_28Feb2023_program.zip), SHA-256 `ee7b57e042fb05626a6f3c6dc791bb86857a9802d48ecdbc8d1a1c4604b52a23` |
| v8 reference library | [`WGSExtract-v8_30Jan2023_reflib.zip`](https://get.wgse.io/WGSExtract-v8_30Jan2023_reflib.zip), SHA-256 `5cd199c7dfa68ec594e8b23cf1f19ef34aadd6973f22be14a6c1659ffb131365` |
| Development repository | WGSExtract-Dev `f0f50d867c1e395f33abf09d6291d708e7fd1aba` (stale v33-era tree) |
| CLI source analysis | `1cc62f3cbca69937df5499774bdbebd1453bb886` |
| CLI empirical run | `6b676c17481d866a96229e8a2db654ecf88d1a3a` |
| Scientific tools | samtools, bcftools and htslib 1.24 on both sides |

Both sides of the comparison ran the same modern samtools/bcftools 1.24. That
isolates differences in program logic; it does not reproduce v4's historic
bundled installers, and nothing here claims those older binaries behaved
identically.

## Before you start

- **Keep your v4 installation, its settings and your original files.** The
  current app does not change them; keep them until you have checked the new
  outputs.
- **Write new outputs to a separate folder.** By default each command writes
  to a `wgse/` folder beside its input; set another with `--outdir` (or the
  Advanced-mode Output Directory). Don't point it at v4's output folder: file
  names differ and you would mix results from two programs.
- **Don't reuse v4's `reference/` folder as the reference library.** Its
  layout, the all-diploid `microarray/ploidy.txt`, the chains and the chip
  templates differ from what the CLI expects. Set up the current library with
  `wgsextract ref bootstrap` (an explicit download) and the Reference Genomes
  page, then check genomes with `wgsextract ref verify`. A reference genome
  FASTA you already have can be reused; verify it first.
- **Settings are not imported.** v4 kept a few values in `~/.wgsextract`
  (JSON). Copy the ones you want into `config.toml` (see
  [Configuration](configuration.md)) or the Settings page:

| v4 `~/.wgsextract` key | Current setting |
| --- | --- |
| `outdir.FP` | `output_directory` (or `--outdir`) |
| `reflib.FP` | review, then `reference_library`; genomes go in `reference_genome_library` |
| `tempf.FP` | `temp_directory` |
| `BAM.file_FN` | `input_path`, or choose the file on the page |
| `lang.language` | the desktop app's language setting (not `config.toml`) |
| `.wgsewslbwa` (Windows WSL BWA) | none: current Windows builds use native tools ([Windows](windows.md)) |

- **Data terms differ from the program licence.** Reference chains, chip
  templates, BEDs, Y trees and manuals carry their own terms; see
  [Download mirrors](resource-mirrors.md) and the NOTICE file.

## Old buttons, current pages and commands

The desktop app has Basic and Advanced modes; Basic covers the familiar v4
tasks, Advanced adds every CLI option. Pages are named as in the app.

| v44.2 action | Desktop page | CLI command | Notes |
| --- | --- | --- | --- |
| Select BAM, Stats | BAM: Detailed Info | `info --detailed` | Placed-unmapped reads are no longer counted as mapped |
| Stats: WGS/WES/Poz coverage bins | BAM: Detailed Coverage | `info calculate-coverage`, `--by BED` | Bins and denominators differ |
| Header (view, save) | BAM: Basic Info | `bam identify`, `info` | No header-text export |
| Index, Sort, To BAM/CRAM | BAM / CRAM Management | `bam index`, `bam sort`, `bam to-cram`, `bam to-bam` | |
| Realign | Realign BAM/CRAM | `realign` | |
| Extract WES (TruSeq/xGen BED) | Extract: Custom Extract | `extract custom --region targets.bed` | Bring the BED; no named exome presets |
| Microarray: formats, Recommended, Generate | Microarray: Generate | `microarray --formats ...` | `all` means CombinedKit only (below) |
| MT FASTA / BAM / VCF | Extract: MT-only | `extract mito-fasta`, `extract mt-bam`, `extract mito-vcf` | |
| Y BAM; Y+MT BAM | Extract: Y-only; Y and MT | `extract ydna-bam`, `extract y-mt-extract` | No `chrY_KI270740v1_random` reads (#1184) |
| Y BAM (Poz) | Extract | `extract ydna-bam --poz` | |
| Targeted Y VCF (yBrowse) | Extract | `extract ydna-vcf --ybrowse` | GRCh38 only (#1186) |
| Y haplogroup (Yleaf) | Ancestry: Run Yleaf | `lineage y-haplogroup` | Yleaf 4.1.6-derived engine; trees selectable |
| mt haplogroup (Haplogrep) | Ancestry: Run Mito Haplogrep | `lineage mt-haplogroup` | Haplogrep 3, not 2.4.0 |
| Unmapped export | Extract: Unmapped | `extract unmapped`, `--fastq` | Paired singletons kept as a separate output |
| Unalign | BAM: Unalign to FASTQ | `bam unalign` | |
| Align (BWA, minimap2) | FASTQ: Run Align | `align` | Also BWA-MEM2 and pbmm2; platform checks |
| fastp; FastQC (+MultiQC) | FASTQ: FastP; FastQC | `qc fastp`, `qc fastqc --multiqc` | |
| Whole-genome SNP VCF | VCF: BCFTools | `vcf snp` | Sex-aware ploidy |
| InDel VCF | VCF: BCFTools | `vcf indel` | v44.2 wrote no indels (below) |
| Annotate / Filter / VariantQC ("Coming Soon" in v4) | VCF; Annotate | `vcf annotate`, `vcf filter`, `qc vcf` | New in practice: v4's buttons were stubs |
| CNV / SV (hidden in v4) | VCF (Advanced) | `vcf cnv`, `vcf sv` | New |
| DEBUG: subset | Extract: BAM Subset | `extract bam-subset --fraction 0.1` | |
| DEBUG: parallel CombinedKit | Microarray | `microarray` (parallel with `--threads` above 1) | v4 kept its parallel path off; its comments report about 1% wrong values |
| DEBUG: memory/threads | Settings | `cpu_threads`, `memory_limit`, `--threads` | |
| Language, fonts, Save Window (JPEG) | Settings | none | No Finnish locale or screenshot save yet |

## Working examples

Each line is checked against the CLI's parser by
`tests/test_v4_differential.py`.

```sh
# Stats and coverage
wgsextract info --input sample.cram --detailed
wgsextract info calculate-coverage --input sample.cram --by exome_targets.bed

# v4's "Recommended" microarray set: CombinedKit plus 23andMe V3, V5 and V35
wgsextract microarray --input sample.cram --formats 23andme_v3,23andme_v5,23andme_v35 --outdir new_outputs

# A low-coverage sample: see why each marker is a no-call
wgsextract microarray --input sample.cram --formats 23andme_v3 --call-reasons --outdir new_outputs

# mtDNA and Y-DNA
wgsextract extract mito-vcf --input sample.cram --outdir new_outputs
wgsextract extract y-mt-extract --input sample.cram --outdir new_outputs
wgsextract lineage y-haplogroup --input sample.cram --outdir new_outputs
wgsextract lineage mt-haplogroup --input sample.cram --outdir new_outputs

# Exome-style subset from your own BED
wgsextract extract custom --input sample.cram --region exome_targets.bed --outdir new_outputs

# FASTQ QC, alignment and variant calling
wgsextract qc fastqc --input sample_R1.fastq.gz --r2 sample_R2.fastq.gz --multiqc
wgsextract align --r1 sample_R1.fastq.gz --r2 sample_R2.fastq.gz --ref hs38.fa
wgsextract vcf snp --input sample.cram --outdir new_outputs
wgsextract vcf indel --input sample.cram --outdir new_outputs
```

## Changed defaults

- **`--formats all` is the CombinedKit only**, not every vendor kit and not
  v4's Recommended set. Name the kits you want; the CombinedKit is always
  written alongside them.
- **Quality gates.** Microarray calls ignore reads below MAPQ 10 and no-call
  sites below depth 10 or GQ 20 (configurable; see [Microarray](microarray.md)
  and [Genotype QC](genotype-qc.md)). v4 had no such gates, so a low-coverage
  sample gets many more no-calls. A no-call is not a failed run: add
  `--call-reasons` to see each marker's reason (`low_dp`, `low_mapq`,
  `no_reads`, ...). Lowering one threshold does not reproduce v4: its calling
  prior, ploidy and liftover also differ.
- **Sex-aware ploidy.** v4's v8 `ploidy.txt` made every contig diploid for
  everyone. The CLI calls male non-PAR X and Y haploid and treats a female's
  Y as no-call, from the sample's sex (`sample set --sex` overrides it).
- **Haploid output formatting.** Haploid calls are written as one letter
  where the vendor does (23andMe MT), so files can differ from v4's doubled
  letters.
- **Liftover.** hg38 positions are converted to pyliftover's zero-based API
  and back, reverse-strand placements are complemented, and ambiguous
  placements are dropped as no-calls. v4 passed one-based positions directly,
  ignored strand and kept the first hit. Most markers are unaffected: the two
  origin errors cancel inside forward chain blocks.
- **mtDNA stays rCRS.** MT markers keep their rCRS positions instead of being
  projected onto hg19's Yoruba chrM.
- **File names.** Outputs are named after the input file, for example
  `sample_CombinedKit.txt`, `sample_CombinedKit_hg19.txt` and
  `sample_23andMe_V3.txt`, with a result manifest beside them
  ([Result manifest](result-manifest.md)); v4 also zipped the CombinedKit.
  Compare files by content, not by name.

## Not yet equivalent

| v44.2 capability | Current state |
| --- | --- |
| Y and Y+MT BAMs keep `chrY_KI270740v1_random` reads on GRCh38 | Not kept; #1184 (reference-aware Y-associated contigs) |
| Targeted Y VCF on GRCh37 with HG and ISOGG annotations | `--ybrowse` is GRCh38-only; #1186 (GRCh37 targeted Y VCF) |
| Named one-click exome presets (TruSeq, xGen) and Poz coverage bins | Supply the BED to `extract custom` / `info calculate-coverage --by`; `--poz` exists for Y |
| Header text export, Save Window image, Finnish translation | Not available |
| Haplogrep 2.4.0 / v4's Yleaf payload | Haplogrep 3 and a Yleaf 4.1.6-derived engine: haplogroups can differ, especially on older trees |

Of the genealogy follow-ups from #994, #1185 (FTDNA VCF FILTER repair) is
done ([FTDNA VCF repair](ftdna-vcf-repair.md)); #1184 and #1186 above are
open.

## Fixes since the comparison

The #994 research found three current-CLI issues; all are fixed:

- #1042 (rCRS mtDNA export): MT markers are no longer lifted through the
  hg38 to hg19 chain on export.
- #1043 (microarray reuse identity): a rerun regenerates outputs when the
  effective sex/ploidy, template or chain content, or quality settings change,
  instead of reusing them.
- #1044 (gVCF block depth): a gVCF reference block's start-site depth no
  longer certifies the chip markers inside the block.

## Reading the outputs: the public comparison

On public 1000 Genomes HG00096 reads, GRCh38 chr20:10,000,000-10,100,000
(24,766 records), with the same 131 v8 targets, ploidy file, chain and 23andMe
V3 template on both sides:

| Observable | v44.2 | CLI | Relation |
| --- | ---: | ---: | --- |
| CombinedKit hg19 rows | 131 | 131, 0 disagreements | parity |
| 23andMe V3 rows / called | 959,286 / 57 | 959,286 / 57, 0 disagreements | parity |
| 10% subsample: called CombinedKit genotypes | 124 | 1 (123 `low_dp`, 7 `no_reads`) | policy |
| Mapped records | 24,766 | 24,746 (+20 placed unmapped) | correction |
| InDel VCF records | 0 | 39 | correction |

How to read it:

- The 57 V3 calls are the template markers inside the slice; the other
  959,286 rows are no-calls in both. This is 57 observed agreements, not
  959,286.
- The low-depth difference is a quality policy, not an accuracy ranking:
  neither program's calls were checked against a truth set. Where both called
  a genotype, they agree.
- The 39 indels were not truth-checked either. v44.2's InDel button passed
  `mpileup -I` (skip indels), so it could not write any; its release notes
  say the author had not seen it produce indels.
- Tiny single runs on one core: v44.2 CombinedKit plus V3 conversion 6.6 s,
  CLI 6.0 s. Descriptive only, not a speed claim.

## Platform qualification

Feature availability is qualified per tool and platform, not per operating
system family. A command existing on every platform does not mean every tool
behind it does: check [Windows](windows.md), [Linux ARM64](linux-arm64.md)
and [Hardware](hardware.md), and run `wgsextract deps list` on your machine.
For example, Ensembl VEP needs Linux or macOS while vep-rs runs on Windows
([VEP engines](vep-engines.md)), and GATK and some GPU paths are gated on
ARM64. v4's documents described Windows, macOS and Linux support at the level
of the whole program; neither program has a single all-platform matrix.

## The differential corpus

`tests/fixtures/v4_differential/corpus.json` records every comparison case:
v44.2's behaviour with its release source lines, the current behaviour, the
relation (parity, correction, policy or known gap) and the evidence. Its cases
cover chain-block boundaries, strand, ambiguous placements, sex, the mtDNA
reference, calling quality and genealogy (mtDNA VCF, Yoruba refusal, Y
contigs).

- `tests/test_v4_differential.py` runs real samtools/bcftools on synthetic
  genomes and applies the v44.2 rule to the same input for each case; normal
  test runs include it, offline.
- `scripts/v4_differential_corpus.py synthetic --outdir out/v4-differential`
  reruns every case's evidence and writes tool versions, outcomes and the
  SHA-256 of each input and output file.
- `scripts/v4_differential_corpus.py fetch --cache CACHE --download` fetches
  the pinned public inputs: the HG00096 slice streamed from EBI's CRAM,
  GRCh38 chr20 (or `--reference` to take it from your own GRCh38 FASTA) and
  the v8 reference library (254 MB), each checked against its pin. Nothing is
  downloaded without `--download`; the v4 library is not redistributed here.
- `scripts/v4_differential_corpus.py public --cache CACHE --outdir
  out/v4-differential` reruns the CLI offline on the slice and compares it
  with the recorded v44.2 outputs
  (`tests/fixtures/v4_differential/hg00096_v44.2.tsv`).

The 10% subsample is pinned by its read names: `samtools view --subsample`
with the same seed keeps different reads on Linux x86-64 and macOS arm64.

## Provenance and credit

WGS Extract was written by City Farmer (2018-2020) and Randy Harr
(2020-2022); its microarray generator grew out of Thomas Krahn's Extract23.
The CombinedKit-to-chip-template design is theirs, and the CLI's template
export and liftover are modified ports of v4's `aconv.py` and `hg38tohg19.py`,
kept under GPL-3.0-or-later with their copyright notices. The desktop
renderer's MIT licence does not cover GPL code. See NOTICE, and #864 for the
source rights of shipped data.
