"""Help entries for ``pet-align``."""

from . import CommandHelp, _h

ENTRIES: dict[str, CommandHelp] = {
    "pet-align": _h(
        "<reads stem>_<species>.bam or .cram (+ index) and "
        "<reads stem>_<species>.vcf.gz (+ .tbi).",
        "wgsextract pet-align --r1 dog_R1.fastq.gz --r2 dog_R2.fastq.gz --species dog",
    ),
}
