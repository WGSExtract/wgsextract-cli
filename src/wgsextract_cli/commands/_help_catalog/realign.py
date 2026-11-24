"""Help entries for ``realign``."""

from . import CommandHelp, _h

ENTRIES: dict[str, CommandHelp] = {
    "realign": _h(
        "<input stem>_aligned.bam or .cram (+ index); with --keep-fastq, also "
        "<input stem>_R1/_R2.fastq.gz (single-end ONT/PacBio: <input "
        "stem>.fastq.gz). Completed steps are recorded in "
        "<output>.steps.json, so a rerun resumes after a crash.",
        "wgsextract realign --input sample_hg19.bam --ref hs38.fa",
        "wgsextract realign --input sample_hg19.bam --ref hs38.fa --dry-run",
    ),
}
