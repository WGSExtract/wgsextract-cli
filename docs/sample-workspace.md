# Sample workspace (`wgsextract.sample.toml`)

Several commands need the same facts about a sample: its sex (Y-DNA, ploidy,
repeat typing), its sequencing platform (variant caller models, aligner
presets), the reference build and the read depth (genotype QC). Each used to
work them out again on every run. Counting chrX/chrY reads in a 30x CRAM, for
example, takes about a minute. A sample workspace records each fact once, with
where it came from, and lets you correct it.

The workspace is a genome folder: the folder of a `--genome` library entry, or
any folder holding `wgsextract.sample.toml` or `genome-config.toml`. Inputs in
other folders are not recorded (`info`'s own `<input>.wgse_info.json` cache
still applies to them), unless you set an override for one of them, which
creates the workspace there.

## What it records

```toml
schema = "wgsextract.sample/1"

[override]
sex = "Female"

[override."HG002.ont.bam"]
fingerprint = "wgse-fp1:41d7…"
platform = "ont"

[detected."HG002.cram"]
fingerprint = "wgse-fp1:9b1c…"

[detected."HG002.cram".sex]
value = "Male"
method = "GRCh37/38 sampled non-PAR depth / autosomal depth (screening)"
confidence = "medium"
version = 4
by = "wgsextract 0.9.0 info"
at = "2026-11-04T22:18:27+00:00"

[detected."HG002.cram".build]
value = "hg38"
method = "alignment header contigs"
confidence = "high"
version = 1
by = "wgsextract 0.9.0 info"
at = "2026-11-04T22:18:27+00:00"

[outputs."HG002_snps.vcf.manifest.json"]
command = "vcf snp"
status = "ok"
at = "2026-11-04T22:31:02+00:00"
```

