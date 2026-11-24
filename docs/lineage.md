# Y-DNA and mitochondrial workflows

```bash
wgsextract extract mito-vcf --input sample.cram
# First use: explicitly allow missing public trees/tables to be downloaded
wgsextract lineage mt-haplogroup --input sample.cram --download
wgsextract lineage y-haplogroup --input sample.cram --download
wgsextract lineage y-str --input sample.cram --panel Y-37
```

`ref bootstrap` does not install these haplogroup trees/tables. `--download`
authorizes their public-data downloads; omit it once the needed resources
are cached in the reference library to run offline. No sample data is uploaded.

Maternal prediction uses Haplogrep 3, with optional haplocheck contamination
testing. `lineage mt-haplogroup --tree-links` adds the YFull mtDNA branch and
FTDNA's public mtDNA tree (use its branch search); the corresponding Y command
adds YFull/FTDNA links.

### Mitochondrial reference coordinates

Haplogrep classifies rCRS coordinates. CHM13v2.0's original chrM has the
same 16,569-base length but is rotated by 576 bases and has three other
differences. Length alone does not identify rCRS: the sequence dictionary's
M5/MD5 tag, the matching reference FASTA, or VCF REF alleles identify it.
Reference metadata requires a known full-sequence MD5, from the header or
computed from the FASTA. A matching start anchor alone cannot rule out
coordinate-shifting indels; unknown digests or unavailable sequence evidence
leave mitochondrial identity unknown. Contig-name/length signatures identify
the assembly but do not establish mitochondrial identity.

For a CHM13 BAM or CRAM, supply its matching `--ref`. The command calls
all mitochondrial sites and maps them to rCRS through the bundled, pinned
microarray mitochondrial alignment before running Haplogrep. The result
records this conversion in `details.mt_reference`. A CHM13 VCF must include
reference-only calls (all-sites VCF or gVCF); a variants-only VCF is refused
with instructions to use the alignment or call all sites. Simply rotating
variant positions would lose sample alleles at reference-difference sites.

## Y haplogroups and STRs

`lineage y-haplogroup` uses native code derived from
[Yleaf v4.1.6](https://github.com/genid/Yleaf), not an external installation.
Predictions, marker/QC tables and version provenance go to `<outdir>/yleaf/`;
the user's directory is never deleted. Missing build tables require explicit
`--download` consent for SHA-256-verified downloads from this repository's
`yleaf-data-v4.1.6` release into the configured reference library. Cached tables
work offline; see [network consent](privacy.md).
CRAM decoding and gVCF block expansion require a matching `--ref`;
use `--no-ref-inference` for targeted VCF panels. Align FASTQ first.
Absent VCF markers are unknown unless confirmed callable by a reference block
passing the caller's quality model and block-wide MIN_DP thresholds (bcftools
reference blocks are depth-only; GQ-based callers also require GQ); all-sites REF calls are accepted
at their recorded positions. Variant-only VCFs warn that results may be
incomplete and suggest gVCF/all-sites input. `--assume-reference` explicitly
opts into upstream Yleaf's unsafe missing-site reference assumption for parity
checks; it may bias predictions toward the reference genome's Y lineage.

Choose `--tree yfull|yfull_v10|ftdna|isogg`. Default `yfull` pins YFull YTree
14.06.0 by SHA-256 and records the version. Register custom trees in
`[y_trees.<id>]` in config.toml (local files or SHA-256-pinned URLs), or pass
`--tree-file` and `--pos-file` for one run. `y_tree` selects the default.
`--min-reads`, `--base-quality`, `--base-majority`, `--min-score`, `--ancient`
and `--pos-file` replace external-tool passthrough. See
[native Yleaf documentation and upstream sync](../src/wgsextract_cli/ydna/yleaf_native/README.md).

