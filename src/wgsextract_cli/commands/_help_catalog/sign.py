"""Help entries for ``sign``."""

from . import CommandHelp, _h

ENTRIES: dict[str, CommandHelp] = {
    "sign create": _h(
        "SHA256SUMS in the inputs' folder (or --output); SHA256SUMS.sig with --key.",
        "wgsextract sign create results/",
        "wgsextract sign create results/ --key ~/.ssh/id_ed25519",
    ),
    "sign verify": _h(
        "Prints the verification result; writes no files.",
        "wgsextract sign verify results/SHA256SUMS --public-key key.pub",
    ),
}
