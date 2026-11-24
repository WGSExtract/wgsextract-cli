"""Help entries for ``align``."""

from . import CommandHelp, _h

ENTRIES: dict[str, CommandHelp] = {
    "align": _h(
        "<reads stem>_aligned.bam or .cram (+ index).",
        "wgsextract align --r1 sample_R1.fastq.gz --r2 sample_R2.fastq.gz --ref hs38.fa",
        "wgsextract align --r1 hifi_reads.fastq.gz --preset map-hifi --ref hs38.fa",
    ),
}