- `[override]`: facts you set, which win over detection (see
  [Overrides](#overrides)). `sex` (`Male`, `Female`) and `platform`
  (`illumina`, `ont`, `pacbio`, `ultima`, `dnbseq`) can be overridden. The
  build and depths are facts of each file, not of the sample. Use `--ref` or
  `--build` on a run instead.
- `[detected."<file>"]`: what commands detected from each file. The file is
  named relative to the workspace, or absolute if it lies elsewhere. Each fact
  has its value, the method that found it, a confidence (`high`, `medium`,
  `low`), the detector's version, the command and the time.
- `fingerprint`: the file's [content fingerprint](result-manifest.md) when its
  facts were detected. If the file is replaced or changed (for example by
  `realign` into the same name), its facts no longer match and are detected
  again on next use. A fact from an older detector version is also detected
  again.
- `[outputs]`: the [result manifests](result-manifest.md) that `--genome` runs
  wrote in the folder.

| Fact | Detected by | Used by |
| :--- | :--- | :--- |
| `sex` | `info --detailed`, or sampled non-PAR/autosomal depth on first use | `lineage y-haplogroup`, `extract ydna`, chrX/chrY ploidy in `vcf snp`/`indel`, `microarray` and `vep`, `typing str`, `vcf trio` (proband) |
| `platform` | read-group `PL` tag, else the first read name | `vcf snp`, `vcf deepvariant`, `vcf sv`, `methylation`, `align` |
| `build` | header contig MD5s or lengths | reference resolution for every command |
| `target_depths` | `info --detailed`, else a read count on first use | the low-depth warning of `vcf snp`/`indel`, `microarray` and `lineage` |
| `read_length`, `mean_coverage` | `info` | `target_depths`; shown by `sample show` |
| `somalier_sex` | `qc relate` (chrX heterozygosity, chrY depth) | checked against `sex`: a disagreement is a `sex_mismatch` warning |
| `superpopulation` | `qc relate` with `ref somalier --install` | shown by `sample show` |
| `ancestry` | `qc ancestry` (K=7 reference proportions of at least 0.5%) | the personal report; shown by `sample show` |
| `contamination` | `qc contamination` (VerifyBamID2 FREEMIX) | the contamination warning of `vcf snp`/`indel`, `microarray` and `lineage`; confidence labels of rare and clinical findings in the genome folder; the personal report |

### Sex evidence and Unknown ploidy

Observed sex and a declared override remain separate: `info` reports the
observed call, method and confidence (including `Unknown`/`low`) in its result
manifest; `declared_sex` records the override, when present. The workspace keeps
the declaration under `[override]` and the observed call under `[detected]`.

For GRCh37 and GRCh38 chromosome lengths, inference samples five dispersed
10 kb windows per chromosome. X/Y windows lie strictly between the build's PAR1
and PAR2 boundaries, at 10%, 30%, 50%, 70% and 90% of that interval to avoid
telomeric gaps. Mean aligned-base depth is divided by the sampled base
length and compared with mean depth of available chr7/chr19 windows, not raw
cross-chromosome read counts. MAPQ/baseQ below 20 and unmapped, secondary,
supplementary, duplicate and QC-failed reads are excluded.
For Y, the sampled interval ends before the Yq12 N block (at 28,819,361 in
GRCh37 and 26,673,214 in GRCh38), rather than diluting the denominator with
unmappable assembly gaps. X uses the full central non-PAR interval.

The screen requires autosomal depth at least 3x, autosomal breadth at least 50%,
X breadth at least 30%, and agreement between the sampled autosomal depths.
X/autosome 0.35–0.65 with Y/autosome 0.1–0.8 supports `Male`; X/autosome
0.8–1.2 with Y/autosome below 0.05 supports `Female`. These deliberately
conservative heuristics return **medium**, not high confidence. They are not
validated sensitivity/specificity estimates or a karyotype diagnosis. Sampled
windows cannot certify an assay's scope; exomes, narrow capture, very low
coverage, X0-like/discordant dosage, Y-only extracts and unsupported assemblies
(including T2T) may remain `Unknown`. No supporting-marker or clinical accuracy
claim is made.

An unavailable measurement (for example, a missing index or CRAM reference)
is not persisted as observed evidence. It is retried on the next call; a
cached `info` result with that failure is also recomputed after prerequisites
are restored; other cached metrics are retained while an index is still missing.
Detection reads alignment headers for both BAM and CRAM, so an unindexed BAM
does not undergo a whole-file `idxstats` scan just to discover it cannot be sampled.

An `Unknown` call **refuses sex-dependent bcftools ploidy**, rather than assuming
female X/Y ploidy. Declare sex with `wgsextract sample set --input FILE --sex
Male` (or `Female`), or explicitly choose numeric `--ploidy` or a
sex-independent custom ploidy file. Autosomal-only regions and sex-independent
ploidy need no sex scan.
For whole-file calls, the alignment's header supplies the called contigs.
Autosomal-only trio calls likewise do not require a proband sex.
`typing str` likewise refuses Unknown sex-dependent repeat ploidy; its
run-specific `--sex male|female` option is an explicit declaration.

## Commands

```sh
wgsextract sample show --genome HG002            # overrides, facts, results
wgsextract sample set --genome HG002 --sex Female
wgsextract sample unset --genome HG002 --sex
wgsextract sample set --input ~/Downloads/mom.bam --sex Female --platform ont
wgsextract sample show --input ~/genomes/HG002/HG002.cram --json
```

`--workspace DIR` names the folder directly. Without it, `--genome` or the
`--input`'s folder is used. `sample show` lists the facts in effect for the
input, the facts and overrides recorded for each file (and whether that file
has changed since) and the results. `--json` prints the same data as the result's
`details`. The [typed API](api.md) has the same operations: `api.sample.show`,
`api.sample.set_overrides` and `api.sample.unset_overrides`.

## Overrides

An override changes how commands work (chrX/chrY ploidy, keeping chrY, aligner
presets, the DeepVariant model), so it applies only where it is sure to be
right:

- **Sex, in a genome folder: folder-wide.** A genome folder (the `--genome`
  folder, a dataset-library entry or a folder with `genome-config.toml`) holds
  one person's files, so `sample set --sex` there applies to all of them
  (`[override] sex = …`).
- **Sex elsewhere, and the platform always: one file.** Any other folder, such
  as Downloads, may hold several people's files, and one person's folder may
  hold reads from several platforms. These overrides belong to the `--input`
  file (`[override."<file>"]`) and are refused without one. They record the
  file's fingerprint and lapse, with a warning, when the file changes: set them
  again if they still hold. With `--genome`, the input defaults to the genome's
  alignment.
- A folder-wide `platform`, or a folder-wide `sex` outside a genome folder (for
  example written by hand), is ignored with a warning.

`sample unset` removes the folder-wide override and, with `--input`, that
file's own.

Commands still check an override against the reads: the recorded fact, else a
cheap detection (the read groups or first read name for the platform; chrX/chrY
sampled non-PAR depth, once per file revision, for sex). When they disagree, the command
uses the override and warns `sex_override_differs` or
`platform_override_differs`, with the file, the detected value and the
override, in its [result manifest](result-manifest.md).

## Robustness

- Recording is best effort. A read-only folder never fails a command; the
  facts just aren't recorded.
- A file that isn't valid TOML, or that a newer release wrote
  (`wgsextract.sample/2`), is ignored with a warning and never overwritten.
  `sample set` refuses to change it.
- A file from before the schema existed keeps its overrides; its detected
  facts are dropped and detected again.
- Writes take a lock (`.wgsextract.sample.toml.guard`) and replace the file
  atomically, so concurrent commands on one sample don't lose facts.
- You may edit `[override]` by hand. wgsextract rewrites the file when it
  records facts, so comments are not kept. Invalid override values are
  reported and ignored.

The JSON Schema of the parsed document is
`src/wgsextract_cli/assets/schemas/wgsextract.sample.1.schema.json`.