Yleaf credits Arwin Ralf, Diego Montiel Gonzalez, Kaiyin Zhong, Manfred Kayser
and Bram van Wersch at Erasmus MC. Cite Ralf et al.,
*Molecular Biology and Evolution* 35(5):1291–1294 (2018),
[doi:10.1093/molbev/msy032](https://doi.org/10.1093/molbev/msy032).
Modified files retain GPL-3.0-or-later; see [NOTICE](../NOTICE).

`lineage y-str` reports FTDNA Y-12 through Y-111, PowerPlex Y23 and Yfiler Plus
repeat counts from hg19, hg38 or T2T BAM/CRAM. It needs only samtools, unlike
`typing str` for disease expansions. `<outdir>/ystr/` holds per-marker TSV,
FTDNA-ordered CSV and `run.json`. Reads must span the repeat and both flanks:
long markers can be no-calls; DYS464, CDY, DYF387S1 and Y-GGAAT-1B07 are not
callable. Against 1000 Genomes PowerPlex Y23 typings, 99.9% of 30x calls match,
with 8% no-calls. See [sources, licences and conventions](../src/wgsextract_cli/ydna/ystr/README.md).

## Candidate private Y SNPs

`lineage y-private` lists Y SNPs that may be the family's own branch: called
chrY SNVs that no installed marker set lists. They are **candidates** to
confirm (for example by Sanger sequencing or a second test), not proven
private SNPs: sequencing and mapping errors pass the same filters.

```bash
# GRCh38: needs the YBrowse snapshot (see below) and, once, the YFull positions.
wgsextract ref ybrowse --install --ref /path/to/reference-library
wgsextract lineage y-private --input sample.cram --download
# T2T, or a single-sample VCF/BCF with FORMAT/AD (DeepVariant, GATK, bcftools).
wgsextract lineage y-private --input sample.vcf.gz --build t2t
```

- **Novel** means the call's alleles are in no installed marker set: on GRCh38
  (primary) the [YBrowse snapshot](#opt-in-ybrowse-y-snp-calling-grch38-only)
  and the `yfull-ybrowse-positions` module of the pinned YFull YTree; on
  T2T-CHM13v2.0 only the YFull positions, since the YBrowse snapshot is GRCh38
  only (`known_markers_partial` warning). A call at a known site with other
  alleles is still a candidate, noted `known site, other allele`.
  GRCh37/hg19 and other Y assemblies are refused: realign first.
  The input must name one primary Y contig. With a contig length in the header,
  this can be `chrY`, `Y`, or the primary Y's RefSeq/GenBank accession.
- **Callable mask:** calls are made, or read from a VCF, only inside the
  bundled Poz regions (the CombBED/McDonald/Poznik union, ~11.65 Mb on GRCh38,
  ~11.6 Mb on T2T). It leaves out both PARs and the Yq12 heterochromatin, and
  most ampliconic, palindromic and other low-mappability sequence; per-site
  structural classes are not labelled.
- **Filters**, in order: FILTER PASS or missing, QUAL ≥ `--min-qual` (30),
  ALT reads ≥ `--min-alt-reads` (10), ALT fraction ≥ `--min-af` (0.9) of
  the reads of every allele in FORMAT/AD (a call missing any AD value
  fails). SNVs only. A BAM/CRAM is called haploid with bcftools, counting
  reads with MAPQ ≥ `--min-mapq` (20) and bases with quality ≥
  `--base-quality` (20); a VCF's mapping filters are its caller's.
  The marker sets are read from the `--ref` library (or the library holding
  its FASTA) when it has them, else from the configured library.
- **QC:** more than 50 candidates raises `y_private_excess`: suspect
  contamination, a mismatched reference, poor data or a marker set that misses
  the lineage. A female sample, or reads of several samples, are refused.

`<outdir>/y_private/` holds `<sample>.y_private.tsv` (candidates; its `#`
header states the marker sets, mask and filters) and `<sample>.y_private.vcf.gz`.
The result manifest's `details` records the `definition`, `marker_sets`,
`callable_mask`, `filters`, the `counts` at each step and the `candidates`.

## Y and mitochondrial extracts

`extract ydna-bam --poz` writes indexed `_Y_poz.bam` from the original
WGS Extract CombBED/McDonald/Poznik union (~12 Mb), **not** all stable SNPs
or a BigY/exome capture. Exact GRCh37/hg19, GRCh38 or T2T-CHM13v2.0 Y lengths
are required, with `chrY` or `Y`, including Y-only inputs. The T2T regions are
the GRCh38 BED lifted with the UCSC `hg38ToHs1` chain (~11.6 Mb). Original BED
mitochondrial regions are excluded; boundary-overlapping reads remain whole.
Unknown and inconsistent assemblies are rejected.
Bundled BED [provenance and licence](../src/wgsextract_cli/assets/reference/poz/NOTICE.txt) require
no download. Without `--poz`, whole-Y extraction is unchanged.

### Y-associated contigs (`--y-scope`)

`extract ydna-bam`, `extract y-mt-extract` and `extract region --target y|ymt`
read only the primary chrY (and chrM) by default: `--y-scope primary`.
`--y-scope associated` adds the reference's Y-associated contigs, writing
`_Y_associated.bam`, `_Y_MT_associated.bam` (and for `region`,
`_Y_associated.vcf.gz`/`.fasta`). Of GRCh37, GRCh38 and T2T-CHM13v2.0 only
GRCh38 assigns one: the 37,240 bp unlocalized scaffold `chrY_KI270740v1_random`,
also named `KI270740.1`, `NT_187395.1` and `HSCHRY_RANDOM_CTG1` (NCBI assembly
report GCA_000001405.15). The alignment header's contig is read only when its
length, its `M5` (if recorded) and a GRCh38-length chrY all match; a
same-named contig that differs, or a Y-like name no supported assembly assigns
to Y, is listed and left out. On GRCh37 and T2T the associated scope reads
chrY alone. A header without @SQ lines or a chrY fails; so does calling, or a
CRAM, whose reference lacks the included contig or has no `.fai` or `.dict`
to show it. Nothing is downloaded.

Records are taken by position, as for chrY: secondary, supplementary and
unmapped mates placed on a selected contig are kept, each record once, in
header order with a BAM index; a mate mapped elsewhere and unplaced unmapped
reads are not. The result's `details` list each considered contig with
`included`, `reason` and, for BAMs, `mapped_records` and
`placed_unmapped_records`; outputs record `y_scope` and `contigs`. `--poz`
and `--ybrowse` take primary chrY only.

**Differences from WGS Extract v4.** v44.2 always added
`chrY_KI270740v1_random` to chr-named build-38 Y and Y+MT BAMs
(`_chrY.bam`, `_chrYM.bam`), by name only, and never for `Y`/`KI270740.1`
naming. `--y-scope associated` keeps those reads under any of the names above.
On HG002 (GRCh38 no-alt, 35x) its Y and Y+MT BAMs hold exactly the records
of v44.2's commands (7,372,465 and 9,938,023; primary chrY alone 7,368,299).
v44.2 left its BAMs unindexed; these are indexed. It is opt-in so the default
outputs keep their meaning. It restores retained reads; no change in
haplogroup predictions was measured, and tools that read only chrY
coordinates ignore the contig.

`extract unmapped --fastq` adds `_unmapped_R1.fastq.gz`,
`_unmapped_R2.fastq.gz`, `_unmapped_SE.fastq.gz` (unpaired) and
`_unmapped_singletons.fastq.gz` (absent mates). Without it, only BAM is written.

## Opt-in yBrowse Y SNP calling (GRCh38, and GRCh37 lifted locally)

```bash
# --ref here names the reference library, not the FASTA.
wgsextract ref ybrowse --install --ref /path/to/reference-library
# Or choose a local, header-verified GRCh38 snapshot.
wgsextract ref ybrowse --install --snapshot /path/to/snps_hg38.vcf.gz --ref /path/to/reference-library
wgsextract extract ydna-vcf --ybrowse --input sample.bam --ref /path/to/GRCh38.fa --outdir results
```

Set the library separately (`wgsextract config` shows its config path):

```toml
reference_library = "/path/to/reference-library"
```

The explicit download fetches the daily-updated `snps_hg38.vcf.gz` snapshot
from <https://ybrowse.org/gbrowse2/gff/snps_hg38.vcf.gz>.
Because upstream refreshes this file daily, downloads are validated by transfer
completeness (`Content-Length` and gzip stream integrity), GRCh38 Y length,
copyright, annotation headers, and sorted coordinates rather than a fixed
upstream hash; any truncated or invalid download fails without replacing
installed data. `--snapshot` selects a local VCF or VCF.gz instead of
downloading. In both cases the snapshot's SHA-256 and prepared file checksums
are recorded in `manifest.json`. Only SNP sites/annotations are installed, never
sample columns. No dataset is bundled or redistributed by these commands.
Since bootstrap v0.2.0, `ref bootstrap` includes no YBrowse files; old
`snps_*.vcf.gz` bootstrap files are unused and safe to delete.

Attribution: **CC-BY-SA Thomas Krahn YSEQ (2022)**. The exact statement/source
are retained in local manifests and VCF headers. No CC-BY-SA version is
specified: clarify before redistributing adaptations. This is local processing,
not a licensed-data mirror. The separate `yfull-ybrowse-positions` module
does redistribute positions with attribution and share-alike notice below.

Calling never downloads or refreshes. It validates samtools/bcftools/bgzip/tabix,
an indexed matching GRCh38 FASTA, and REF-allele agreement before calling.
Sites where the FASTA's chrY is N, such as the PARs masked in hs38 and the
GRCh38 no-alt analysis set, are skipped, not called; their count is in the
VCF header (`##ybrowse_excluded_reference_N_sites`) and the
`ybrowse_reference_n_sites_excluded` warning. Any other REF mismatch stops
the run.
`_Y_ybrowse.vcf.gz` has haploid genotypes at installed targets, aliases in ID
and allele-matched HG/ISOGG annotations. Duplicate aliases combine only for
the same REF/ALT. Multiallelic splitting never turns another ALT into reference;
uncovered sites stay missing, not ancestral. AA annotations are retained:
GRCh38 REF may be derived, so GT=1 need not mean derived.
HG is a marker annotation, **not a sample haplogroup prediction**.
Non-SNP/symbolic alleles are omitted and counted. `chrY`/`Y` is adapted;
T2T is unsupported. Without `--ybrowse`, extraction is unchanged.

### GRCh37

YBrowse's own `snps_hg19.vcf.gz` (last changed 2017) is not used: its
positions are GRCh38 positions under a GRCh37 header, and its REF column was
read from GRCh37 at those wrong positions. Instead, `--grch37` lifts the
installed GRCh38 snapshot to GRCh37 on your computer:

```bash
# Ensembl's pinned GRCh38->GRCh37 chain is fetched only with --download.
wgsextract ref ybrowse --install --grch37 --download --ref /path/to/reference-library
# Or lift with a chain you already have.
wgsextract ref ybrowse --install --grch37 --chain /path/to/GRCh38_to_GRCh37.chain.gz --ref /path/to/reference-library
wgsextract extract ydna-vcf --ybrowse --input sample.b37.bam --ref /path/to/hs37d5.fa.gz --outdir results
```

- **Chain.** Ensembl's
  [`GRCh38_to_GRCh37.chain.gz`](https://ftp.ensembl.org/pub/assembly_mapping/homo_sapiens/GRCh38_to_GRCh37.chain.gz)
  (730 KB, SHA-256 `6bee5bfd…92b1`), saved in the library's `ybrowse/` folder.
  Ensembl
  [places no restrictions](https://www.ensembl.org/info/about/legal/disclaimer.html)
  on its data. Without `--download` or `--chain`, a missing chain stops the
  command and names it; nothing is fetched. UCSC's `hg38ToHg19` chain is not
  used by default because its terms are non-commercial only; you can still give
  it with `--chain`.
- **Reference check.** Each placed allele is checked against an indexed GRCh37
  FASTA: the one `--ref` names, else the one in the library's genomes folder
  (chrY/Y length 59,373,566). Both `Y` and `chrY` naming work, and so do
  hs37d5's N-masked PARs.
- **What is kept.** A site is kept only when the chain places it exactly once,
  on Y. A reverse-strand placement complements its alleles and AA. If GRCh37
  has the GRCh38 ALT, REF and ALT swap and AA stays. Sites placed more than
  once, two GRCh38 sites landing on one GRCh37 position, GRCh37 N bases and
  sites where neither allele matches are dropped, never guessed. The counts are
  in the resource's `manifest.json` and the VCF header
  (`##ybrowse_liftover_counts`), with the chain's name and SHA-256.
- **Staying current.** Re-installing the GRCh38 snapshot makes the GRCh37 sites
  stale: `extract` stops and asks you to rerun `--grch37`.

With the 2026-11-21 snapshot, the lift places 98.2% of the GRCh38 SNP alleles
on GRCh37 (3,002,196 of 3,056,218). On HG002, GRCh37 and GRCh38 targeted calls
agree at over 99.9% of the markers called in both builds. The output is a
marker-annotated VCF, not a haplogroup claim, and it is not meant to match the
v4 GRCh37 output, which came from YBrowse's GRCh37 file.

## Data licences

The program is GPL-3.0. Runtime reference data is separate, with its own
licences that do not apply to the program. Modules are pinned by SHA-256
and fetched into the library when explicitly requested/authorized, not bundled.

| Data module | Used by | Licence |
| --- | --- | --- |
| `yfull-tree` | `lineage y-haplogroup --tree yfull` (default) | CC-BY-4.0, YFull |
| `yfull-ybrowse-positions` | Same, unless `--pos-file` is given; `lineage y-private` | CC-BY-SA (version unspecified), Thomas Krahn YSEQ (2022) |
| Yleaf data `v4.1.6` | Y-haplogroup QC tables and other trees | GPL-3.0-or-later, Yleaf |
| YBrowse SNP VCF | `ref ybrowse --install`, `extract ydna-vcf --ybrowse` | CC-BY-SA, Thomas Krahn YSEQ; downloaded on request, not redistributed or in bootstrap since v0.2.0 |
| Ensembl GRCh38->GRCh37 chain | `ref ybrowse --install --grch37 --download` | Ensembl data, no restrictions on access or use; downloaded on request, not redistributed |
| YBrowse SNP VCF | `ref ybrowse --install`, `extract ydna-vcf --ybrowse`, `lineage y-private` on GRCh38 | CC-BY-SA, Thomas Krahn YSEQ; downloaded on request, not redistributed or in bootstrap since v0.2.0 |

YFull modules print a licence notice on first download; see
[tree data](../src/wgsextract_cli/ydna/yleaf_native/README.md#trees).
