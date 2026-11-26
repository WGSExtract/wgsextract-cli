# Calling, annotating and typing variants

Use the [command reference](https://wgsextract.github.io/wgsextract-cli/commands.html) for all callers and options.
[Confidence labels and caller accuracy](variant-confidence.md) describe what
the results can establish; research findings require clinical confirmation.
For long reads, also read [ONT/PacBio support](ont.md).
To count variants across samples without treating missing calls as reference,
see [local cohort summaries](cohort.md).

## bcftools calling and consensus

`vcf snp` and `vcf indel` call indexed whole-genome BAM/CRAM contigs in up
to `--threads` workers when threads exceed 1. Sex/ploidy resolve once, and
outputs concatenate in reference-index order before indexing.
`--no-parallel`, `--threads 1`, explicit `--region`, unindexed inputs,
multi-sample inputs or a different input/reference contig order use one stream.

bcftools subsamples sites deeper than 255 bases with a per-process random
stream. SNP calling finds deep stretches in worker pileups, recalls them
in one process and splices those records in for exact single-stream parity.
Indels never subsample (bcftools skips indel likelihoods above 250 reads),
so workers already match. Indels omit INFO/BQBZ and INFO/MQSBZ because
bcftools does not compute them for indels and otherwise repeats the previous
SNP site's values.

`vcf indel` normalizes IUPAC reference codes to their first alphabetical
base (`R` → `A`), as VCF requires, preventing full hs38DH calls aborting at
ambiguous bases. Ordinary alleles are unchanged. `vcf to-fasta` reconciles
compatible ambiguity in temporary input, not exported VCFs or called ALTs.
With `--uncalled auto`, a gVCF declared by WGS Extract's reference-evidence
header or GATK-style reference confidence bands masks uncovered bases as `N`,
even if there are no multi-base reference blocks. Without a declaration,
the command checks the first records for reference blocks; for a variant-only
VCF, it leaves uncovered positions as reference bases.
Regional `vcf to-fasta --uncalled n` reads only indexed overlapping records,
including deletions/gVCF blocks beginning before the region; whole-genome
masking is unchanged.

`--reference-evidence gvcf` (or `reference_evidence = "gvcf"`) adds
`bcftools.g.vcf.gz` beside the unchanged SNP/indel variant VCF. Every assessed
base distinguishes confident/low-quality reference, uncovered, filtered,
no-call or ploidy-excluded states. The extra bcftools pass roughly doubles
SNP time and adds ~0.4 GB per 30x genome; other callers are to follow.
See [reference evidence](reference-evidence.md), [ploidy](caller-ploidy.md)
and the [BAQ/prior choices](variant-confidence.md#opt-in-baq-in-vcf-snp-and-vcf-indel).
Callers and haplogroup commands share [low-depth warnings](genotype-qc.md);
they do not change output.

## Trios

For set comparisons across callers or family members instead of an inheritance
analysis, see [Compare local VCFs](variant-comparison.md).

`vcf trio` accepts the proband's and parents' VCFs or BAM/CRAMs.
`--proband` wins; otherwise normal `--vcf-input`/`--input` selection applies,
including explicit `--input` over a configured VCF.
Mixed chr/no-chr names normalize across primary, random, unplaced and ALT
contigs; gVCFs follow the resolved reference's naming.

A missing parental VCF record or low-GQ reference block cannot prove absence:
de novo lists can fill with inherited calls, which raises a warning.
With BAM/CRAMs, the child is called first, then all three are jointly
genotyped at its positions so parental depth/allele counts are known.
Samples are role-named even if every input has `SM:WGSE` (WGS Extract v4).
Proband sex comes from X/Y coverage or `--proband-sex`; Y and mtDNA are not
called. De novo candidates need `--min-depth` (default 15) in all three
and no parental ALT read. `trio_denovo_isolated.vcf.gz` retains candidates
with no other within 1 kb: clusters are mostly misaligned repeat reads.
A 30x genome trio took about 70 minutes with 8 threads.

## DeepVariant

`vcf deepvariant` can use:

- Google's Docker image on x64 Linux/Intel Macs
  (`docker pull google/deepvariant:1.10.0`; `deepvariant_image` overrides),
  or Google's `run_deepvariant` on PATH;
- [wgse-deepvariant](../native/deepvariant/README.md) on Apple Silicon
  (WGS only, GPU call_variants, published weight hashes checked), or Linux
  x86-64 (`pixi install -e deepvariant`, native 0.4.0, WGS/ONT_R104);
  set `deepvariant_weights` to `wgse-deepvariant-weights-1.10.0`.
  Linux uses CPU or `--gpu` CUDA; `deepvariant_native = "off"` keeps
  Docker/Parabricks;
- [Parabricks](hardware.md#nvidia-gpus-parabricks) with `--gpu`;
- a [locally built native ARM64 1.10.0 CPU image](deepvariant-arm64.md)
  on Linux ARM64/Apple Silicon without the native engine. Engine/image
  architecture is checked and precision-changing entrypoints bypassed;
  no x86 fallback or unaudited PATH launcher is used.

Docker/images are validated, never installed. Bioconda's `deepvariant`
package has held no Google DeepVariant since 1.8: the optional native
`deepvariant` environment above is different.
Google's x86-only image fails under Apple Silicon Docker emulation (no AVX)
and is refused. Alternatives are `vcf clair3` for ONT, `vcf snp`/`indel`,
or x86-64 hardware.

Updating WGS Extract preserves the optional `deepvariant` environment.
Rerun `pixi install -e deepvariant` from the updated checkout to upgrade an
older native install to 0.4.0 before calling haploid chrX/Y/MT. If you installed
`wgse-deepvariant` on PATH instead, replace that binary with 0.4.0 or later;
it takes precedence over the optional environment.

CPU preflight covers `postprocess_variants`: each worker sorts a partition
in RAM (~7 GiB for a 30x genome at `-t 5`). It adds partitions/reduces workers
when necessary, logging changes, and refuses if even one cannot fit.
Container OOM is an error, not a hung run. Work persists in `.deepvariant.work`
for stage resume; see [postprocessing resources](resource_estimates.md#deepvariant-postprocess_variants)
and [journals](step-resume.md).

CPU calling stages CRAM ≥3.1 as indexed CRAM 3.0 with host samtools to avoid
the htslib 1.18 SSE4 rANS decoder bug in x86 images; other images, including
ARM, also stage. It honors `--region` (whitespace-separated intervals or
BED/BED.gz/BEDPE, unioned without duplicate reads), checks disk and cleans
up on completion, failure or cancellation. Targeted disk is twice overlapping
CRAI slice sizes (shared slices counted once), minimum 64 MiB; unreadable or
missing indexes use the whole-input estimate. This accounts for capture read
concentration. Unknown contigs in BED/explicit intervals are dropped; bare
unknown contigs or selections covering no reference bases are rejected.
Colon-containing contigs (HLA) support whole-contig/range/single-position
selection. Allow extra staging disk/time (~32 GB/19 min on the observed
30x genome). BAM, CRAM 3.0 and Parabricks inputs are unchanged.

ARM supports only qualified default WGS: other models, `--wes`, `--pacbio`
and `--checkpoint` fail. Build local-only
`localhost/wgsextract-deepvariant:1.10.0-arm64` with
`packaging/deepvariant-arm64/build.sh`; do not pull similarly named Hub images.
Its single multi-stage build does not resolve a builder image from a registry.
Default TensorFlow batch 1024 uses ~26 GiB maximum-child RSS, not per shard:
FP32 oneDNN/ACL inference, not 64K pages (tested guests have 4K) or malloc arenas.
`--batch-size 256` reduced chr20 RSS 26,971,300 → 7,981,020 KiB (70.4%) with
all 210,390 VCF records/fields identical. Precision/models and default stay
unchanged. Preflight separates batch inference from concurrent make_examples;
lowering threads alone does not remove that floor. Example:

```bash
wgsextract vcf deepvariant -i sample.bam --ref reference.fna --batch-size 256 -t 8 -o results
```

The conservative WGS inference estimate is 33 GiB at 16 default shards,
17 GiB for this example. Warnings use reclaimable/cgroup-limited RAM, not an
allocation or custom-model guarantee. Smaller batches were validated on chr20,
not a new whole-genome benchmark.

## Ultima reads

UG 100 reads (including MyHeritage and Nebula/DNA360 WGS) are single-end,
~300 bp, with homopolymer-length errors. They carry flow probabilities in
`tp`/`t0`, read-group `PL:ULTIMA` and flow order `FO`.
Detection uses the read group or `--platform ultima`; choose explicitly for
FASTQs whose names SRA replaces.

- `align`/`realign` use BWA-MEM `-Y`, as Ultima/Broad do, retaining flow-order
  read groups. Flow tags reverse when reads change strand (`samtools reset`
  does not do that). Realign streams BAM/CRAM to BWA without FASTQs.
- GATK `MarkDuplicates --FLOW_MODE` requires matching single-end starts and
  ends. Without GATK, samtools markdup uses starts only and warns.
  Parabricks alignment is refused: it drops flow tags.
- SNP/indel calling uses `mpileup -X ultima` and bcftools' default prior,
  not `-P 0`, which more than halved Ultima indel precision. Homopolymer
  false indels remain common: prefer DeepVariant (see [accuracy](variant-confidence.md#variant-calling-accuracy)).
  GATK uses `--flow-mode ADVANCED`; FreeBayes warns it lacks that error model.
- DeepVariant uses `ULTIMA` from experimental `google/deepvariant:gh1100-ultima`
  (`deepvariant_ultima_image`). `--gpu` uses Google's experimental GPU image,
  not Parabricks; see [GPU requirements](hardware.md#nvidia-gpus-parabricks).
- `bam to-ubam` retains `tp`, `t0`, `tm`, `rq` and `a3` in read orientation.
- Low-pass Ultima (MyHeritage ~2x) cannot give reliable genotypes without
  imputation; these callers do not impute. See the separate [imputation guide](imputation.md).

## FreeBayes regions

Pass one or more comma-separated regions to `vcf freebayes --region`, or pass a
BED file. Text ranges use 1-based inclusive coordinates and accept thousands
separators, as in `chr1:1,000-2,000`. If a comma could separate a whole contig
name rather than group digits, use a BED file to make the selection explicit.

## Preparing a VCF from a provider

VCFs from sequencing services often stop other tools: plain gzip instead of
BGZF, no or a stale index, unsorted records, `1` where the reference says
`chr1`, or records on contigs and INFO/FORMAT/FILTER IDs the header never
declares. `vcf prepare` reads the VCF once, repairs what it finds and writes
`<stem>_prepared.vcf.gz` with a `.tbi` (`.csi` for contigs over 512 Mb). It
never changes the input, and checks the prepared record count before
publishing, so no record is lost.

- `--ref` names, orders and sizes the contigs as the reference does and
  counts records whose REF differs from it (kept unchanged). A VCF whose
  contig lengths or positions show another build is refused.
- `--normalize` (needs `--ref`) splits multiallelic records
  (`bcftools norm -m -any`, the original in INFO/`OLD_MULTIALLELIC`) and
  left-aligns indels (the original in INFO/`OLD_VARIANT`). Records on
  contigs the reference lacks are split too, but neither REF-checked nor
  left-aligned.
- Undeclared IDs are declared as `Type=String` so their values stay exactly
  as written. Legacy FTDNA FILTER expressions such as `DP=1` are refused with
  a pointer to [`repair ftdna-vcf`](ftdna-vcf-repair.md).
- An already valid VCF is copied and indexed with no repairs; running it
  again on its own output repairs nothing.

Each repair is a `vcf_prepare_*` warning in the result manifest with its
counts: `recompressed`, `index_rebuilt`, `contigs_renamed`,
`contigs_declared`, `contig_lengths_added`, `contigs_reordered`,
`ids_declared`, `sorted`, `multiallelics_split` and `left_aligned`. Findings
it leaves alone are `ref_mismatch`, `not_left_aligned`,
`contigs_not_in_reference` and `contig_lengths_unknown`. The metrics give
`records_in`, `records_out`, `input_compression`, `input_index` and the
number of `repairs`.

## Structural variants

`vcf sv` defaults to Delly 2 (`delly sr`); `vcf cnv` uses `delly cnv`,
which needs no mappability map (`--map` is accepted but ignored).
On macOS, install Delly with `pixi install --locked -e delly`. WGS Extract
finds it in that environment; Delly still requires htslib 1.24, while the
macOS builds of bcftools 1.25 require htslib 1.25.
ONT detection/`--ont` or `--long-read` selects Sniffles; `--pacbio` supports
pbsv/Sniffles. Explicit `--caller` wins, but Delly with a long-read flag
or pbsv with ONT is rejected. See [structural variants](ont.md#structural-variants).

Delly (`vcf sv` and `vcf cnv`) and Sniffles accept a single region, a
comma-separated list such as `chr1:1-10000,chr1:20000-40000`, or a BED file.
Text ranges are 1-based and inclusive; BED intervals are 0-based and half-open.
Low-coverage checks sample the same selected intervals with either format.
For Delly and Sniffles, `chr1:1000` or `chr1:1000-` runs through the end of
the contig, and `chr1:-1000` starts at base 1. BEDPE files are not supported.
Region contigs must be present in both the alignment and the reference. pbsv
accepts one region only, not a list or BED file. The same limits apply to
`vcf call --types sv` or `--types cnv`. If SNPs or indels are also requested,
use text regions: the small-variant stage does not accept a BED file.

## One gene

`info gene --gene SYMBOL` puts one gene's local evidence together, offline: its
span from the bundled NCBI RefSeq annotations (aliases resolve, as in
`panel bed`), its RefSeq transcripts and exons when `ref gene-map` installed
them, the sample's carried variants in it from `--vcf-input` (the same columns
as `vcf table`: consequence, ClinVar, gnomAD frequency, research-only ACMG
class) and `samtools depth` over each exon of `--input` (BAM or CRAM) against
`--min-depth` (default: config `confidence_min_depth`). Each variant is tagged
with the filters it passes: `rare_damaging` (gnomAD AF below 1%, or absent from
an annotated gnomAD, with HIGH or MODERATE impact), `clinvar_plp` and
`acmg_plp`. `--export all|rare-damaging|clinvar-plp|acmg-plp` writes those rows
as `<vcf stem>_<GENE>_<filter>_variants.tsv` with the table's provenance header.

```sh
wgsextract info gene --gene BRCA1 --input sample.cram --vcf-input sample_annotated.vcf.gz
```

`--json` puts the `wgsextract.gene/1` document in `details`: `gene`,
`transcripts`, `variants` (rows, the annotations present and the header lines
each came from), `coverage` (gene, 100 bins and each distinct exon, with
`below` when any base is under the threshold) and `notices`. A notice names
what is missing and the command that makes it (`ref gene-map`, `vep run`,
`vcf clinvar`, `vcf gnomad`, `vcf acmg`, `vcf call`); nothing is downloaded.

`info gene` has no online mode. The desktop's optional
[gene.iobio hand-off](../gui/docs/gene-page.md#open-in-geneiobio) requires
separate confirmation and does not share files. gene.iobio is free for
academic and personal use; any other use (including commercial) requires a
licence from Frameshift Genomics (info@frameshift.io). Not for clinical use.
The desktop app's [gene page](../gui/docs/gene-page.md) shows this document.

## Annotation resources

Gene filtering (`--gene`) requires rebuilt/current `ref gene-map` maps,
hg19/hg38 only, not T2T; see [reference verification](reference-genomes.md#verification-and-gene-maps).
`vcf clinvar`, `vcf gnomad --max-af` and analyze's discovery report write
technical `high`/`moderate`/`low` labels and reason codes to INFO/WGSE_CONF,
`.confidence.tsv` and manifests. `vcf pharmcat` publishes confidence labels
only in its `.confidence.tsv` and manifest, not in an annotated VCF.
Configure `confidence_*`,
or override `--confidence-depth LOW HIGH` / `--confidence-gq LOW HIGH`.
See [rules, rationale and validation](variant-confidence.md).

- `ref gnomad --release 4.1 --download` streams the native-GRCh38 joint
  exome/genome VCFs (~880 GB, hours). For each passing ALT with positive joint
  frequency, the table keeps the joint frequency, maximum ancestry-group
  frequency and alternate homozygote count. `vcf gnomad` transfers them to
  `GNOMAD_AF`, `GNOMAD_POPMAX_AF` and `GNOMAD_NHOMALT`, respectively.
  Older tables remain readable, but may omit fields or combine multiple ALTs
  in one row. The same `--download` command rebuilds them in the current
  per-allele format. Without `--download`, `ref gnomad
  --release 4.1` checks the local table and can recreate a missing index
  without fetching data. `ref gnomad --download` installs the default release,
  2.1.1, which is exome-only and lifted to hg38.
- `ref cadd` gets CADD v1.7 (~85 GB/build); `ref gerp` gets GERP++ bigWig.
  Annotation adds `CADD_RAW`/`CADD_PHRED` and `GERP`.
- GERP/PhyloP bigWig lookups reuse bounded per-contig windows (pyBigWig's
  NumPy reader if available, lists otherwise). Scores remain the exact
  maximum across REF bases; missing bases are ignored and wholly missing
  spans unscored. Long REF alleles use chunked exact interval statistics.
- `ref spliceai` gets Ensembl's open GRCh38 scores (~29 GB plus published
  index): every SNV in MANE Select transcripts, no indels. Illumina's full
  hg19/hg38 sets require BaseSpace login, so hg19 is a manual download;
  the command names the location. Scores are free only for academic/nonprofit use.
- See [VEP engines](vep-engines.md) for annotation and cache setup.

## Pharmacogenomics and repeat typing

`ref pharmgkb` builds `downloads/pharmgkb_hg38.tsv.gz` from ClinPGx (formerly
PharmGKB) clinical annotations and variant locations. `vcf pharmgkb` matches
annotated SNV alleles only, not star alleles/haplotypes (CYP2D6*4) or indels;
hg38 only. Data is CC BY-SA 4.0 under the
[ClinPGx policy](https://www.clinpgx.org/page/dataUsagePolicy), research use,
not for sale. The table retains licence, download creation dates and retrieval
date; annotation copies them to the VCF header and `details.pharmgkb_source`.

`vcf pharmcat` calls diplotypes/response phenotypes (CYP2C19, CYP2C9, DPYD,
TPMT, …) and a [PharmCAT](https://pharmcat.org) prescribing report.
Extract `pharmcat-pipeline-<version>.tar.gz`
from [PharmCAT releases](https://github.com/PharmGKB/PharmCAT/releases) into
`<reference library>/pharmcat` or `pharmcat_directory`; PharmCAT is not on
conda. `pixi install -e pharmcat` supplies Python/Java/bcftools on macOS,
Linux and Windows. Input must be GRCh38; gVCFs work. CYP2D6/HLA need reads,
not a VCF: give `typing cyp2d6`'s `<stem>.cyp2d6.pharmcat.tsv` and `typing hla`'s
`<stem>.hla.pharmcat.tsv` via repeatable `--outside-call`. They merge into
`<stem>.outside.tsv`, also picked up on later runs.
The first run also needs PharmCAT's GRCh38 reference from Zenodo record 7288118
(about 883 MB). Pass `--download` to fetch it. WGS Extract verifies the pinned
SHA-256 before extraction and installs the FASTA only after both indexes;
interrupted downloads never become installed references. Later runs stay offline.
First-use installs share a directory lock. Interrupted or cancelled transfers
keep their download resume state for the next `--download` run;
incomplete FASTA indexes are detected and repaired with the same consent.

For a variants-only VCF delivered with its reads, add an indexed, coordinate-sorted
BAM/CRAM from the **same sample**:

```sh
wgsextract vcf pharmcat --vcf-input sample.vcf.gz --alignment sample.cram \
  --alignment-reference GRCh38.fa.gz
```

`--alignment-reference` takes the local, indexed FASTA used for the alignment.
It defaults to PharmCAT's FASTA; supply the original FASTA for CRAM if it differs.
The BAM/CRAM and its FASTA must use the same contig names. The VCF may use
standard aliases such as `1` for `chr1`, but the builds must agree. A different
read-group sample name is an error. When read groups name the sample, only
reads from its named groups count. If there is no sample name, the command warns.

An explicit `--alignment` replaces any automatically discovered companion gVCF;
it cannot be combined with `--absent-to-ref`. Only definitions with no overlapping
VCF record are eligible, so explicit no-calls and existing variants are not
overwritten. A local `bcftools mpileup` checks only those definitions' reference
bases, using mapping and base quality ≥20 and excluding reads with unavailable
quality scores. Every base must meet the lower `--confidence-depth` threshold
with only reference support. Read-level evidence of an insertion or deletion
also prevents a reference call. Uncovered sites, insufficient depth,
non-reference evidence, and sites with at least 10,000 reads before base-quality
and overlapping-mate filters stay missing.
This supplies diploid reference evidence, not new alternate genotypes or GQ.

When using an alignment, a companion gVCF, or a WGS Extract depth-only gVCF
as supplemental evidence, see `<stem>.reference-evidence.json` for the
source, thresholds, counts and missing definitions (1-based PharmCAT coordinates).
The result manifest includes the same summary without the missing list.

`typing smn` uses archived SMNCopyNumberCaller 1.1.2 (last Apache-2.0 release)
for SMN1/SMN2 copy number. One SMN1 copy indicates a carrier, none SMA;
two can be a “2+0” silent carrier, with associated g.27134T>G reported.
It refuses long reads and gives no calls below 30x median short-read WGS;
non-bwa-like mapping warns. Use the `smn` environment on Linux/macOS.
It is not a diagnosis.

`typing cyp2d6` (Cyrius), `typing hla` (T1K) and `typing str`
(ExpansionHunter) need indexed whole-genome BAM/CRAM (~30x).
Install `cyrius`, `t1k` or `expansionhunter` explicitly on Linux/macOS.
Cyrius is PolyForm Strict: noncommercial only, no distribution/modification;
it is not bundled. If its index is missing, `typing hla --download` explicitly
allows T1K to download IPD-IMGT/HLA data and build the index at
`<reference library>/downloads/t1k_hla`. Without `--download`, a missing index stops
the command. For offline use, reuse the cached index or supply an existing
index FASTA with `--hla-index PATH`. STR typing compares with
[STRchive](https://strchive.org) ranges; give `--sex` or infer from depth.
For ONT/PacBio use LongTR (`longtr`) or `--caller strkit` (`strkit`).
See [repeat expansions](ont.md#repeat-expansions).

### Long-read paralog typing

`typing paraphase` calls copy number and haplotypes for groups of highly
similar genes, such as SMN1/SMN2, PMS2 and its pseudogene, and STRC. It
needs an indexed whole-genome PacBio HiFi or ONT BAM/CRAM and the matching
indexed FASTA without ALT contigs. A BAM's unused decoys don't need to be
in the FASTA. Paraphase is intended for high-coverage HiFi WGS (roughly
20–30× or more). Read-group `PL` tags must all name the same platform
(`PACBIO` or `ONT`). ONT is **experimental and unvalidated**; the result
warning, manifest and call files mark it accordingly.

```sh
pixi install --locked -e paraphase
wgsextract typing paraphase -i sample.cram --ref hs38.fa.gz --genes SMN1,PMS2,STRC -t 4
```

The optional environment (Linux and macOS) pins Paraphase 3.3.1 under
[BSD-3-Clause-Clear](https://github.com/PacificBiosciences/paraphase/blob/v3.3.1/LICENSE).
The command never installs tools or fetches reference data. Omit `--genes`
to run the engine's full catalog. Version 3.3.1 supports 162 loci on GRCh38,
11 on GRCh37/hg19, and just `smn1` on T2T-CHM13. GRCh38 and CHM13 use
`chr`-prefixed chromosome names; GRCh37 also supports unprefixed names.
Use Paraphase's [locus names](https://github.com/PacificBiosciences/paraphase/blob/v3.3.1/docs/regions.md)
with `--genes` (case-insensitive).

To use your own compatible Paraphase 3.3.2–4.x executable, pass
`--pacbio-licensed-executable PATH` locally. This opts in to the
[PacBio Software License Agreement](https://github.com/PacificBiosciences/paraphase/blob/v4.1.0/LICENSE.md),
which permits analysis only of data generated on a PacBio instrument. Every
input read group must be `PL:PACBIO`; the licensed executable is never run
on ONT, mixed-platform or incompletely tagged inputs. The opt-in scans the
alignment to check that each read belongs to a declared PacBio read group.
The command does not install this executable. The selected engine and
licence are recorded.

The normalized `<stem>.paraphase.tsv` and `<stem>.paraphase.json`
give one row or object per locus, with `gene`, `total_cn`, `call_status`,
`experimental` and `gene_fields` (a JSON object in both formats).
`total_cn` is for the **whole paralog family**, not the main gene;
`gene_fields.gene_cn` holds the main-gene count when available. `no_call`
means an undetermined CN (null in JSON, blank in TSV), not zero. SMN uses
separate `smn1_cn` and `smn2_cn` counts: both reported is `called`, just one
is `partial`, and neither is `no_call`. Its upstream `total_cn` may remain
null even when both gene counts are reported. `called` does not mean every
gene-specific field or haplotype was resolved.

The original JSON (`<stem>.paraphase.raw.json`), haplotagged BAM and index,
and any per-locus VCFs (`<stem>.paraphase.raw.<locus>.vcf`) are retained.
In the 3.3.1 VCFs, each resolved haplotype has its own sample column
with haploid genotypes. The columns preserve phase across variants;
they are not different people or a single diploid genotype such as `0|1`.
CYP2D6-family copy number is not a CYP2D6 star-allele call. These results
are not a diagnosis.

### Target copy-number screening

Use `typing target-cnv` to estimate germline copy number over named intervals
in an indexed short-read WGS BAM or CRAM. Supply a matching indexed FASTA
and a BED (zero-based start, exclusive end; optional target name in column 4).
For gene spans, use `panel bed --build BUILD --genes SYMBOLS --pad-kb 0`.

```sh
wgsextract typing target-cnv -i sample.cram --ref hs38.fa.gz --bed targets.bed
```

The `<stem>.target-cnv.tsv` report includes mean read depth, GC fraction,
the GC-matched baseline depth, and a copy-number class (`0`, `1`, `2`, or
`3+`). The estimate is twice the target-to-autosomal depth ratio. The
baseline uses roughly 220 distributed 1 kb windows; reads and bases below
quality 20, duplicates, secondary and supplementary alignments are
excluded, and overlapping mates are counted once. BED targets cannot
contribute their own depth to the baseline. A GC bin is five percentage
points wide and needs at least five windows with coverage on at least
80% of their bases. The command refuses
sparse baseline coverage or median depth below 10×; a target gets `no_call`
if its GC bin lacks support or its autosome has no covered baseline window.
Confidence depends on how far the estimate is from a class boundary relative
to baseline depth variation; it is not a validated probability.

The bundled GIAB v3.5 masks run offline on GRCh37, GRCh38, and
T2T-CHM13v2.0. They round difficult intervals outward to 1 kb. Add
`--download` to use the original mask boundaries; this fetches only
two public BEDs for the assembly into the reference library, never
sending any target or sample data. Masked intervals are usually withheld as
`specialist_caller_required`. The exception is an exact whole-gene RefSeq
span for a curated deletion locus: GSTM1, GSTT1, UGT2B17, RHD, LCE3B or
LCE3C, when present on that reference's primary assembly (for example, GSTT1
has no primary span in the bundled GRCh38 annotations). BED labels alone do
not qualify an interval. Relative to GC-matched diploid depth,
ratios ≤0.15, 0.35–0.65, and 0.85–1.25 give CN 0, 1, and 2 respectively,
with `medium` confidence and reason `masked_region_curated_call`. Other
ratios stay withheld. At supported loci, the `specialist` column points to
`typing smn`, `typing cyp2d6`, long-read `typing paraphase`, or external
[RHtyper](https://github.com/disonchang/RHtyper). A depth call does not
determine RHD alleles or blood type. The same recommendations appear in
[exon callability](exon-callability.md) for known specialist exons.

The existing sex check supplies the expected CN for non-PAR X and Y
targets. Unknown sex and pseudoautosomal targets are not called; a
non-PAR Y target in a sample inferred or declared female is
`not_applicable`, not a deletion. This is a screen, not a diagnosis.
It does not find breakpoints, resolve paralogs, or distinguish
alleles or mosaic states. It is not intended for panels or exomes.
