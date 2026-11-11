# A 30x genome on a 16 GB Apple M1

This page records how the whole toolset runs on the smallest current Apple
Silicon Mac, with **default settings**, and what a user should expect
([#549](https://github.com/WGSExtract/wgsextract-cli/issues/549)). Every
figure is **measured** unless labelled otherwise.

## The computer

- Mac mini, base Apple M1: 4 performance and 4 efficiency cores, 8-core GPU,
  16 GB of unified memory, internal SSD.
- macOS 27.2. It is someone's everyday Mac: their desktop apps stayed open,
  so about 10 GiB of RAM was available when runs started, and macOS already
  held about 0.6 GiB of swap.
- WGS Extract from a source checkout with Pixi, as
  [developer setup](developer_setup.md) describes, plus the `macos-aligners-11`
  builds of `wgse-bwa`, `wgse-bwa-mem2` and `wgse-minimap2` on `PATH`
  ([Apple Silicon](hardware.md#apple-silicon-metal)).
- The README installer also worked (55 s). Its last step opens Finder, which
  fails harmlessly over SSH.

Times are wall-clock. Peak memory is the largest resident set of any process
(`/usr/bin/time -l`) and, for long steps, the summed resident memory of all
the user's processes, sampled every 5 s.

## Summary

A whole genome went from a BAM file to a personal report in **7.3 hours** on
the base 16 GB M1, with default settings and its owner's apps still open.
Nothing ran out of memory or disk. The genome, HG002, was sequenced to 35x,
a little deeper than the usual 30x, so a 30x genome should take a little less.

- **`realign` takes 6 hours**, and the GPU sets the pace. Calling SNPs and
  indels takes 68 minutes. Every other step takes under 4 minutes.
- **Memory.** Only `realign` comes close to 16 GB: all processes together used
  10.4 GiB at their peak and macOS swapped up to 4.8 GiB. Every other step
  used under 4 GiB.
- **Disk.** `realign` needs about 166 GiB free at its peak, on top of the input.
- **Accuracy.** Against GIAB v4.2.1, `bcftools` SNP calls have 99.1% recall and
  97.5% precision, and indel calls 95.2% recall and 95.3% precision. 27 of the 29 Y-STRs it
  called match NIST's values for HG002.

## Every command on small data

Two passes ran every command family:

- **Fake genomes** from the test fixtures: 53 commands, each within 30 s
  except `deps check`, and at most 4.1 GiB (`realign`). `deps check` took
  74 s on its first, cold run; it now takes about 10 s
  ([#552](https://github.com/WGSExtract/wgsextract-cli/pull/552)). 44 passed.
  Of the 9 that exited non-zero, 7 stopped for the right reason, with a clear
  message:
  - `vcf deepvariant` needs Docker;
  - `lineage y-haplogroup` and `lineage y-str` find no usable chrY markers in
    the fake genome;
  - `typing str`, `typing hla` and `typing cyp2d6` refuse it because its
    scaled reference is neither GRCh37 nor GRCh38;
  - `microarray` needs the hg38-to-hg19 liftover chain from the reference
    library (`ref bootstrap`).

  The other two were faults:
  - `bam unalign` was run without its required `--r1`/`--r2` (a test-script
    error); given them it passes in 4 s;
  - `analyze comprehensive` stopped at the Y haplogroup step instead of going
    on to call variants. That is fixed in
    [#577](https://github.com/WGSExtract/wgsextract-cli/pull/577).
- **5 million real HG002 read pairs** (about 0.5x) aligned to hs38. Everything
  passed except Y haplogroup and Y-STR, which need more than the 1.9x of
  chrY these reads gave.

| Step (5M read pairs) | Time | Peak memory |
| :--- | ---: | ---: |
| `align`, default (`wgse-bwa` on Metal) | 223 s | 7.79 GiB |
| `align --native-aligners off` (bwa on the CPU) | 955 s | 7.55 GiB |
| `realign` of the CRAM | 247 s | 7.83 GiB |
| `vcf snp`, whole genome, contigs in parallel (4 threads) | 211 s | 0.46 GiB |
| `vcf snp`, whole genome, one stream (before) | 849 s | 0.52 GiB |
| `vcf cnv` | 182 s | 4.46 GiB |
| `vcf sv` | 47 s | 1.40 GiB |
| `typing hla` | 161 s | 2.71 GiB |
| `microarray` | 35 s | 1.05 GiB |

The test suite passed on the M1 (5,826 tests in 6 min 21 s).

## Defaults for 16 GB

Measuring found five problems on this Mac. Each is fixed, and the fix is the
default:

- **`wgse-bwa` fell back to the CPU for a whole genome.** Its Metal estimate
  was compared with the RAM free at the start, which a 16 GB Mac with apps
  open never has. It now runs on Metal whenever its estimate fits within the
  CPU aligner's own estimate, lowering the sort memory to 512M. The CPU
  fallback aligned at ~11k reads/s; Metal aligns at ~41k reads/s, about 4×
  faster ([#563](https://github.com/WGSExtract/wgsextract-cli/pull/563)).
- **`vcf snp` and `vcf indel` used one core.** They now call contigs in
  parallel when `--threads` is above 1, with identical records: 849 s became
  211 s ([#560](https://github.com/WGSExtract/wgsextract-cli/pull/560)).
- **`deps check` took 32 s warm (74 s cold)**, mostly asking Pixi to size the checkout. It now
  takes 9.8 s ([#552](https://github.com/WGSExtract/wgsextract-cli/pull/552)).
- **`realign`'s FASTQ extraction showed 0% for 23 minutes.** It now reports
  how much of the input it has read, then how many reads it has written
  ([#564](https://github.com/WGSExtract/wgsextract-cli/pull/564)).
- **`realign`'s disk pre-flight was a warning** even when the disk had room.
  It is now information ([#556](https://github.com/WGSExtract/wgsextract-cli/pull/556)).

Experiments that left the defaults alone:

- **Threads.** `realign` of 25M reads took 656 s at 4 threads and 637 s at 8:
  the GPU sets the pace (~44k reads/s), and more threads only add memory.
- **Sort memory.** Streaming reads into the aligner (`-m 512M` or less)
  instead of extracting FASTQs saved disk, not time (664–666 s).
- **Variant caller.** `bcftools` is the caller to use on a Mac.
  - GATK HaplotypeCaller runs on one core on arm64: chr20 was not half done
    after 44 minutes, which would be days for a genome.
  - Google's DeepVariant image is x86-64 only and can't run on Apple Silicon:
    Docker Desktop's emulation lacks the AVX instructions its TensorFlow
    needs. `vcf deepvariant` refuses it, and uses only the native ARM64 image
    ([deepvariant-arm64.md](deepvariant-arm64.md#apple-silicon-macs)).

## A whole genome (HG002 at 35x)

The input was the public GIAB HG002 NovaSeq PCR-free 35x BAM (830 million
reads, 43 GiB) from Google's DeepVariant case study. `realign` aligned it
again to hs38 (GRCh38 with no ALTs). Each command ran with defaults, one after
another, with `/usr/bin/time -l`. A sampler recorded swap, free memory, the
summed memory of all the user's processes, and disk use every 5 s, and the
process list every 30 s
([evidence](https://github.com/WGSExtract/wgsextract-cli/issues/549)).

| Step | Time | Peak memory |
| :--- | ---: | ---: |
| `realign` (extract 23 min, align and sort 5 h 39 min, index 67 s) | 6 h 2 min | 8.40 GiB |
| `info --detailed` | 2 s | 0.23 GiB |
| `vcf snp` | 31 min | 0.53 GiB |
| `vcf indel` | 37 min | 0.30 GiB |
| `microarray` | 196 s | 1.51 GiB |
| `lineage mt-haplogroup` | 3 s | 0.23 GiB |
| `lineage y-haplogroup` | 11 s | 0.59 GiB |
| `lineage y-str` (Y-37) | 1 s | 0.12 GiB |
| `extract mito-fasta` | 5 s | 0.11 GiB |
| `extract ydna-vcf` | 69 s | 0.11 GiB |
| `extract unmapped` | 70 s | 0.12 GiB |
| `typing hla` | 162 s | 3.60 GiB |
| `typing cyp2d6` | 25 s | 0.58 GiB |
| `report` | under 1 s | 0.13 GiB |

Peak memory is the largest single process. During `realign` all the user's
processes together peaked at 10.42 GiB, and swap grew from 1.5 to 4.8 GiB;
during calling they stayed under 1.6 GiB, and swap didn't grow in any later
step.

- **`realign` aligned about 41,000 reads a second.** It chose the Metal GPU and
  lowered the sort memory to 512M a thread to make room for it. Free memory
  never fell below 16%. A rerun on the current
  version skips the index step, because `align` and `realign` now index while
  sorting ([#545](https://github.com/WGSExtract/wgsextract-cli/issues/545)).
- **Disk.** Disk use rose by at most 166 GiB above the input: the FASTQs (deleted
  once aligned), the sort's temporary files and the 46 GB BAM. `realign`'s
  pre-flight asked for about 185 GB.
- **Calling** used 4 workers, the default for the M1's four performance
  cores.
- **Results.**
  - mtDNA haplogroup H5a7; Y haplogroup J-S4994.
  - Y-37: 29 markers called, and 27 of them match NIST's HG002 values.
    The 8 not called are in palindromes, longer than the reads, or poorly
    supported.
  - HLA: A\*01:01/\*26:01, B\*35:08/\*38:01, C\*04:01/\*12:03,
    DRB1\*04:02/\*10:01, DQB1\*03:02/\*05:01.
  - CYP2D6 \*2/\*4.
  - `report` gathered these into one HTML page. Its sample overview is
    empty, because `info --detailed` records its metrics only inside a
    [sample workspace](sample-workspace.md).

## Accuracy

`rtg vcfeval` compared the merged SNP and indel calls with the GIAB HG002
v4.2.1 GRCh38 benchmark (chr1–22), inside its benchmark regions. hap.py has no
macOS arm64 build; the counts are split into SNPs and indels the way hap.py
reports them.

| Type | Recall | Precision | F1 | True positives | False negatives | False positives |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: |
| SNPs | 0.9907 | 0.9747 | 0.9826 | 3,335,029 | 31,345 | 86,482 |
| Indels | 0.9518 | 0.9533 | 0.9526 | 500,090 | 25,307 | 24,403 |

They are close to the chr20-only result on the same Mac (SNPs 0.9932 recall,
0.9746 precision; indels 0.9498 and 0.9496). Google's DeepVariant image can't
run on a Mac (see above).

## `wgse-bwa` or `wgse-bwa-mem3`

On Macs with room for it, `wgse-bwa-mem3` aligns 1.8 times as fast as
`wgse-bwa`, for about 11 GiB of memory against 6
([Apple Silicon](hardware.md#apple-silicon-metal)). On the same 16 GB M1, with its
owner's desktop session open (12–13 GiB available), it doesn't fit
([#609](https://github.com/WGSExtract/wgsextract-cli/issues/609)). Both aligners
were from `macos-aligners-14` (`wgse-bwa` 0.9.0, `wgse-bwa-mem3` 0.2.0
`--compat=bwa-mem`), aligning 5 million HG002 read pairs to hs38. Every completed
run with `-K 10000000` wrote the same records, from either aligner.

| Aligner, backend, threads (`-K 10000000`) | Time | Peak memory |
| :--- | ---: | ---: |
| `wgse-bwa --metal`, 8 threads | 201–202 s | 6.2 GiB |
| `wgse-bwa --metal`, 4 threads | 205 s | 6.3 GiB |
| `wgse-bwa` on the CPU, 8 threads | 360–363 s | 5.5 GiB |
| `wgse-bwa` on the CPU, 4 threads | 490–492 s | 5.5 GiB |
| `wgse-bwa-mem3 --metal`, 8 threads | 668 s; over 900 s | 10.8 GiB |
| `wgse-bwa-mem3 --metal`, 4 threads | 721 s | 10.6 GiB |
| `wgse-bwa-mem3` on the CPU, 8 threads | 797 s; over 900 s | 10.6 GiB |
| `wgse-bwa-mem3` on the CPU, 4 threads | over 900 s | 10.5 GiB |

- **`wgse-bwa-mem3` thrashed.** macOS compressed memory to make room, free
  memory fell to 36–39%, and the load average was seen at about 50. The Mac
  stopped answering SSH for about 25 minutes.
- **With the default batch size it was worse.** No run finished in 900 s, and
  `--metal` at 8 threads got through only a tenth of the reads. `wgse-bwa`
  with its default batch size took the same time as with `-K 10000000`.
- **`wgse-bwa` is the aligner to use here.** On the GPU it was 3.3 to
  3.5 times as fast as `wgse-bwa-mem3`, at any thread count.
- **`align` picks it.** With the defaults, `align` estimated 19.2 GiB for
  `wgse-bwa-mem3` against 12.0–13.0 GiB available, kept `wgse-bwa` on the GPU, and
  took 209–211 s. Forced with `--aligner bwa-mem3` it took 2,144 s (ten times as
  long), swap grew from 1.8 to 5.0 GiB, and the records were the same.

### The whole genome again, with `macos-aligners-14`

`macos-aligners-14` is the aligner release with `wgse-bwa` 0.9.0
([Installing](../native/README.md#installing)).

The same 35x HG002 BAM was realigned to hs38 again with the defaults
([#609](https://github.com/WGSExtract/wgsextract-cli/issues/609)): WGS Extract
0.8.6 from main, with `wgse-bwa` 0.9.0 and `wgse-bwa-mem3` 0.2.0 on `PATH` and
bwa-mem2's index (without `.0123`) beside hs38. #549's reference library had
been deleted, so hs38 was an uncompressed copy instead of its bgzipped one,
with the same BWA index files.
`realign`'s pre-flight found about 8.4 GiB of RAM available at the start.

| Step | #549 (`wgse-bwa` 0.7.1) | #609 (`wgse-bwa` 0.9.0) |
| :--- | ---: | ---: |
| `realign` | 6 h 2 min | 5 h 52 min |
| extract FASTQs | 23 min | 22.5 min |
| align and sort | 5 h 39 min | 5 h 29 min |
| peak memory (largest process) | 8.40 GiB | 8.41 GiB |
| all processes together | 10.42 GiB | 10.24 GiB |
| swap | 1.5 to 4.8 GiB | 1.1 to 4.9 GiB |
| lowest free memory | 16% | 18% |
| disk above the input | 166 GiB | 164 GiB |
| `vcf snp` | 31 min | 31.5 min |
| `vcf indel` | 37 min | 36 min |

- **`realign` chose `wgse-bwa` on the GPU.** It estimated 29.6 GiB for
  `wgse-bwa-mem3` against the 9.7 GiB available when alignment began, after extraction,
  so it didn't use it. It
  sorted with 512M a thread and extracted FASTQs, because holding a collate
  bucket beside the aligner didn't fit.
- **The record counts matched #549's**: 835,260,415, of which 832,735,204
  mapped.
- **The time barely changed.** On the GPU the run took about as long as
  with 0.7.1; the GPU still sets the pace.
- **The pre-flight warned and the run still finished.** It reported memory
  (22.0 GiB estimated against 8.4 available) and disk (230 GiB against 223
  free) as LOW, then completed within 164 GiB of extra disk.

## Advice for 16 GB Macs

- **Close what you can before `realign`,** and leave the Mac on overnight.
  It runs at the GPU's pace, and more threads only use more memory.
- **Have about 230 GB free** for a 30x `realign`: 166 GiB at its peak, plus
  the input.
- **Install the `macos-aligners` builds** ([Apple Silicon](hardware.md#apple-silicon-metal)).
  Without them `realign` runs bwa on the CPU, about four times slower.
- **Call with `vcf snp` and `vcf indel`** (`bcftools`). They use the four
  performance cores and little memory.
- **Run the other commands as you like.** They take minutes and stay well
  within 16 GB.
