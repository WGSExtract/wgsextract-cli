# Y-DNA and mitochondrial workflows

```bash
wgsextract extract mito-vcf --input sample.cram
wgsextract lineage mt-haplogroup --input sample.cram
wgsextract lineage y-haplogroup --input sample.cram
wgsextract lineage y-str --input sample.cram --panel Y-37
```

Maternal prediction uses Haplogrep 3, with optional haplocheck contamination
testing. `lineage mt-haplogroup --tree-links` adds the YFull mtDNA branch and
FTDNA's public mtDNA tree (use its branch search); the corresponding Y command
adds YFull/FTDNA links.

## Y haplogroups and STRs

`lineage y-haplogroup` uses native code derived from
[Yleaf v4.1.6](https://github.com/genid/Yleaf), not an external installation.
Predictions, marker/QC tables and version provenance go to `<outdir>/yleaf/`;
the user's directory is never deleted. Build tables are lazy, SHA-256-verified
downloads from this repository's `yleaf-data-v4.1.6` release into the configured
reference library, subject to [network consent](privacy.md).
CRAM and variant-only VCF inference require a matching `--ref`;
use `--no-ref-inference` for targeted VCF panels. Align FASTQ first.

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

## Y and mitochondrial extracts

`extract ydna-bam --poz` writes indexed `_Y_poz.bam` from the original
WGS Extract CombBED/McDonald/Poznik union (~12 Mb), **not** all stable SNPs
or a BigY/exome capture. Exact GRCh37/hg19 or GRCh38 Y lengths are required,
with `chrY` or `Y`, including Y-only inputs. Original BED mitochondrial
regions are excluded; boundary-overlapping reads remain whole.
T2T, unknown and inconsistent assemblies are rejected, not lifted.
Bundled BED [provenance and licence](../src/wgsextract_cli/assets/reference/poz/NOTICE.txt) require
no download. Without `--poz`, whole-Y extraction is unchanged.

`extract unmapped --fastq` adds `_unmapped_R1.fastq.gz`,
`_unmapped_R2.fastq.gz`, `_unmapped_SE.fastq.gz` (unpaired) and
`_unmapped_singletons.fastq.gz` (absent mates). Without it, only BAM is written.

## Opt-in yBrowse Y SNP calling (GRCh38 only)

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

The explicit download pins compressed SHA-256
`b7984349c575c3d582933993a92770d6143603d3b895d3a0612340b167f37b62`
from <https://ybrowse.org/gbrowse2/gff/snps_hg38.vcf.gz>.
The mutable URL's changed hash fails without replacing installed data.
`--snapshot` deliberately selects a local VCF or VCF.gz replacement: GRCh38 Y
length, copyright, annotation header and sorted coordinates are verified, and
its hash is recorded. Only SNP sites/annotations are installed, never sample
columns. No dataset is bundled or redistributed by these commands.
Since bootstrap v0.2.0, `ref bootstrap` includes no YBrowse files; old
`snps_*.vcf.gz` bootstrap files are unused and safe to delete.

Attribution: **CC-BY-SA Thomas Krahn YSEQ (2022)**. The exact statement/source
are retained in local manifests and VCF headers. No CC-BY-SA version is
specified: clarify before redistributing adaptations. This is local processing,
not a licensed-data mirror. The separate `yfull-ybrowse-positions` module
does redistribute positions with attribution and share-alike notice below.

Calling never downloads or refreshes. It validates samtools/bcftools/bgzip/tabix,
an indexed matching GRCh38 FASTA, and REF-allele agreement before calling.
`_Y_ybrowse.vcf.gz` has haploid genotypes at installed targets, aliases in ID
and allele-matched HG/ISOGG annotations. Duplicate aliases combine only for
the same REF/ALT. Multiallelic splitting never turns another ALT into reference;
uncovered sites stay missing, not ancestral. AA annotations are retained:
GRCh38 REF may be derived, so GT=1 need not mean derived.
HG is a marker annotation, **not a sample haplogroup prediction**.
Non-SNP/symbolic alleles are omitted and counted. `chrY`/`Y` is adapted;
GRCh37 and T2T are unsupported. Without `--ybrowse`, extraction is unchanged.

## Data licences

The program is GPL-3.0. Runtime reference data is separate, with its own
licences that do not apply to the program. Modules are pinned by SHA-256
and fetched into the library when explicitly requested/authorized, not bundled.

| Data module | Used by | Licence |
| --- | --- | --- |
| `yfull-tree` | `lineage y-haplogroup --tree yfull` (default) | CC-BY-4.0, YFull |
| `yfull-ybrowse-positions` | Same, unless `--pos-file` is given | CC-BY-SA (version unspecified), Thomas Krahn YSEQ (2022) |
| Yleaf data `v4.1.6` | Y-haplogroup QC tables and other trees | GPL-3.0-or-later, Yleaf |
| YBrowse SNP VCF | `ref ybrowse --install`, `extract ydna-vcf --ybrowse` | CC-BY-SA, Thomas Krahn YSEQ; downloaded on request, not redistributed or in bootstrap since v0.2.0 |

YFull modules print a licence notice on first download; see
[tree data](../src/wgsextract_cli/ydna/yleaf_native/README.md#trees).
