"""Help entries for ``bam``."""

from . import CommandHelp, _h

ENTRIES: dict[str, CommandHelp] = {
    "bam identify": _h(
        "Prints the reference the file was aligned to; writes no files.",
        "wgsextract bam identify --input sample.bam",
    ),
    "bam sort": _h(
        "<input stem>_sorted.bam or .cram (+ index).",
        "wgsextract bam sort --input sample.bam",
    ),
    "bam index": _h(
        "Beside the input: <input>.bai or .crai.",
        "wgsextract bam index --input sample.bam",
    ),
    "bam unindex": _h(
        "Deletes the input's .bai/.crai index; writes no files.",
        "wgsextract bam unindex --input sample.bam",
    ),
    "bam unsort": _h(
        "<input stem>_unsorted.bam or .cram.",
        "wgsextract bam unsort --input sample.bam",
    ),
    "bam to-cram": _h(
        "<input stem>.cram (+ .crai).",
        "wgsextract bam to-cram --input sample.bam --ref hs38.fa",
    ),
    "bam to-bam": _h(
        "<input stem>.bam (+ .bai).",
        "wgsextract bam to-bam --input sample.cram",
    ),
    "bam unalign": _h(
        "<input stem>_R1.fastq.gz and _R2.fastq.gz, or the FASTQs --r1/--r2 "
        "name (and --se).",
        "wgsextract bam unalign --input sample.cram",
    ),
    "bam merge": _h(
        "merged.bam or merged.cram (+ index), or the --output path; each run "
        "keeps its own read group under one sample name (SM).",
        "wgsextract bam merge lane1.bam lane2.bam --output sample.bam",
        "wgsextract bam merge run1.bam run2.bam --sample HG002 --output HG002.bam",
    ),
    "bam to-ubam": _h(
        "<input stem>_unaligned.bam: queryname-sorted unaligned reads.",
        "wgsextract bam to-ubam --input sample.cram",
        "wgsextract bam to-ubam --r1 sample_R1.fastq.gz --r2 sample_R2.fastq.gz "
        "--sample NA12878",
    ),
}
