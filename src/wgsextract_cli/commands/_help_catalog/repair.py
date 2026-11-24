"""Help entries for ``repair``."""

from . import CommandHelp, _h

ENTRIES: dict[str, CommandHelp] = {
    "repair ftdna-bam": _h(
        "<input stem>_repaired.bam (+ .bai), or the --output path.",
        "wgsextract repair ftdna-bam --input ftdna.bam",
    ),
    "repair ftdna-vcf": _h(
        "<input stem>_repaired.vcf, or the --output path.",
        "wgsextract repair ftdna-vcf --input ftdna.vcf",
    ),
}
