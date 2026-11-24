"""Help entries for ``deps``."""

from . import CommandHelp, _h

ENTRIES: dict[str, CommandHelp] = {
    "deps check": _h(
        "Prints each tool's status; writes no files.",
        "wgsextract deps check",
        "wgsextract deps check --tool samtools",
    ),
    "deps list": _h(
        "Prints each tool's status and what can be done about it; writes no files.",
        "wgsextract deps list",
        "wgsextract --json deps list",
    ),
    "deps ready": _h(
        "Prints each prerequisite of the command line, its status and the "
        "explicit step that provides a missing one; writes no files. With "
        "--json, the result's details list every item and its evidence.",
        "wgsextract deps ready -- vcf snp --input sample.bam",
        "wgsextract --json deps ready -- vcf clair3 --input ont.bam --gpu",
    ),
    "deps install": _h(
        "Installs the tool's Pixi environment beside the CLI's own.",
        "wgsextract deps install gatk",
        "wgsextract deps install sniffles",
    ),
    "deps uninstall": _h(
        "Removes the tool's Pixi environment, and the other tools in it.",
        "wgsextract deps uninstall gatk",
    ),
}
