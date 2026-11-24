"""Help entries for ``help``."""

from . import CommandHelp, _h

ENTRIES: dict[str, CommandHelp] = {
    "help": _h(
        "Prints to stdout; writes no files.",
        "wgsextract help --all",
        "wgsextract help vcf snp",
    ),
}
