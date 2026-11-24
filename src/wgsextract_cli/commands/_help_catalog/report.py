"""Help entries for ``report``."""

from . import CommandHelp, _h

ENTRIES: dict[str, CommandHelp] = {
    "report": _h(
        "<sample>_personal_report.html, .md and .json and <sample>_personal_report_findings.tsv "
        "(or .parquet with --format parquet) in the sample folder (or --outdir); "
        "evidence links are relative to the sample folder.",
        "wgsextract report --genome HG002",
        "wgsextract report --input sample.cram",
        "wgsextract report --genome HG002 --gene-list acmg-sf-3.2",
        "wgsextract report --genome HG002 --gene-list my_genes.bed",
        "wgsextract report --workspace HG002 --results ~/wgs-out/HG002 --format parquet",
    ),
}
