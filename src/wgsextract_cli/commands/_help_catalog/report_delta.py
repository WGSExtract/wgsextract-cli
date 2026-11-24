"""Help entries for ``report-delta``."""

from . import CommandHelp, _h

ENTRIES: dict[str, CommandHelp] = {
    "report-delta": _h(
        "Prints what changed between the runs; writes no files. With --json, "
        "the result's details are the wgsextract.delta/1 document.",
        "wgsextract report-delta --before old/HG002/HG002_personal_report.manifest.json "
        "--after HG002/HG002_personal_report.manifest.json",
        "wgsextract --json report-delta --before old/clinvar_pathogenic.vcf.manifest.json "
        "--after clinvar_pathogenic.vcf.manifest.json",
    ),
}
