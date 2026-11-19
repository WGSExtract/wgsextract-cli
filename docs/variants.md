# Calling, annotating and typing variants

Use the [command reference](commands.md) for all callers and options.
[Confidence labels and caller accuracy](variant-confidence.md) describe what
the results can establish; research findings require clinical confirmation.
For long reads, also read [ONT/PacBio support](ont.md).

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
  x86-64 (`pixi install -e deepvariant`, native 0.3.1, WGS/ONT_R104);
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

## Structural variants

`vcf sv` defaults to Delly 2 (`delly sr`); `vcf cnv` uses `delly cnv`,
which needs no mappability map (`--map` is accepted but ignored).
ONT detection/`--ont` or `--long-read` selects Sniffles; `--pacbio` supports
pbsv/Sniffles. Explicit `--caller` wins, but Delly with a long-read flag
or pbsv with ONT is rejected. See [structural variants](ont.md#structural-variants).

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

- `ref gnomad --release 4.1` streams native-GRCh38 exome/genome VCFs
  (~880 GB, hours) into an allele-frequency-only table. Default 2.1.1 is
  exome-only, lifted to hg38.
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

`ref pharmgkb` builds `ref/pharmgkb_hg38.tsv.gz` from ClinPGx (formerly
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
`<reference library>/ref/t1k_hla`. Without `--download`, a missing index stops
the command. For offline use, reuse the cached index or supply an existing
index FASTA with `--hla-index PATH`. STR typing compares with
[STRchive](https://strchive.org) ranges; give `--sex` or infer from depth.
For ONT/PacBio use LongTR (`longtr`) or `--caller strkit` (`strkit`).
See [repeat expansions](ont.md#repeat-expansions).
