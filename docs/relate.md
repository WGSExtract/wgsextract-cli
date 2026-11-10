# Sex, relatedness and ancestry (`qc relate`)

`wgsextract qc relate` answers three questions with
[somalier](https://github.com/brentp/somalier) (Pedersen et al. 2020,
*Genome Medicine*):

- **Is this file really mine?** It infers the sex from chrX and chrY and checks
  it against the sex already recorded for the file.
- **Are these kits from relatives?** It estimates the relatedness of every pair
  of inputs: about 1 for the same person, 0.5 for parent and child or full
  siblings, 0.25 for second-degree relatives and about 0 for unrelated people.
- **Which continental group?** It predicts one of the five 1000 Genomes
  superpopulations (AFR, AMR, EAS, EUR, SAS). This is a coarse continental
  estimate, not an ancestry breakdown.

```sh
pixi install -e somalier                 # once: the optional somalier tool
wgsextract ref somalier --install        # once: sites and 1000 Genomes sketches (~85 MB)
wgsextract qc relate me.cram
wgsextract qc relate child.cram mother.cram father.cram --ped trio.ped
wgsextract qc relate me.cram somalier/cousin.somalier
```

## Inputs

BAM, CRAM and VCF files, and `.somalier` sketches from an earlier run. Each
BAM, CRAM or VCF is sketched at about 17,800 common SNPs (a few seconds for a
VCF, a few minutes for a 30x CRAM). The reference is found from the file's
header, as for other commands, or given with `--ref`. The sketches are kept in
`<outdir>/somalier/`: they are a few hundred kilobytes, so a relative can send
theirs instead of a whole genome.

somalier picks the sites file that matches each input's build and contig
names (hg38, hg19/GRCh37 and T2T-CHM13). Kits on different builds can be
compared. `--sites` uses your own sites VCF instead, for example for an
exome.

A BAM or CRAM gives the most accurate results. A VCF's genotypes are used
as called (REF and the site's ALT only; filtered, missing or other-allele
calls are unknown). A VCF lists only variants, so a site it lacks is usually
homozygous reference, but only in a whole-genome call set:

- For a sample of a **whole-genome, variants-only** VCF (single-sample or
  joint-called), missing autosomal sites are written into its sketch as
  homozygous reference. Such a sketch can be reused, or compared with BAMs,
  without changing the result. "Whole genome" means non-reference calls on at
  least 95% of the autosomes, at least 80% of the variant sites the sites'
  allele frequencies predict, and at least 400 records per autosomal megabase
  in the whole file. Whole-genome VCFs reach 89-100% of the sites (GIAB
  HG002/HG003/HG004 v4.2.1 98%, 1000 Genomes NA19240 89% and HG00513 92%);
  HG002's chromosomes 1-6 alone reach 38% and its chr20 3%. somalier's sites
  suit exomes, so HG002 in the xGen exome (+-100 bp) still reaches 90%; its
  25 records per megabase, against 1,408 for the whole VCF, tell it apart.
- For any other variants-only VCF (one chromosome, an exome or panel),
  missing sites stay unknown. The sample's relatedness then rests on its
  variant sites alone, so its pairs are reported as `uncertain`, with a
  `vcf_called_sites_only` warning.
- An all-sites VCF writes its hom-ref calls out, so they are used as they
  are, and its missing sites stay unknown, as a BAM's uncovered sites do;
  no warning.

chrX and chrY are never filled, so the sex reads only real calls. gVCFs are
refused: their no-call blocks can't be told from reference blocks.

Sample names come from the read group (SM; else the file name) or the VCF
header, and for a sketch from the name stored inside it, so a renamed
`.somalier` file still matches its pedigree row. A BAM/CRAM of several
samples, and names with path separators, are refused. When a BAM, CRAM or VCF
has the sample name of another input, it is prefixed with its file name.

## Outputs

In `--outdir`, else beside the first input (for sketches in an earlier run's
`somalier/` folder, in that run's folder):

| File | Contents |
| :--- | :--- |
| `relate.samples.tsv` | One row per sample: inferred sex and its confidence, the sex recorded before, the superpopulation and its probability, depths and chrX heterozygosity |
| `relate.pairs.tsv` | One row per pair: relatedness, likely relationship, IBS0/IBS2, sites shared, expected relatedness from `--ped`, and whether it is flagged |
| `relate.html`, `relate.ancestry.html` | somalier's interactive reports |
| `somalier/<sample>.somalier` | The sketches |
| `relate.manifest.json` | The [result manifest](result-manifest.md) |

`--format parquet` writes the two tables as Parquet.

## Warnings

| Code | Meaning |
| :--- | :--- |
| `sex_mismatch` | somalier's sex disagrees with the sex recorded for the file: your override, a sex detected earlier, or for a BAM its chrX/chrY read counts (a CRAM is compared only once `info` or another command has recorded its sex) |
| `vcf_called_sites_only` | A VCF sample isn't a whole-genome variant call set: its pairs are `uncertain` |
| `same_person` | Two inputs are the same person |
| `relationship_mismatch` | With `--ped`: a pair is more than 0.1 away from the relatedness the pedigree implies, for example a swapped file |
| `ped_sex_mismatch` | With `--ped`: a sample's sex disagrees with the pedigree |

`--ped` takes a PED or PLINK `.fam` file (family, sample, father, mother,
sex), tab- or space-separated, naming the samples as `qc relate` does.

The sex and superpopulation of each BAM or CRAM are recorded in its
[sample workspace](sample-workspace.md) as `somalier_sex` and
`superpopulation`.

## Resources and licences

`ref somalier --install` downloads, and checks against pinned SHA-256
digests:

- somalier's sites files (MIT), from the brentp/somalier release attachments;
- `ancestry-labels-1kg.tsv` (MIT), from the somalier repository;
- `1kg.somalier.tar.gz`, sketches of the 2,504 unrelated 1000 Genomes samples
  from the NYGC high-coverage data, from
  [Zenodo record 3479773](https://zenodo.org/records/3479773) (CC BY 4.0,
  Brent Pedersen).

They go in `<library>/somalier/`. Each sketch's SHA-256 is recorded when the
archive is unpacked and checked before every use (well under a second); `ref somalier --delete` removes them.
Ancestry runs when they are installed, unless `--sites` gives another panel
(`--no-ancestry` skips it).

## Platforms

somalier is built for Linux and macOS (x86-64 and ARM64) and is not available
on Windows; use WSL2 there.
