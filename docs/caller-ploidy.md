# Sex/PAR policy in model-based callers

DeepVariant, Clair3 and GATK use the same sample workspace sex override and
coverage-based inference as bcftools calling. The detected fact and any explicit
override remain separate in the workspace. An unknown sex produces the structured
`caller_sex_unknown` warning and uses the female policy, matching bcftools.
Autosome-only requests do not need a sex scan.

The reference library's ploidy file wins; otherwise GRCh37/GRCh38 use the same
bcftools build-specific table used by `vcf snp`. The table's coordinates are
1-based inclusive, translated to half-open BED intervals at each boundary.
Male X is haploid outside the table's PAR intervals and diploid inside; autosomes
remain diploid. The bcftools contract makes male Y haploid (including its PAR
copies), female Y excluded, and mitochondrial calls haploid. This is not a
genome-wide haploid switch. Other sex-chromosome builds require a library ploidy
file. Custom tables must be unambiguous and specify ploidy 0, 1 or 2.

* **Google DeepVariant:** uses `--haploid_contigs` and `--par_regions_bed`;
  ploidy-zero intervals are excluded from its region selection. The generated
  BEDs are fingerprinted as inputs and kept while work is resumable.
  Google's recalibration suppresses heterozygous non-PAR genotypes but retains
  homozygous **diploid-encoded** GT fields (`0/0`, `1/1`), not single-allele GT.
* **CPU GATK:** calls haploid and diploid interval sets separately, using
  `--sample-ploidy`, then gathers and sorts the VCF. Partitioned calls cannot
  currently be combined with `--scatter`.
* **CPU Clair3:** calls each ploidy interval set separately, with
  `--haploid_precise` on the haploid set. That mode retains homozygous-alt
  haploid calls, rather than converting every heterozygous prediction to ALT.
* **wgse-deepvariant and GPU/Parabricks paths:** explicitly refuse requests
  requiring non-diploid handling until those paths are qualified. Diploid-only
  regions still work. For Google's pipeline rather than the native engine, set
  `deepvariant_native = "off"` in config.toml.

GATK and Clair3 outputs are clipped by variant POS to the partition, so a call
overlapping a boundary cannot be emitted by both partitions. Interval-restricted
assembly and Clair3's haploid filtering may differ from other callers; the shared
ploidy contract is **not** a claim of scientific parity. Regression tests use real
fixture alignments and real samtools/bcftools, replacing only the expensive caller
processes. They cover both sides of GRCh37/GRCh38 PAR starts, whole-reference and
bounded requests, and male/female/unknown sex.

## Public bounded qualification

On 2026-11-14, Google's DeepVariant 1.10.0 CPU image called public male HG00096
30x GRCh38 CRAM against hs38DH over `chrX:2770000-2790000` (crossing the
PAR/non-PAR boundary) and `chr20:1000001-1010000`, using two CPUs. The persisted
make_examples and postprocess_variants invocations both received
`--haploid_contigs chrX` and the generated PAR BED. Of 89 records, the 17 non-PAR
X records were homozygous (10 `1/1`, 7 `0/0`); the PAR retained 23 `0/1` calls
and the autosome retained 28 `0/1` calls. This qualifies the bounded Google
GRCh38 path, not whole-genome accuracy, GRCh37, native engines, Clair3 or GATK
scientific parity.
