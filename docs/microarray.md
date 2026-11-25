# Consumer microarray exports

`microarray` simulates chip raw-data files from BAM/CRAM or VCF input.
Start with `wgsextract help microarray` for options and examples; use
`--help-all` for advanced controls. These are selected markers, not a complete
genome or a clinical test.

## Formats and chip identification

Supported vendors include 23andMe, AncestryDNA, FTDNA (including separate
GRCh38 Y files), Living DNA, MyHeritage, tellmeGen, Genera, MTHFR Genetics,
meuDNA, SelfDecode and Nucleus. `--formats all` writes the combined
CombinedKit v5; vendor revisions use letters, for example `ancestry_v2cd`.
[AADR research panels](aadr.md) (`aadr_1240k`, `aadr_ho`, `aadr_1240k+ho`)
export PLINK and EIGENSTRAT instead.

```bash
wgsextract microarray --input sample.cram --formats 23andme_v5
wgsextract info chip --input raw-data.zip
wgsextract info chip --list
```

`info chip` reads plain, gzip or ZIP raw data without changing or exporting it.
Headers identify vendor/chip; marker lists identify silent same-version
revisions. Identical markers give confidence 1; otherwise confidence is the
marker-ID Jaccard similarity to the closest
[catalogued revision](../src/wgsextract_cli/assets/microarray/revisions/README.md),
halved for tied revisions. It names the matching `--formats` template, or
the closest one and added/dropped markers. `--list` lists the catalogue;
`--json` puts detection or catalogue in manifest `details`.

## Coordinates and targets

Nuclear exports use hg19 coordinates. The existing hg38 vendor route retains
legacy Yoruba-to-hg19 mitochondrial chain shifts and gaps: those positions
are **not rCRS**. CHM13v2 original-MT/masked-Y has a separate pinned hg19/rCRS
projection; read [CHM13 reference requirements and limits](microarray-chm13.md).

hg38 input never chooses among multiple hg19 placements. Unmapped,
alternate-contig, multiply placed sites, and sites colliding with any other
site's placements are omitted from the projected calls and exported as
no-call (`--`). NCBI36 templates and the hg38 target also omit multiply
placed markers. Inverted blocks complement once. Manifest counts are
`liftover_lifted`, `liftover_unmapped`, `liftover_alt_contig`,
`liftover_ambiguous` and `liftover_collision`; ambiguity or collision raises
`liftover_ambiguous`. UCSC's pinned chains place each bundled chip marker at
most once.

All chips' markers are genotyped together against one genome-wide target list.
`ref bootstrap` supplies no such list; first use builds
`microarray/All_SNPs_<build>_ref.tab.gz` from templates and later runs reuse it.
The hg38 list uses UCSC's `hg19ToHg38.over.chain.gz` to place the targets.
GRCh38 input also needs `hg38ToHg19.over.chain.gz` to export hg19 coordinates,
even if you supply an existing target list. Use `microarray --download` to
fetch missing chains into the reference library. The input stays local; see
[network permissions](privacy.md). Reads below MAPQ 10 are ignored: ambiguous
mapping becomes a no-call, not a guessed homozygote.

hg38 exports cache exact hg38-to-hg19 projections in the configured library's
`microarray/hg19-coordinates/`. Keys include chain content, ordered source
coordinates and format version; never IDs or genotypes. Changed inputs or
damaged caches rebuild. Deleting the directory is safe. Without a configured,
writable library, exports run uncached; an explicit FASTA does not choose
the cache directory. Valid read-only caches work. Windows library paths
deeper than 175 characters run uncached; see [derived-cache notes](microarray-chm13.md).

## Call evidence

Defaults require sample **DP ≥10**, **GQ ≥20**, and FILTER `PASS` or `.`.
Missing required fields produce no-calls. For internally called single-sample
BAM/CRAM reference calls only, bcftools' reference-call QUAL substitutes when
it omits GQ. Supplied VCFs never use that fallback. Multi-sample VCFs require
`--sample`. If sample selection encounters undeclared VCF contigs, it reads
the contig names from the records and repairs the header in the working stream.
The original VCF is not rewritten.

VCF input may be bgzipped (`.vcf.gz`), a `.bcf`, or a plain `.vcf`. Indexed
input is read in place, and an unindexed `.vcf.gz` is indexed beside itself
unless `--sample` is given. Any other input (a plain VCF, an unindexed BCF,
an unindexed `.vcf.gz` with `--sample`, or one that can't be indexed where it
is because it is unsorted or in a read-only folder) is compressed and indexed
as a working copy in the run's temporary work, never beside the input, so its
chromosome naming (`1` or `chr1`, `MT` or `chrM`) is matched to the targets'
and `--region` works as for indexed input. The copy keeps only the `--sample`
you chose, and keeps the input's record order: only unsorted input is sorted.

