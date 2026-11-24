"""Help entries for ``sample``."""

from . import CommandHelp, _h

ENTRIES: dict[str, CommandHelp] = {
    "sample show": _h(
        "Prints the sample's overrides, recorded facts and results; writes no files.",
        "wgsextract sample show --genome HG002",
        "wgsextract sample show --input sample.cram --json",
    ),
    "sample set": _h(
        "wgsextract.sample.toml in the sample folder (the --genome folder, "
        "--workspace or the input's folder).",
        "wgsextract sample set --genome HG002 --sex Female",
        "wgsextract sample set --input sample.cram --platform ont",
        "wgsextract sample set --input ~/Downloads/mom.bam --sex Female",
    ),
    "sample unset": _h(
        "wgsextract.sample.toml in the sample folder.",
        "wgsextract sample unset --genome HG002 --sex",
        "wgsextract sample unset --input sample.cram --platform",
    ),
}
