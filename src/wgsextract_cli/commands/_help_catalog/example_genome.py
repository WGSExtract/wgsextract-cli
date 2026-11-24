"""Help entries for ``example-genome``."""

from . import _PACBIO_TAG, _STARTER_EXAMPLE, CommandHelp, _h

ENTRIES: dict[str, CommandHelp] = {
    "example-genome list": _h(
        "Prints the curated examples; writes no files.",
        "wgsextract example-genome list",
        f"wgsextract example-genome list --tag {_PACBIO_TAG}",
    ),
    "example-genome download": _h(
        "Into <mygenomes_library>/test-1000genomes/<example id>/, with a genome "
        "config file.",
        f"wgsextract example-genome download {_STARTER_EXAMPLE}",
        f"wgsextract example-genome download {_STARTER_EXAMPLE} --dry-run",
    ),
}