Absent VCF markers are **no-calls**, not reference genotypes: variant-only
VCFs do not establish coverage. Explicit `0/0` records and gVCF reference-block
interiors need the same gates as alternate calls. Block `MIN_DP`, when present,
replaces block-start DP conservatively; uncovered gaps remain missing.
See [bringing VCFs back](interop.md#bringing-vcfs-back) for block precedence
and build handling.

Unsafe legacy absence-as-reference behaviour requires
`--assume-hom-ref --min-dp 0 --min-gq 0`; `--no-exclude-filtered` additionally
accepts failing FILTER records. These can invent calls at unassessed sites or
retain weak evidence: use only with independently established callability.
Indel-only or MNP records do not support a SNP call (`unsupported_allele`),
even when the genotype is `0/0`. They do not make a chromosome count as
called. A mixed SNP/indel record can still support a SNP call if the genotype
uses only single-base alleles; a partially missing SNP genotype is a no-call
(`missing_gt`).
Imputed VCFs without DP/GQ also require explicit threshold overrides; their
imputation-confidence mask still applies.

Matching top-level [configuration](https://wgsextract.github.io/wgsextract-cli/configuration.html) keys are
`microarray_min_gq`, `microarray_min_dp`, `microarray_assume_hom_ref`,
`microarray_exclude_filtered` and `microarray_call_reasons`.
Explicit CLI values win, including `--min-gq 0`, `--assume-hom-ref`,
`--no-exclude-filtered` and `--no-call-reasons`.
Vendor haploid, allele-order, quoting and no-call conventions are unchanged.
Declaring a vendor's I/D encoding does not enable indel calling:
[`--indel-manifest FORMAT=PATH`](microarray-indels.md) is a separate opt-in
caller for verified sequence-defined GRCh37 manufacturer probes, leaving the
SNP CombinedKit unchanged. Repeat it for explicitly selected 23andMe,
Ancestry or MyHeritage layouts. CSV, gzip and ZIP are supported; full reference
contexts and DNA alleles must verify. Unsupported, absent or quality/identity-
rejected probes stay missing; proprietary definitions and Ancestry probe order
are not inferred.

Commands warn about mean depths below 15x autosomal or 3x Y/mtDNA.
After export, over 88% autosomal homozygosity or over 10% vendor no-calls also
warns, without changing output. Depth uses the detailed-info cache, CRAM
headers or BAM index. Configure `qc_*` thresholds or override depth for a run
with `--qc-min-depth` (0 disables). See [genotype QC](genotype-qc.md).

The kits match owners' real AncestryDNA/MyHeritage chips at about 99% of
markers. Weekly tests compare HG00096 with its Omni chip;
[microarray benchmarks](microarray-benchmarks.md) cover all layouts against
independent public chips, per-chromosome concordance/call rate and CPU,
wall time, memory and export-byte parity. With `--threads` above 1,
chromosomes run in parallel by default; sites deeper than 255 reads are
recalled in one stream for exact single-stream calls. `--no-parallel`
keeps one stream.

## Reuse and provenance

Each combined kit (`<sample>_CombinedKit.txt`), vendor kit and AADR panel has
an `<output>.inputs.json`. It records:

- BAM/CRAM/VCF and reference path, size and modification time;
- target list, ploidy file, liftover chain and CLI version;
- genotype-changing options resolved through config: `--min-gq`, `--min-dp`,
  `--strand-ambiguous`, `--resolve-rsids`, `--quirks`, `--assume-hom-ref`,
  `--exclude-filtered`, `--call-reasons`, `--region`, `--sample`,
  AADR sample label/sex, `--indel-manifest` and requested AADR panels
  (their sites join the combined kit).

Reuse requires matching recorded inputs and unchanged outputs; otherwise that
output and sidecar regenerate. Adding a vendor format converts from the kept
combined kit without recalling genotypes. Manifests mark kept outputs `reused`.
Delete a sidecar to force its output to rebuild. CHM13 always regenerates.

## Explaining no-calls

`--call-reasons` adds `<sample>_CombinedKit_reasons.tsv`, all targets with the
usual four fields plus `CALL_REASON`, in the input build.
GRCh37/38 manifests include `call_reason_<reason>` counts; CHM13 keeps counts
in its report. Reasons distinguish `no_reads` (no aligned base under standard
flag filters), `low_mapq` (evidence only below MAPQ 10), low/missing DP/GQ,
`filtered`, `missing_gt`, unsupported alleles, absent VCF markers/contigs,
and explicit `assumed_hom_ref`. CombinedKit/vendor schemas stay four-column.

For BAM/CRAM this adds two depth passes per requested interval, indexed with
`--region`; without a region it reads the whole alignment twice.
It is off by default.

## Identity and vendor strands

These are opt-in identity checks, not probe-orientation validation:

- `--strand-ambiguous no-call` masks known biallelic A/T or C/G input REF/ALT
  sites, including homozygotes. It cannot classify absent sites, reference-only
  records or multiallelics; it neither infers probe strands nor corrects
  mitochondrial complements. Default: `keep`;
  setting: `microarray_strand_ambiguous`.
- `--resolve-rsids` uses bundled [dbSNP build157 history](../src/wgsextract_cli/assets/microarray/rsid/README.md)
  for merged aliases in rsID vendor lookup; withdrawn, ambiguous or unresolved
  identities are excluded. IDs, coordinates and row order stay unchanged;
  alleles never flip. Conflicting aliases, including an explicit quality
  no-call versus a called allele, stay missing. Unlisted IDs are not certified
  active. This replaces neither NCBI36 liftover nor probe-allele validation.
  Default: `microarray_resolve_rsids = false`; `--no-resolve-rsids` overrides
  config. Call policies require one sample.
- Call reasons also include `strand_ambiguous`, `retired_rsid`,
  `ambiguous_rsid` and `unresolved_rsid` for native CombinedKit calls;
  additional vendor-projection conflicts are counted in the log.
- `--quirks vendor` reproduces reverse-strand reports confirmed by other
  public revisions; `--quirks corrected` (default, `microarray_quirks`)
  keeps every call on GRCh37's forward strand. The openSNP-derived
  [table](../src/wgsextract_cli/assets/microarray/revisions/README.md#strand-quirks-strandtsv)
  covers 32 markers across 23andMe V2/V3, AncestryDNA V1/V2 and FTDNA V2,
  and names later fixing revisions. It infers neither A/T or C/G markers,
  swapped probe alleles nor unconfirmed reports. `corrections.tsv` position
  fixes apply in both modes.
