"""Help entries for ``fastq``."""

from . import CommandHelp, _h

ENTRIES: dict[str, CommandHelp] = {
    "fastq archive": _h(
        "A new .wgfq archive (--output). Source files are not modified.",
        "wgsextract fastq archive --r1 sample_R1.fastq.gz --output sample.wgfq",
        "wgsextract fastq archive --r1 sample_R1.fastq.gz --r2 sample_R2.fastq.gz --output sample.wgfq",
    ),
    "fastq restore": _h(
        "Verified uncompressed FASTQ (--output and, for pairs, --output-r2).",
        "wgsextract fastq restore --input sample.wgfq --output sample.fastq",
        "wgsextract fastq restore --input sample.wgfq --output sample_R1.fastq --output-r2 sample_R2.fastq",
    ),
}
