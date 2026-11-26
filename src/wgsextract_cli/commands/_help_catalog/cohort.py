"""Help entries for ``cohort``."""

from . import CommandHelp, _h

ENTRIES: dict[str, CommandHelp] = {
    "cohort summarize": _h(
        "cohort.variants.tsv (or .parquet) and a result manifest; "
        "a reduced local table only with --export; "
        "a private participant join only with --include-identifiers.",
        "wgsextract cohort summarize cohort.json --region chr22 --outdir summary",
    ),
}
